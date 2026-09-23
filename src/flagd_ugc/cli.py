from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .animation_lab import AnimationLab
from .assets import load_character_references, load_demos
from .batch_preview import render_batch_preview
from .config import Settings, default_font_file, load_local_env
from .db import Database
from .demo_bank import prepare_demo_bank
from .errors import PipelineError, QualityError
from .fal_provider import FalKlingProvider
from .hook_video_lab import HookVideoLab
from .hook_copy_lab import HookCopyLab
from .hook_formats import hook_format_keys
from .media import (
    cleanup_renders,
    MediaComposer,
    probe_video_dimensions,
    require_media_tools,
    validate_output,
)
from .pipeline import Pipeline
from .providers import GeminiProvider
from .reactions import get_reaction, reaction_keys
from .retry import retry_quality, retry_transient
from .scene_lab import SceneLab
from .scenarios import DEFAULT_SCENARIO, scenario_keys
from .scheduler import parse_run_time, run_daemon
from .visual_hooks import get_visual_hook, visual_hook_keys
from .wardrobes import wardrobe_keys

LOGGER = logging.getLogger(__name__)

CHARACTER_SAMPLE_DIRECTION = """
For this consistency sample, make the two arm roles visually unmistakable. Exactly one arm
holds the front-camera phone: keep that camera arm almost entirely outside the crop, with at
most one partial forearm entering from a single lower corner. Her other arm is the free arm:
its elbow is clearly bent beside her torso and its hand rests naturally near her upper chest
or low torso. The free arm must not extend toward the lens, run parallel to the camera arm,
touch the frame edge, or resemble a second camera-bearing arm. Do not show the phone.
""".strip()


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagd-ugc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run or resume one daily batch")
    run.add_argument("--date", type=_date)
    run.add_argument("--no-deliver", action="store_true")

    subparsers.add_parser("daemon", help="run every day on the London schedule")
    subparsers.add_parser("preflight", help="validate secrets, assets, and tools")
    subparsers.add_parser("healthcheck", help="check local runtime health")
    gemini_check = subparsers.add_parser(
        "gemini-check",
        help="check Gemini connectivity, key access, and model without generation",
    )
    gemini_check.add_argument("--model")
    fal_check = subparsers.add_parser(
        "fal-check",
        help="test several fresh fal TLS connections without paid inference",
    )
    fal_check.add_argument("--attempts", type=int, default=3)
    subparsers.add_parser("cleanup", help="apply configured retention windows")

    prepare_demos = subparsers.add_parser(
        "prepare-demos",
        help="import raw recordings and build the approved 12-second demo bank",
    )
    prepare_demos.add_argument("--plan", type=Path, required=True)
    prepare_demos.add_argument("--source-dir", type=Path, required=True)
    prepare_demos.add_argument(
        "--output-dir", type=Path, default=Path("data/demos")
    )

    compose_lab = subparsers.add_parser(
        "compose-lab",
        help="render one silent 15-second hook-and-demo prototype locally",
    )
    compose_lab.add_argument("--opening", type=Path, required=True)
    compose_lab.add_argument("--demo", type=Path, required=True)
    compose_lab.add_argument("--hook", required=True)
    compose_lab.add_argument(
        "--hook-format",
        choices=hook_format_keys(),
        default="direct-observation",
        help="render one approved caption layout (default: direct-observation)",
    )
    compose_lab.add_argument("--output", type=Path, required=True)
    compose_lab.add_argument("--font-file", type=Path)
    compose_lab.add_argument("--open", action="store_true")

    batch_preview = subparsers.add_parser(
        "batch-preview-lab",
        help="render a four-video silent local preview pack from a JSON plan",
    )
    batch_preview.add_argument("--plan", type=Path, required=True)
    batch_preview.add_argument(
        "--output-dir", type=Path, default=Path("batch-preview-output")
    )
    batch_preview.add_argument("--font-file", type=Path)
    batch_preview.add_argument("--open", action="store_true")

    hook_copy = subparsers.add_parser(
        "hook-copy-lab",
        help="generate and review one four-format Gemini text-only hook batch",
    )
    hook_copy.add_argument("--demos-dir", type=Path, default=Path("data/demos"))
    hook_copy.add_argument("--date", type=_date)
    hook_copy.add_argument(
        "--output-dir", type=Path, default=Path("hook-copy-output")
    )
    hook_copy.add_argument(
        "--dry-run",
        action="store_true",
        help="write the selected formats and examples without calling Gemini",
    )
    hook_copy.add_argument(
        "--confirm-paid",
        action="store_true",
        help="explicitly allow the two Gemini text calls",
    )
    hook_copy.add_argument("--open", action="store_true")

    smoke = subparsers.add_parser("smoke", help="generate one paid, undelivered video")
    smoke.add_argument("--confirm-paid", action="store_true")
    smoke.add_argument("--date", type=_date)

    samples = subparsers.add_parser(
        "character-samples", help="generate eight paid character consistency samples"
    )
    samples.add_argument("--confirm-paid", action="store_true")
    sample_scope = samples.add_mutually_exclusive_group()
    sample_scope.add_argument("--count", type=int, default=8)
    sample_scope.add_argument(
        "--replace",
        type=int,
        metavar="NUMBER",
        help="regenerate only one numbered sample and preserve the existing pack",
    )

    approve = subparsers.add_parser(
        "approve-samples", help="record manual approval of generated character samples"
    )
    approve.add_argument("--reviewer", required=True)

    scene_lab = subparsers.add_parser(
        "scene-lab",
        help="experiment with motion-ready first-scene stills from one master photo",
    )
    scene_lab.add_argument("--master", type=Path, required=True)
    scene_lab.add_argument(
        "--import-image",
        type=Path,
        action="append",
        default=[],
        help="add an existing candidate to the review report; repeat as needed",
    )
    scene_lab.add_argument("--samples", type=int, default=4)
    scene_lab.add_argument(
        "--scenario",
        choices=scenario_keys(),
        default=DEFAULT_SCENARIO,
        help="pose and setting to test",
    )
    scene_lab.add_argument(
        "--wardrobe",
        choices=wardrobe_keys(),
        help="override the scenario's ordinary default outfit",
    )
    scene_lab.add_argument("--model")
    scene_lab.add_argument("--image-size", choices=("1K", "2K", "4K"), default="2K")
    scene_lab.add_argument("--prompt-file", type=Path)
    scene_lab.add_argument("--motion-prompt-file", type=Path)
    scene_lab.add_argument("--output-dir", type=Path, default=Path("scene-lab-output"))
    scene_lab.add_argument(
        "--dry-run",
        action="store_true",
        help="write the experiment and import candidates without making API calls",
    )
    scene_lab.add_argument(
        "--confirm-paid",
        action="store_true",
        help="explicitly allow paid image-generation calls",
    )
    scene_lab.add_argument(
        "--open", action="store_true", help="open the generated HTML review report"
    )

    hook_video_lab = subparsers.add_parser(
        "hook-video-lab",
        help="generate one self-shot front-camera hook video with an internal scene frame",
    )
    hook_video_source = hook_video_lab.add_mutually_exclusive_group(required=True)
    hook_video_source.add_argument("--master", type=Path)
    hook_video_source.add_argument(
        "--resume",
        type=Path,
        help="continue an existing hook-video run from its saved stage",
    )
    hook_video_lab.add_argument(
        "--scenario",
        choices=scenario_keys(),
    )
    hook_video_lab.add_argument("--wardrobe", choices=wardrobe_keys())
    hook_video_lab.add_argument("--model")
    hook_video_lab.add_argument(
        "--image-size", choices=("1K", "2K", "4K"), default="2K"
    )
    hook_video_lab.add_argument(
        "--duration", type=int, choices=(3, 4), default=3
    )
    hook_video_lab.add_argument(
        "--output-dir", type=Path, default=Path("hook-video-output")
    )
    hook_video_lab.add_argument("--confirm-paid", action="store_true")
    hook_video_lab.add_argument("--open", action="store_true")

    animation_lab = subparsers.add_parser(
        "animation-lab",
        help="animate one accepted scene still through fal Kling and review it locally",
    )
    animation_source = animation_lab.add_mutually_exclusive_group(required=True)
    animation_source.add_argument(
        "--image",
        type=Path,
        help="accepted vertical scene-lab candidate to animate",
    )
    animation_source.add_argument(
        "--resume",
        type=Path,
        help="existing animation-lab run directory with a submitted request",
    )
    animation_lab.add_argument(
        "--motion-prompt-file",
        type=Path,
        help="defaults to MOTION_PROMPT.txt beside --image",
    )
    animation_lab.add_argument(
        "--reaction",
        choices=reaction_keys(),
        help="use a tuned high-arousal reaction or gesture preset",
    )
    animation_lab.add_argument(
        "--visual-hook",
        choices=visual_hook_keys(),
        help="use a scene-aware silent behavioral hook preset",
    )
    animation_lab.add_argument(
        "--duration", type=int, choices=range(3, 16), default=3
    )
    animation_lab.add_argument(
        "--kling-tier",
        choices=("standard", "pro"),
        help="override FAL_KLING_MODEL for a controlled Standard/Pro comparison",
    )
    animation_lab.add_argument(
        "--output-dir", type=Path, default=Path("animation-lab-output")
    )
    animation_lab.add_argument(
        "--confirm-paid",
        action="store_true",
        help="explicitly allow one new paid fal Kling request",
    )
    animation_lab.add_argument(
        "--open", action="store_true", help="open the generated HTML review report"
    )
    return parser


def _optional_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineError(f"could not read prompt file {path}: {exc}") from exc


def _compose_lab_font(configured: Path | None) -> Path:
    if configured is not None:
        font = configured.expanduser().resolve()
        if not font.is_file():
            raise PipelineError(f"font file is missing: {font}")
        return font
    font = default_font_file()
    if not font.is_file():
        raise PipelineError("no suitable bold system font found; pass --font-file")
    return font


def _preflight(
    settings: Settings, *, require_approval: bool = True
) -> dict[str, object]:
    settings.create_directories()
    missing = settings.missing_secrets()
    if missing:
        raise PipelineError("missing secrets: " + ", ".join(missing))
    require_media_tools()
    parse_run_time(settings.run_time)
    references = load_character_references(settings.characters_dir)
    demos = load_demos(settings.demos_dir)
    if not 8 <= len(demos) <= 12:
        raise PipelineError(f"expected 8–12 approved demos, found {len(demos)}")
    non_vertical = []
    for demo in demos:
        width, height = probe_video_dimensions(demo.path)
        if width >= height:
            non_vertical.append(f"{demo.path.name} ({width}x{height})")
    if non_vertical:
        raise PipelineError("demos must be vertical: " + ", ".join(non_vertical))
    if not settings.font_file.is_file():
        raise PipelineError(f"font is missing: {settings.font_file}")
    approval = settings.data_dir / "character-samples" / "APPROVED.json"
    if require_approval and not approval.is_file():
        raise PipelineError(
            "eight character samples have not been manually approved; run "
            "character-samples then approve-samples"
        )
    return {
        "character_references": len(references),
        "approved_demos": len(demos),
        "silent_outputs": True,
        "sample_approval": approval.is_file(),
    }


def _prepare_smoke_settings(settings: Settings) -> Settings:
    root = settings.data_dir / "smoke"
    root.mkdir(parents=True, exist_ok=True)
    for name, source in (
        ("characters", settings.characters_dir),
        ("demos", settings.demos_dir),
    ):
        target = root / name
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(source, target_is_directory=True)
    return replace(settings, data_dir=root, video_count=1)


def _character_samples(
    settings: Settings, count: int, *, replace: int | None = None
) -> Path:
    references = load_character_references(settings.characters_dir)
    demos = load_demos(settings.demos_dir)
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model=settings.gemini_image_model,
        timeout=settings.request_timeout_seconds,
    )
    root = settings.data_dir / "character-samples"
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "review.json"
    if replace is None:
        report: list[dict[str, object]] = []
        indexes = range(1, count + 1)
    else:
        if replace < 1:
            raise PipelineError("replacement sample number must be at least 1")
        if not report_path.is_file():
            raise PipelineError(
                "generate the character sample pack before replacing one sample"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PipelineError("existing character sample review is invalid") from exc
        if not isinstance(report, list) or replace > len(report):
            raise PipelineError(
                f"sample {replace} is not present in the existing review report"
            )
        indexes = (replace,)
        approval_path = root / "APPROVED.json"
        if approval_path.exists():
            approval_path.unlink()

    for index in indexes:
        output = root / f"sample-{index:02d}.png"
        demo = demos[(index - 1) % len(demos)]

        def generate_and_review() -> dict[str, object]:
            retry_transient(
                lambda: provider.generate_scene(
                    "I needed a clearer way to see this",
                    demo.description,
                    references,
                    output,
                    direction=CHARACTER_SAMPLE_DIRECTION,
                ),
                retries=settings.transient_retries,
            )
            return retry_transient(
                lambda: provider.review_visual(
                    [output], references, kind="character consistency sample"
                ),
                retries=settings.transient_retries,
            )

        try:
            review = retry_quality(
                generate_and_review, retries=settings.quality_retries
            )
            result = {"file": output.name, "review": review}
        except QualityError as exc:
            result = {"file": output.name, "error": str(exc)}
        if replace is None:
            report.append(result)
        else:
            report[index - 1] = result
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report_path


def _approve_samples(settings: Settings, reviewer: str) -> Path:
    root = settings.data_dir / "character-samples"
    report_path = root / "review.json"
    if not report_path.is_file():
        raise PipelineError("generate character samples before approving them")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if len(report) < 8 or any("error" in item for item in report[:8]):
        raise PipelineError(
            "eight samples must pass automated review before manual approval"
        )
    missing = [
        item["file"] for item in report[:8] if not (root / item["file"]).is_file()
    ]
    if missing:
        raise PipelineError("sample files are missing: " + ", ".join(missing))
    approval_path = root / "APPROVED.json"
    approval_path.write_text(
        json.dumps(
            {
                "reviewer": reviewer,
                "approved_on": datetime.now(ZoneInfo(settings.timezone))
                .date()
                .isoformat(),
                "samples": [item["file"] for item in report[:8]],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return approval_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "prepare-demos":
        try:
            prepared = prepare_demo_bank(args.plan, args.source_dir, args.output_dir)
            print(
                f"prepared {len(prepared)} approved demos at "
                f"{args.output_dir.expanduser().resolve()}"
            )
            return 0
        except (PipelineError, ValueError, OSError) as exc:
            LOGGER.error("%s", exc)
            return 2
    if args.command == "compose-lab":
        try:
            require_media_tools()
            output = args.output.expanduser().resolve()
            composer = MediaComposer(_compose_lab_font(args.font_file))
            composer.compose(
                args.opening.expanduser().resolve(),
                args.demo.expanduser().resolve(),
                None,
                args.hook,
                output,
                hook_format=args.hook_format,
            )
            validate_output(output, require_audio=False)
            print(f"silent prototype: {output}")
            if args.open:
                webbrowser.open(output.as_uri())
            return 0
        except (PipelineError, ValueError, OSError) as exc:
            LOGGER.error("%s", exc)
            return 2
    if args.command == "batch-preview-lab":
        try:
            require_media_tools()
            result = render_batch_preview(
                args.plan,
                args.output_dir,
                _compose_lab_font(args.font_file),
            )
            print(
                f"rendered {len(result.videos)} silent local previews at "
                f"{result.output_dir}"
            )
            print(f"review: {result.report_path}")
            if args.open:
                webbrowser.open(result.report_path.as_uri())
            return 0
        except (PipelineError, ValueError, OSError) as exc:
            LOGGER.error("%s", exc)
            return 2
    load_local_env()
    settings = Settings.from_env()
    try:
        if args.command == "healthcheck":
            settings.create_directories()
            require_media_tools()
            database = Database(settings.db_path)
            database.initialize()
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            print("ok")
            return 0
        if args.command == "preflight":
            print(json.dumps(_preflight(settings), indent=2))
            return 0
        if args.command == "hook-copy-lab":
            if args.dry_run and args.confirm_paid:
                raise PipelineError(
                    "choose either --dry-run or --confirm-paid, not both"
                )
            if not args.dry_run and not args.confirm_paid:
                raise PipelineError(
                    "hook-copy-lab makes two Gemini text calls; pass --confirm-paid "
                    "or use --dry-run"
                )
            if not args.dry_run and not settings.gemini_api_key:
                raise PipelineError("missing secret: GEMINI_API_KEY")
            run_date = args.date or datetime.now(ZoneInfo(settings.timezone)).date()
            provider = (
                None
                if args.dry_run
                else GeminiProvider(
                    settings.gemini_api_key,
                    text_model=settings.gemini_text_model,
                    image_model=settings.gemini_image_model,
                    timeout=settings.request_timeout_seconds,
                )
            )
            result = HookCopyLab(
                provider, quality_retries=settings.quality_retries
            ).run(
                args.demos_dir,
                run_date,
                args.output_dir,
                dry_run=args.dry_run,
            )
            print(f"hook copy ({result.status}): {result.report_path}")
            if args.dry_run:
                print("dry run: no Gemini calls were made")
            if args.open:
                webbrowser.open(result.report_path.as_uri())
            return 0
        if args.command == "gemini-check":
            if not settings.gemini_api_key:
                raise PipelineError("missing secret: GEMINI_API_KEY")
            model = args.model or settings.gemini_image_model
            provider = GeminiProvider(
                settings.gemini_api_key,
                text_model=settings.gemini_text_model,
                image_model=model,
                timeout=settings.request_timeout_seconds,
            )
            payload = retry_transient(
                provider.check_image_model,
                retries=settings.transient_retries,
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "model": payload.get("name", model),
                        "display_name": payload.get("displayName"),
                        "generation_methods": payload.get(
                            "supportedGenerationMethods", []
                        ),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "fal-check":
            provider = FalKlingProvider(
                settings.fal_api_key,
                model=settings.fal_kling_model,
                request_timeout=settings.request_timeout_seconds,
            )
            payload = provider.check_connection(args.attempts)
            print(json.dumps(payload, indent=2))
            print("no paid inference calls were made")
            return 0
        if args.command == "cleanup":
            count = cleanup_renders(
                settings.renders_dir,
                settings.final_retention_days,
                settings.intermediate_retention_days,
            )
            print(f"removed {count} expired files")
            return 0
        if args.command == "daemon":
            run_daemon(settings)
            return 0
        if args.command == "run":
            run_date = args.date or datetime.now(ZoneInfo(settings.timezone)).date()
            result = Pipeline(settings).run(run_date, deliver=not args.no_deliver)
            print(
                f"batch {result.run_date}: {result.status} "
                f"({result.completed}/{result.expected})"
            )
            return 0 if result.status == "complete" else 1
        if args.command == "smoke":
            if not args.confirm_paid:
                raise PipelineError("smoke makes paid API calls; pass --confirm-paid")
            smoke = _prepare_smoke_settings(settings)
            run_date = args.date or datetime.now(ZoneInfo(settings.timezone)).date()
            result = Pipeline(smoke).run(run_date, count=1, deliver=False)
            print(f"paid smoke test: {result.status} ({result.completed}/1)")
            return 0 if result.status == "complete" else 1
        if args.command == "character-samples":
            if not args.confirm_paid:
                raise PipelineError(
                    "character-samples makes paid API calls; pass --confirm-paid"
                )
            if args.replace is None and args.count < 8:
                raise PipelineError("generate at least eight samples")
            if not settings.gemini_api_key:
                raise PipelineError("missing secret: GEMINI_API_KEY")
            path = _character_samples(
                settings, args.count, replace=args.replace
            )
            print(f"inspect samples and automated review at {path}")
            return 0
        if args.command == "approve-samples":
            path = _approve_samples(settings, args.reviewer)
            print(f"recorded manual approval at {path}")
            return 0
        if args.command == "scene-lab":
            if args.dry_run and args.confirm_paid:
                raise PipelineError(
                    "choose either --dry-run or --confirm-paid, not both"
                )
            if not args.dry_run and args.samples > 0 and not args.confirm_paid:
                raise PipelineError(
                    "scene-lab generation makes paid API calls; pass --confirm-paid "
                    "or use --dry-run"
                )
            if not args.dry_run and args.samples > 0 and not settings.gemini_api_key:
                raise PipelineError("missing secret: GEMINI_API_KEY")
            model = args.model or settings.gemini_image_model
            provider = None
            if not args.dry_run and args.samples > 0:
                provider = GeminiProvider(
                    settings.gemini_api_key,
                    text_model=settings.gemini_text_model,
                    image_model=model,
                    timeout=settings.request_timeout_seconds,
                )
            result = SceneLab(
                provider, transient_retries=settings.transient_retries
            ).run(
                master=args.master,
                output_root=args.output_dir,
                samples=args.samples,
                model=model,
                scenario=args.scenario,
                wardrobe=args.wardrobe,
                prompt=_optional_text(args.prompt_file),
                motion_prompt=_optional_text(args.motion_prompt_file),
                imported_images=args.import_image,
                dry_run=args.dry_run,
                image_size=args.image_size,
            )
            print(f"scene lab: {result.report_path}")
            if args.dry_run:
                print("dry run: no paid API calls were made")
            else:
                print(
                    f"generated {result.generated_successfully}/{args.samples} "
                    "valid vertical samples"
                )
            if args.open:
                webbrowser.open(result.report_path.as_uri())
            return 0
        if args.command == "hook-video-lab":
            resume_needs_submission = False
            if args.resume is not None:
                resume_manifest_path = (
                    args.resume.expanduser().resolve() / "manifest.json"
                )
                if not resume_manifest_path.is_file():
                    raise PipelineError(
                        f"hook-video manifest is missing: {resume_manifest_path}"
                    )
                resume_manifest = json.loads(
                    resume_manifest_path.read_text(encoding="utf-8")
                )
                animation_relative = str(
                    resume_manifest.get("animation_run") or ""
                ).strip()
                resume_needs_submission = not (
                    animation_relative
                    and HookVideoLab._has_submitted_request(
                        args.resume.expanduser().resolve() / animation_relative
                    )
                )
                if resume_needs_submission and not args.confirm_paid:
                    raise PipelineError(
                        "this run stopped before Kling submission; pass --confirm-paid "
                        "to review its saved image and create one animation"
                    )
                if not resume_needs_submission and args.confirm_paid:
                    raise PipelineError(
                        "the animation request is already paid; omit --confirm-paid"
                    )
            if args.resume is None:
                if args.scenario is None:
                    raise PipelineError("new hook videos require --scenario")
                if not args.confirm_paid:
                    raise PipelineError(
                        "hook-video-lab makes one paid image and one paid animation "
                        "call; pass --confirm-paid"
                    )
                if not settings.gemini_api_key:
                    raise PipelineError("missing secret: GEMINI_API_KEY")
            elif resume_needs_submission and not settings.gemini_api_key:
                raise PipelineError("missing secret: GEMINI_API_KEY")
            if not settings.fal_api_key:
                raise PipelineError("missing secret: FAL_KEY")
            animation_provider = FalKlingProvider(
                settings.fal_api_key,
                model=settings.fal_kling_model,
                request_timeout=settings.request_timeout_seconds,
                poll_seconds=settings.fal_poll_seconds,
                initial_poll_delay=settings.fal_initial_poll_delay_seconds,
                job_timeout=settings.kling_timeout_seconds,
                transient_retries=settings.transient_retries,
            )
            if args.resume is not None:
                image_provider = None
                if resume_needs_submission:
                    animation_provider.check_queue_access()
                    image_provider = GeminiProvider(
                        settings.gemini_api_key,
                        text_model=settings.gemini_text_model,
                        image_model=(args.model or settings.gemini_image_model),
                        timeout=settings.request_timeout_seconds,
                    )
                result = HookVideoLab(
                    image_provider,
                    animation_provider,
                    transient_retries=settings.transient_retries,
                    quality_retries=settings.quality_retries,
                ).resume(args.resume)
            else:
                assert args.master is not None
                assert args.scenario is not None
                model = args.model or settings.gemini_image_model
                animation_provider.check_queue_access()
                image_provider = GeminiProvider(
                    settings.gemini_api_key,
                    text_model=settings.gemini_text_model,
                    image_model=model,
                    timeout=settings.request_timeout_seconds,
                )
                result = HookVideoLab(
                    image_provider,
                    animation_provider,
                    transient_retries=settings.transient_retries,
                    quality_retries=settings.quality_retries,
                ).run(
                    master=args.master,
                    output_root=args.output_dir,
                    scenario=args.scenario,
                    wardrobe=args.wardrobe,
                    model=model,
                    image_size=args.image_size,
                    duration=args.duration,
                )
            print(f"hook video ({result.status}): {result.report_path}")
            if args.open:
                webbrowser.open(result.report_path.as_uri())
            return 0
        if args.command == "animation-lab":
            if args.resume is not None:
                if args.confirm_paid:
                    raise PipelineError(
                        "--resume does not create a paid request; omit --confirm-paid"
                    )
                if args.motion_prompt_file is not None:
                    raise PipelineError(
                        "--resume uses its saved prompt; omit --motion-prompt-file"
                    )
                if args.reaction is not None:
                    raise PipelineError(
                        "--resume uses its saved prompt; omit --reaction"
                    )
                if args.visual_hook is not None:
                    raise PipelineError(
                        "--resume uses its saved prompt; omit --visual-hook"
                    )
                if args.kling_tier is not None:
                    raise PipelineError(
                        "--resume uses its saved fal request; omit --kling-tier"
                    )
            else:
                if not args.confirm_paid:
                    raise PipelineError(
                        "animation-lab creates one paid request (about $0.252 for "
                        "3 seconds); pass --confirm-paid"
                    )
                prompt_choices = sum(
                    value is not None
                    for value in (
                        args.reaction,
                        args.visual_hook,
                        args.motion_prompt_file,
                    )
                )
                if prompt_choices > 1:
                    raise PipelineError(
                        "choose only one of --reaction, --visual-hook or "
                        "--motion-prompt-file"
                    )
            if not settings.fal_api_key:
                raise PipelineError("missing secret: FAL_KEY")
            kling_model = settings.fal_kling_model
            if args.resume is None and args.kling_tier is not None:
                kling_model = (
                    "fal-ai/kling-video/v3/"
                    f"{args.kling_tier}/image-to-video"
                )
            provider = FalKlingProvider(
                settings.fal_api_key,
                model=kling_model,
                request_timeout=settings.request_timeout_seconds,
                poll_seconds=settings.fal_poll_seconds,
                initial_poll_delay=settings.fal_initial_poll_delay_seconds,
                job_timeout=settings.kling_timeout_seconds,
                transient_retries=settings.transient_retries,
            )
            if args.resume is None:
                provider.check_queue_access()
            lab = AnimationLab(provider)
            if args.resume is not None:
                result = lab.resume(args.resume)
            else:
                assert args.image is not None
                allow_hand_entry = False
                if args.reaction is not None:
                    reaction = get_reaction(args.reaction)
                    motion_prompt = reaction.motion_prompt()
                    motion_prompt_id = f"reaction:{args.reaction}:v1"
                    allow_hand_entry = reaction.allow_hand_entry
                elif args.visual_hook is not None:
                    visual_hook = get_visual_hook(args.visual_hook)
                    motion_prompt = visual_hook.motion_prompt()
                    motion_prompt_id = f"visual-hook:{args.visual_hook}:v1"
                    allow_hand_entry = visual_hook.allow_hand_entry
                else:
                    prompt_path = (
                        args.motion_prompt_file
                        if args.motion_prompt_file is not None
                        else args.image.expanduser().resolve().parent
                        / "MOTION_PROMPT.txt"
                    )
                    motion_prompt = _optional_text(prompt_path)
                    assert motion_prompt is not None
                    motion_prompt_id = None
                result = lab.run(
                    image=args.image,
                    motion_prompt=motion_prompt,
                    output_root=args.output_dir,
                    duration=args.duration,
                    motion_prompt_id=motion_prompt_id,
                    allow_hand_entry=allow_hand_entry,
                )
            print(f"animation lab ({result.status}): {result.report_path}")
            if args.open:
                webbrowser.open(result.report_path.as_uri())
            return 0
        raise AssertionError(args.command)
    except (PipelineError, ValueError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

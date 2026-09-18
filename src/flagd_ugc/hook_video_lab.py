from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .animation_lab import AnimationLab, AnimationLabResult
from .errors import AssetError, QualityError
from .media import require_media_tools
from .retry import retry_transient
from .scene_lab import SceneLab, estimated_image_cost
from .scenarios import get_scenario


@dataclass(frozen=True)
class HookVideoLabResult:
    run_dir: Path
    report_path: Path
    manifest_path: Path
    animation_run_dir: Path
    status: str


class HookVideoLab:
    """Generate an internal starting frame and return a reviewable hook video."""

    def __init__(
        self,
        image_provider: Any,
        animation_provider: Any,
        *,
        transient_retries: int = 3,
        quality_retries: int = 2,
    ):
        self.image_provider = image_provider
        self.animation_provider = animation_provider
        self.transient_retries = transient_retries
        self.quality_retries = quality_retries

    def run(
        self,
        *,
        master: Path,
        output_root: Path,
        scenario: str,
        model: str,
        image_size: str = "2K",
        duration: int = 3,
        wardrobe: str | None = None,
    ) -> HookVideoLabResult:
        require_media_tools()
        if duration not in (3, 4):
            raise ValueError("hook-video-lab duration must be 3 or 4 seconds")
        definition = get_scenario(scenario)
        now = datetime.now(UTC)
        run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{scenario}-video-{uuid4().hex[:6]}"
        run_dir = output_root.expanduser().resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest_path = run_dir / "manifest.json"
        image_cost = estimated_image_cost(model, image_size)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": now.isoformat(),
            "status": "generating-internal-scene",
            "scenario": scenario,
            "prompt_version": definition.prompt_version,
            "handheld_required": True,
            "self_shot_front_camera_required": True,
            "allow_hand_entry": definition.allow_hand_entry,
            "model": model,
            "image_size": image_size,
            "duration_seconds": duration,
            "estimated_image_cost_usd": image_cost,
            "estimated_max_image_cost_usd": (
                round(image_cost * (self.quality_retries + 1), 4)
                if image_cost is not None
                else None
            ),
            "estimated_animation_cost_usd": round(duration * 0.084, 3),
            "internal_scene_run": None,
            "internal_scene_attempts": [],
            "scene_review": None,
            "animation_run": None,
            "final_report": None,
            "error": None,
        }
        self._write_manifest(manifest_path, manifest)

        try:
            internal_image: Path | None = None
            quality_errors: list[str] = []
            for attempt in range(1, self.quality_retries + 2):
                scene_result = SceneLab(
                    self.image_provider,
                    transient_retries=self.transient_retries,
                ).run(
                    master=master,
                    output_root=run_dir / "internal-scenes",
                    samples=1,
                    model=model,
                    scenario=scenario,
                    wardrobe=wardrobe,
                    dry_run=False,
                    image_size=image_size,
                )
                attempt_record: dict[str, Any] = {
                    "attempt": attempt,
                    "run": str(scene_result.run_dir.relative_to(run_dir)),
                    "status": "technical-validation-failed",
                    "error": None,
                }
                manifest["internal_scene_attempts"].append(attempt_record)
                ready = next(
                    (
                        candidate
                        for candidate in scene_result.candidates
                        if candidate.status == "ready"
                    ),
                    None,
                )
                if ready is None:
                    error = "the generated frame failed vertical asset validation"
                    quality_errors.append(error)
                    attempt_record["error"] = error
                    self._write_manifest(manifest_path, manifest)
                    continue

                candidate_image = scene_result.run_dir / ready.file
                manifest["status"] = "reviewing-internal-scene"
                self._write_manifest(manifest_path, manifest)
                try:
                    review = retry_transient(
                        lambda: self.image_provider.review_visual(
                            [candidate_image],
                            [master],
                            kind="self-shot front-camera opening scene",
                        ),
                        retries=self.transient_retries,
                    )
                except QualityError as exc:
                    error = str(exc)
                    quality_errors.append(error)
                    attempt_record["status"] = "quality-review-failed"
                    attempt_record["error"] = error
                    self._write_manifest(manifest_path, manifest)
                    continue

                attempt_record["status"] = "accepted"
                attempt_record["review"] = review
                manifest["internal_scene_run"] = str(
                    scene_result.run_dir.relative_to(run_dir)
                )
                manifest["scene_review"] = review
                internal_image = candidate_image
                break

            if internal_image is None:
                details = "; ".join(quality_errors) or "unknown quality failure"
                raise QualityError(
                    "internal starting frame exhausted quality retries: " + details
                )
            manifest["status"] = "animating"
            self._write_manifest(manifest_path, manifest)

            motion_prompt = self._motion_prompt(definition, duration)
            animation_result: AnimationLabResult = AnimationLab(
                self.animation_provider
            ).run(
                image=internal_image,
                motion_prompt=motion_prompt,
                output_root=run_dir / "animations",
                duration=duration,
                motion_prompt_id=f"scenario:{scenario}:{definition.prompt_version}",
                allow_hand_entry=definition.allow_hand_entry,
            )
            manifest.update(
                {
                    "status": animation_result.status,
                    "animation_run": str(
                        animation_result.run_dir.relative_to(run_dir)
                    ),
                    "final_report": str(
                        animation_result.report_path.relative_to(run_dir)
                    ),
                    "error": None,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
            self._write_manifest(manifest_path, manifest)
            return HookVideoLabResult(
                run_dir=run_dir,
                report_path=animation_result.report_path,
                manifest_path=manifest_path,
                animation_run_dir=animation_result.run_dir,
                status=animation_result.status,
            )
        except Exception as exc:
            animation_dirs = sorted((run_dir / "animations").glob("*"))
            if animation_dirs:
                manifest["animation_run"] = str(
                    animation_dirs[-1].relative_to(run_dir)
                )
            manifest["status"] = "interrupted"
            manifest["error"] = str(exc)
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            self._write_manifest(manifest_path, manifest)
            raise

    def resume(self, run_dir: Path) -> HookVideoLabResult:
        """Resume the saved animation request without generating or submitting again."""
        require_media_tools()
        root = run_dir.expanduser().resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise AssetError(f"hook-video manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        try:
            animation_relative = str(manifest.get("animation_run") or "").strip()
            animation_run = root / animation_relative
            if animation_relative and self._has_submitted_request(animation_run):
                animation_result = AnimationLab(self.animation_provider).resume(
                    animation_run
                )
            else:
                animation_result = self._resume_before_animation(root, manifest)
            manifest.update(
                {
                    "status": animation_result.status,
                    "animation_run": str(
                        animation_result.run_dir.relative_to(root)
                    ),
                    "final_report": str(
                        animation_result.report_path.relative_to(root)
                    ),
                    "error": None,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
            self._write_manifest(manifest_path, manifest)
            return HookVideoLabResult(
                run_dir=root,
                report_path=animation_result.report_path,
                manifest_path=manifest_path,
                animation_run_dir=animation_result.run_dir,
                status=animation_result.status,
            )
        except Exception as exc:
            animation_dirs = sorted((root / "animations").glob("*"))
            if animation_dirs:
                manifest["animation_run"] = str(
                    animation_dirs[-1].relative_to(root)
                )
            manifest["status"] = "interrupted"
            manifest["error"] = str(exc)
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            self._write_manifest(manifest_path, manifest)
            raise

    def _resume_before_animation(
        self, root: Path, manifest: dict[str, Any]
    ) -> AnimationLabResult:
        if self.image_provider is None:
            raise AssetError(
                "this run stopped before animation and requires a Gemini reviewer"
            )
        attempts = manifest.get("internal_scene_attempts")
        if not isinstance(attempts, list) or not attempts:
            raise AssetError("this run has no generated scene candidate to review")
        attempt = attempts[-1]
        scene_run = root / str(attempt.get("run") or "")
        scene_manifest_path = scene_run / "manifest.json"
        if not scene_manifest_path.is_file():
            raise AssetError(f"internal scene manifest is missing: {scene_manifest_path}")
        scene_manifest = json.loads(scene_manifest_path.read_text(encoding="utf-8"))
        candidate_name = next(
            (
                str(item["file"])
                for item in scene_manifest.get("candidates", [])
                if item.get("status") == "ready"
            ),
            "",
        )
        candidate = scene_run / candidate_name
        master = scene_run / str(scene_manifest.get("master_file") or "")
        if not candidate_name or not candidate.is_file():
            raise AssetError("the saved scene candidate is missing")
        if not master.is_file():
            raise AssetError("the saved master reference is missing")

        review = manifest.get("scene_review")
        review_passed = isinstance(review, dict) and all(
            review.get(key) is True
            for key in ("passed", "consistent", "defect_free", "self_shot")
        )
        if not review_passed:
            manifest["status"] = "reviewing-internal-scene"
            self._write_manifest(root / "manifest.json", manifest)
            review = retry_transient(
                lambda: self.image_provider.review_visual(
                    [candidate],
                    [master],
                    kind="self-shot front-camera opening scene",
                ),
                retries=self.transient_retries,
            )
        assert isinstance(review, dict)
        attempt["status"] = "accepted"
        attempt["error"] = None
        attempt["review"] = review
        manifest["internal_scene_run"] = str(scene_run.relative_to(root))
        manifest["scene_review"] = review
        manifest["status"] = "animating"
        self._write_manifest(root / "manifest.json", manifest)

        scenario = str(manifest["scenario"])
        definition = get_scenario(scenario)
        duration = int(manifest["duration_seconds"])
        return AnimationLab(self.animation_provider).run(
            image=candidate,
            motion_prompt=self._motion_prompt(definition, duration),
            output_root=root / "animations",
            duration=duration,
            motion_prompt_id=f"scenario:{scenario}:{definition.prompt_version}",
            allow_hand_entry=definition.allow_hand_entry,
        )

    @staticmethod
    def _has_submitted_request(animation_run: Path) -> bool:
        manifest_path = animation_run / "manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return isinstance(manifest.get("request"), dict) and bool(
            manifest["request"].get("request_id")
        )

    @staticmethod
    def _motion_prompt(definition: Any, duration: int) -> str:
        motion_prompt = definition.motion_prompt()
        if duration == 4:
            motion_prompt += (
                "\n\nFOUR-SECOND OUTPUT\nComplete the described hook by 3.0 seconds, "
                "then hold the final pose naturally from 3.0–4.0 seconds with only "
                "breathing and the required subtle handheld phone drift. Do not add "
                "another gesture, reaction beat or camera move.\n"
            )
        return motion_prompt

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

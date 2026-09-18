from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any
from uuid import uuid4

from .errors import (
    AssetError,
    PipelineError,
    ProviderError,
    ProxyBlockError,
    QualityError,
    TransientError,
)
from .fal_provider import FalJob
from .media import extract_review_frames, require_media_tools, validate_vertical_asset


FAL_KLING_AUDIO_OFF_COST_PER_SECOND = 0.084


@dataclass(frozen=True)
class AnimationProbe:
    width: int
    height: int
    video_codec: str
    fps: float
    duration: float
    has_audio: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "fps": round(self.fps, 3),
            "duration": round(self.duration, 3),
            "has_audio": self.has_audio,
        }


@dataclass(frozen=True)
class AnimationLabResult:
    run_dir: Path
    report_path: Path
    manifest_path: Path
    status: str


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def probe_animation(path: Path) -> AnimationProbe:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise QualityError(f"ffprobe failed: {result.stderr[-2000:]}")
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        video = next(item for item in streams if item["codec_type"] == "video")
        numerator, denominator = video["avg_frame_rate"].split("/", maxsplit=1)
        return AnimationProbe(
            width=int(video["width"]),
            height=int(video["height"]),
            video_codec=str(video["codec_name"]),
            fps=float(numerator) / float(denominator),
            duration=float(payload["format"]["duration"]),
            has_audio=any(item.get("codec_type") == "audio" for item in streams),
        )
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise QualityError(f"could not inspect fal animation: {exc}") from exc


def validate_animation(path: Path, duration: int) -> AnimationProbe:
    probe = probe_animation(path)
    failures: list[str] = []
    if probe.height <= probe.width or abs((probe.width / probe.height) - (9 / 16)) > 0.025:
        failures.append(f"resolution is not vertical 9:16 ({probe.width}x{probe.height})")
    if probe.video_codec != "h264":
        failures.append(f"video codec is {probe.video_codec}, expected H.264")
    if abs(probe.duration - duration) > 0.4:
        failures.append(f"duration is {probe.duration:.3f}s, expected about {duration}s")
    if probe.has_audio:
        failures.append("audio exists even though generation requested audio off")
    if failures:
        raise QualityError("invalid fal animation: " + "; ".join(failures))
    return probe


class AnimationLab:
    def __init__(self, provider: Any):
        self.provider = provider

    def run(
        self,
        *,
        image: Path,
        motion_prompt: str,
        output_root: Path,
        duration: int = 3,
        motion_prompt_id: str | None = None,
        allow_hand_entry: bool = False,
    ) -> AnimationLabResult:
        require_media_tools()
        source = image.expanduser().resolve()
        if not source.is_file():
            raise AssetError(f"starting image is missing: {source}")
        validate_vertical_asset(source)
        prompt = motion_prompt.strip()
        if not prompt:
            raise ValueError("motion prompt must not be empty")
        if duration not in range(3, 16):
            raise ValueError("duration must be between 3 and 15 seconds")

        now = datetime.now(UTC)
        run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-kling-v3-{uuid4().hex[:6]}"
        run_dir = output_root.expanduser().resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        input_name = f"input{source.suffix.lower()}"
        shutil.copy2(source, run_dir / input_name)
        (run_dir / "MOTION_PROMPT.txt").write_text(prompt + "\n", encoding="utf-8")

        master_file: str | None = None
        master_candidates = sorted(source.parent.glob("master.*"))
        if master_candidates:
            master = master_candidates[0]
            master_file = f"master{master.suffix.lower()}"
            shutil.copy2(master, run_dir / master_file)

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": now.isoformat(),
            "status": "prepared",
            "provider": "fal.ai",
            "model": self.provider.model,
            "source_image": str(source),
            "input_file": input_name,
            "master_file": master_file,
            "motion_prompt_file": "MOTION_PROMPT.txt",
            "motion_prompt_id": motion_prompt_id,
            "allow_hand_entry": allow_hand_entry,
            "duration_seconds": duration,
            "generate_audio": False,
            "estimated_cost_usd": round(
                duration * FAL_KLING_AUDIO_OFF_COST_PER_SECOND, 3
            ),
            "request": None,
            "last_queue_status": None,
            "output_file": None,
            "review_frames": [],
            "review_fps": 4,
            "validation": None,
            "error": None,
        }
        manifest_path = run_dir / "manifest.json"
        _atomic_json(manifest_path, manifest)
        self._write_report(run_dir, manifest, prompt)

        try:
            job = self._submit_with_proxy_retries(
                run_dir / input_name,
                prompt,
                duration=duration,
                allow_hand_entry=allow_hand_entry,
            )
            manifest["request"] = job.as_dict()
            manifest["status"] = "submitted"
            _atomic_json(manifest_path, manifest)
            return self._finish(
                run_dir,
                manifest,
                prompt,
                new_submission=True,
            )
        except KeyboardInterrupt as exc:
            manifest["status"] = "interrupted"
            manifest["error"] = "local polling interrupted; resume the saved request"
            _atomic_json(manifest_path, manifest)
            self._write_report(run_dir, manifest, prompt)
            raise PipelineError(
                f"polling interrupted; request saved at {run_dir}; resume it without "
                "--confirm-paid"
            ) from exc
        except Exception as exc:
            manifest["error"] = str(exc)
            if not manifest.get("request"):
                manifest["status"] = "failed"
            elif isinstance(exc, QualityError):
                manifest["status"] = "quality-failed"
            elif isinstance(exc, TransientError):
                manifest["status"] = "interrupted"
            else:
                manifest["status"] = "generation-failed"
            _atomic_json(manifest_path, manifest)
            self._write_report(run_dir, manifest, prompt)
            raise PipelineError(
                f"{exc}; experiment saved at {run_dir}"
            ) from exc

    def _submit_with_proxy_retries(
        self,
        image: Path,
        prompt: str,
        *,
        duration: int,
        allow_hand_entry: bool,
    ) -> FalJob:
        retries = int(getattr(self.provider, "transient_retries", 0))
        sleep = getattr(self.provider, "sleep", lambda _: None)
        for attempt in range(retries + 1):
            try:
                return self.provider.submit(
                    image,
                    prompt,
                    duration=duration,
                    allow_hand_entry=allow_hand_entry,
                )
            except ProxyBlockError:
                if attempt >= retries:
                    raise
                sleep(min(5.0 * (2**attempt), 30.0))
        raise AssertionError("unreachable")

    def resume(self, run_dir: Path) -> AnimationLabResult:
        require_media_tools()
        root = run_dir.expanduser().resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise AssetError(f"animation manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prompt_path = root / str(manifest.get("motion_prompt_file", "MOTION_PROMPT.txt"))
        if not prompt_path.is_file():
            raise AssetError(f"motion prompt is missing: {prompt_path}")
        prompt = prompt_path.read_text(encoding="utf-8").strip()
        if manifest.get("status") == "complete":
            output = root / str(manifest.get("output_file", ""))
            if output.is_file():
                self._refresh_review_frames(root, manifest, output)
                self._write_report(root, manifest, prompt)
                return self._result(root, manifest)
        if not manifest.get("request"):
            raise ProviderError(
                "this run has no submitted request to resume; start a new paid run"
            )
        manifest["status"] = "submitted"
        manifest["error"] = None
        _atomic_json(manifest_path, manifest)
        try:
            return self._finish(root, manifest, prompt)
        except KeyboardInterrupt as exc:
            manifest["status"] = "interrupted"
            manifest["error"] = "local polling interrupted; resume the saved request"
            _atomic_json(manifest_path, manifest)
            self._write_report(root, manifest, prompt)
            raise PipelineError(
                f"polling interrupted; request remains saved at {root}; resume it "
                "without --confirm-paid"
            ) from exc
        except Exception as exc:
            if isinstance(exc, QualityError):
                manifest["status"] = "quality-failed"
            elif isinstance(exc, TransientError):
                manifest["status"] = "interrupted"
            else:
                manifest["status"] = "generation-failed"
            manifest["error"] = str(exc)
            _atomic_json(manifest_path, manifest)
            self._write_report(root, manifest, prompt)
            raise

    def _finish(
        self,
        run_dir: Path,
        manifest: dict[str, Any],
        prompt: str,
        *,
        new_submission: bool = False,
    ) -> AnimationLabResult:
        manifest_path = run_dir / "manifest.json"
        job = FalJob.from_dict(manifest["request"])

        def checkpoint(payload: dict[str, Any]) -> None:
            manifest["last_queue_status"] = payload.get("status")
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            _atomic_json(manifest_path, manifest)

        output = run_dir / "opening.mp4"
        provider_result = self.provider.wait_and_download(
            job,
            output,
            on_status=checkpoint,
            new_submission=new_submission,
            prefer_result_endpoint=True,
        )
        manifest["output_file"] = output.name
        manifest["provider_result"] = provider_result
        _atomic_json(manifest_path, manifest)
        probe = validate_animation(output, int(manifest["duration_seconds"]))
        frames = self._refresh_review_frames(run_dir, manifest, output)
        manifest.update(
            {
                "status": "complete",
                "completed_at": datetime.now(UTC).isoformat(),
                "last_queue_status": "COMPLETED",
                "output_file": output.name,
                "validation": probe.as_dict(),
                "error": None,
            }
        )
        _atomic_json(manifest_path, manifest)
        self._write_report(run_dir, manifest, prompt)
        return self._result(run_dir, manifest)

    @staticmethod
    def _refresh_review_frames(
        run_dir: Path, manifest: dict[str, Any], output: Path
    ) -> list[Path]:
        review_fps = 4
        count = int(manifest["duration_seconds"]) * review_fps
        frames = extract_review_frames(
            output, run_dir / "review-frames", count=count, fps=review_fps
        )
        manifest["review_fps"] = review_fps
        manifest["review_frames"] = [
            str(path.relative_to(run_dir)) for path in frames
        ]
        _atomic_json(run_dir / "manifest.json", manifest)
        return frames

    @staticmethod
    def _result(run_dir: Path, manifest: dict[str, Any]) -> AnimationLabResult:
        return AnimationLabResult(
            run_dir=run_dir,
            report_path=run_dir / "report.html",
            manifest_path=run_dir / "manifest.json",
            status=str(manifest["status"]),
        )

    @staticmethod
    def _write_report(
        run_dir: Path, manifest: dict[str, Any], prompt: str
    ) -> None:
        (run_dir / "report.html").write_text(
            _render_report(manifest, prompt), encoding="utf-8"
        )


def _render_report(manifest: dict[str, Any], prompt: str) -> str:
    output = manifest.get("output_file")
    video = (
        f'<video src="{html.escape(str(output))}" controls loop muted playsinline></video>'
        if output
        else '<div class="waiting">No downloaded video yet. Resume this run after an interruption.</div>'
    )
    frames = "".join(
        f'<img src="{html.escape(str(path))}" alt="Review frame">'
        for path in manifest.get("review_frames", [])
    )
    validation = html.escape(
        json.dumps(manifest.get("validation"), indent=2)
        if manifest.get("validation")
        else "Not available yet"
    )
    error = (
        f'<p class="error">{html.escape(str(manifest["error"]))}</p>'
        if manifest.get("error")
        else ""
    )
    master = (
        f'<figure><img src="{html.escape(str(manifest["master_file"]))}" alt="Master identity"><figcaption>Master identity</figcaption></figure>'
        if manifest.get("master_file")
        else ""
    )
    template = Template(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>flagd animation lab · $run_id</title><style>
:root{color-scheme:dark;--bg:#0e1013;--panel:#171a1f;--line:#303640;--text:#f2f4f7;--muted:#a8b0bd;--green:#62d29f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,sans-serif}header,main{width:min(1280px,94vw);margin:auto}header{padding:30px 0 18px}p,figcaption{color:var(--muted)}.layout{display:grid;grid-template-columns:minmax(260px,420px) 1fr;gap:22px}.panel,figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}img,video{display:block;width:100%;border-radius:9px;background:#08090b}figure img{aspect-ratio:9/16;object-fit:cover}video{aspect-ratio:9/16;object-fit:contain;max-height:76vh}.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:22px 0}.frames img{aspect-ratio:9/16;object-fit:cover}.gates{display:grid;gap:9px;margin:15px 0}pre{white-space:pre-wrap;background:#0f1115;padding:14px;border-radius:8px;overflow:auto}.status{color:var(--green)}.error{color:#ff9696}.waiting{padding:50px 20px;color:var(--muted);border:1px dashed var(--line);border-radius:10px}@media(max-width:760px){.layout{grid-template-columns:1fr}}
</style></head><body><header><small>FLAGD · ANIMATION LAB</small><h1>Kling three-second motion test</h1><p>Status: <span class="status">$status</span> · Estimated generation cost: $$$cost · Audio off</p>$error</header><main><section class="layout"><div>$video</div><div><div class="layout">$master<figure><img src="$input_file" alt="Starting frame"><figcaption>Starting frame</figcaption></figure></div><section class="panel"><h2>Manual hard gates</h2><div class="gates"><label><input type="checkbox"> Same identity in every frame</label><label><input type="checkbox"> Clearly her own front-camera selfie throughout</label><label><input type="checkbox"> Natural movement coupled to her camera arm</label><label><input type="checkbox"> Intended reaction completes within three seconds</label><label><input type="checkbox"> Face, hair, hands and body do not morph</label><label><input type="checkbox"> Clothing and background remain stable</label><label><input type="checkbox"> No cut, camera jump, text or logo</label></div><details><summary>Motion prompt</summary><pre>$prompt</pre></details><details><summary>Codec validation</summary><pre>$validation</pre></details></section></div></section><h2>One frame per second</h2><section class="frames">$frames</section></main></body></html>"""
    )
    return template.substitute(
        run_id=html.escape(str(manifest["run_id"])),
        status=html.escape(str(manifest["status"])),
        cost=f"{float(manifest['estimated_cost_usd']):.3f}",
        error=error,
        video=video,
        master=master,
        input_file=html.escape(str(manifest["input_file"])),
        prompt=html.escape(prompt),
        validation=validation,
        frames=frames or '<p class="waiting">Frames will appear after completion.</p>',
    )

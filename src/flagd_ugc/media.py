from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import AssetError, QualityError
from .hook_formats import caption_plan


@dataclass(frozen=True)
class MediaSpec:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    intro_seconds: float = 3.0
    total_seconds: float = 15.0


@dataclass(frozen=True)
class ProbeResult:
    width: int
    height: int
    video_codec: str
    fps: float
    audio_codec: str | None
    duration: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "fps": self.fps,
            "audio_codec": self.audio_codec,
            "duration": self.duration,
        }


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class MediaComposer:
    def __init__(self, font_file: Path, spec: MediaSpec | None = None):
        self.font_file = font_file
        self.spec = spec or MediaSpec()

    def compose(
        self,
        opening: Path,
        demo: Path,
        music: Path | None,
        hook: str,
        output: Path,
        *,
        hook_format: str = "direct-observation",
    ) -> None:
        sources = (opening, demo) if music is None else (opening, demo, music)
        for source in sources:
            if not source.is_file():
                raise AssetError(f"media source is missing: {source}")
        if not self.font_file.is_file():
            raise AssetError(f"font file is missing: {self.font_file}")
        output.parent.mkdir(parents=True, exist_ok=True)
        caption_files: list[Path] = []
        caption_lines = caption_plan(hook, hook_format).lines
        for index, line in enumerate(caption_lines, start=1):
            caption_file = output.parent / f".{output.stem}-caption-{index}.txt"
            wrapped = "\n".join(
                textwrap.wrap(
                    line.text,
                    width=line.wrap_width,
                    break_long_words=False,
                )
            )
            caption_file.write_text(wrapped, encoding="utf-8")
            caption_files.append(caption_file)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        temporary.unlink(missing_ok=True)
        spec = self.spec
        demo_seconds = spec.total_seconds - spec.intro_seconds
        if demo_seconds <= 0:
            raise ValueError("intro duration must be shorter than total duration")
        fade_out_start = max(0.0, spec.total_seconds - 0.5)
        # The opening is styled as an ordinary short-form caption, not a title card:
        # centred in the top safe area, lowercase copy, and enough outline to remain
        # readable over a real handheld scene without adding an ad-like banner.
        font_size = max(6, round(spec.width * 0.0704))
        line_spacing = max(2, round(spec.width * 0.0093))
        outline = max(1, round(spec.width * 0.00463))
        shadow = max(1, round(spec.width * 0.00185))
        video_normalize = (
            f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
            f"crop={spec.width}:{spec.height},fps={spec.fps},setsar=1"
        )
        drawtexts: list[str] = []
        for line, caption_file in zip(caption_lines, caption_files, strict=True):
            line_font_size = max(6, round(font_size * line.font_scale))
            line_spacing_size = max(2, round(line_spacing * line.font_scale))
            line_outline = max(1, round(outline * min(1.0, line.font_scale)))
            line_shadow = max(1, round(shadow * min(1.0, line.font_scale)))
            line_top = max(4, round(spec.height * line.top_ratio))
            drawtexts.append(
                f"drawtext=fontfile='{_filter_path(self.font_file)}':"
                f"textfile='{_filter_path(caption_file)}':reload=0:fontcolor=white:"
                f"fontsize={line_font_size}:line_spacing={line_spacing_size}:"
                f"borderw={line_outline}:bordercolor=black@0.82:"
                f"shadowx={line_shadow}:shadowy={line_shadow}:shadowcolor=black@0.60:"
                f"expansion=none:x=(w-text_w)/2:y={line_top}"
            )
        drawtext = ",".join(drawtexts)
        filters = (
            f"[0:v]{video_normalize},trim=duration={spec.intro_seconds},"
            f"setpts=PTS-STARTPTS,{drawtext}[intro];"
            f"[1:v]{video_normalize},trim=duration={demo_seconds},"
            f"setpts=PTS-STARTPTS[demo];"
            f"[intro][demo]concat=n=2:v=1:a=0[v]"
        )
        if music is not None:
            filters += (
                ";"
                f"[2:a]atrim=duration={spec.total_seconds},asetpts=PTS-STARTPTS,"
                "loudnorm=I=-18:LRA=11:TP=-1.5,"
                f"afade=t=in:st=0:d=0.25,afade=t=out:st={fade_out_start}:d=0.5[a]"
            )
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(opening),
            "-stream_loop",
            "-1",
            "-i",
            str(demo),
        ]
        if music is not None:
            command.extend(["-stream_loop", "-1", "-i", str(music)])
        command.extend(
            [
                "-filter_complex",
                filters,
                "-map",
                "[v]",
            ]
        )
        if music is not None:
            command.extend(["-map", "[a]", "-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        command.extend(
            [
                "-t",
                str(spec.total_seconds),
                "-r",
                str(spec.fps),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(temporary),
            ]
        )
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                raise QualityError(
                    f"ffmpeg composition failed: {result.stderr[-3000:]}"
                )
            os.replace(temporary, output)
        finally:
            for caption_file in caption_files:
                caption_file.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)


def probe_media(path: Path) -> ProbeResult:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise QualityError(f"ffprobe failed: {result.stderr[-2000:]}")
    try:
        payload = json.loads(result.stdout)
        video = next(
            item for item in payload["streams"] if item["codec_type"] == "video"
        )
        audio = next(
            (
                item
                for item in payload["streams"]
                if item["codec_type"] == "audio"
            ),
            None,
        )
        numerator, denominator = video["avg_frame_rate"].split("/", maxsplit=1)
        fps = float(numerator) / float(denominator)
        duration = float(payload["format"]["duration"])
        return ProbeResult(
            width=int(video["width"]),
            height=int(video["height"]),
            video_codec=str(video["codec_name"]),
            fps=fps,
            audio_codec=str(audio["codec_name"]) if audio is not None else None,
            duration=duration,
        )
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise QualityError(
            f"could not read required streams from {path}: {exc}"
        ) from exc


def validate_output(
    path: Path, spec: MediaSpec | None = None, *, require_audio: bool = True
) -> ProbeResult:
    expected = spec or MediaSpec()
    result = probe_media(path)
    failures: list[str] = []
    if (result.width, result.height) != (expected.width, expected.height):
        failures.append(f"resolution {result.width}x{result.height}")
    if result.video_codec != "h264":
        failures.append(f"video codec {result.video_codec}")
    if abs(result.fps - expected.fps) > 0.05:
        failures.append(f"frame rate {result.fps:.3f}")
    if require_audio and result.audio_codec != "aac":
        failures.append(f"audio codec {result.audio_codec}")
    if not require_audio and result.audio_codec is not None:
        failures.append(f"unexpected audio codec {result.audio_codec}")
    if abs(result.duration - expected.total_seconds) > 0.25:
        failures.append(f"duration {result.duration:.3f}s")
    if failures:
        raise QualityError("invalid final output: " + ", ".join(failures))
    return result


def extract_review_frames(
    video: Path, output_dir: Path, count: int = 3, *, fps: int = 1
) -> list[Path]:
    if fps < 1:
        raise ValueError("review frame rate must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "frame-%02d.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps={fps}",
        "-frames:v",
        str(count),
        str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    frames = sorted(output_dir.glob("frame-*.jpg"))
    if result.returncode != 0 or len(frames) != count:
        raise QualityError(
            f"could not extract animation review frames: {result.stderr[-2000:]}"
        )
    return frames


def cleanup_renders(
    root: Path, final_days: int = 30, intermediate_days: int = 7
) -> int:
    if not root.exists():
        return 0
    now = datetime.now(UTC)
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        retention = final_days if "final" in path.parts else intermediate_days
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if now - modified > timedelta(days=retention):
            path.unlink()
            removed += 1
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def require_media_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise AssetError("missing required media tools: " + ", ".join(missing))


def probe_video_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        width, height = result.stdout.strip().split("x", maxsplit=1)
        return int(width), int(height)
    except ValueError as exc:
        raise AssetError(f"demo has no readable video stream: {path}") from exc


def validate_vertical_asset(path: Path, tolerance: float = 0.025) -> tuple[int, int]:
    try:
        width, height = probe_video_dimensions(path)
    except AssetError as exc:
        raise QualityError(f"generated asset is not readable: {path}") from exc
    expected = 9 / 16
    actual = width / height
    if abs(actual - expected) > tolerance:
        raise QualityError(
            f"generated asset is not 9:16: {width}x{height} (ratio {actual:.3f})"
        )
    return width, height

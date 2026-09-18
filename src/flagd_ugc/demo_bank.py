from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AssetError, QualityError


@dataclass(frozen=True)
class DemoTarget:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration: float = 12.0


@dataclass(frozen=True)
class DemoSegment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class PreparedDemo:
    identifier: str
    path: Path
    description: str
    category: str


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"invalid demo preparation plan {path}: {exc}") from exc


def _positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AssetError(f"{label} must be a number") from exc
    if number <= 0:
        raise AssetError(f"{label} must be positive")
    return number


def _target(payload: dict[str, Any]) -> DemoTarget:
    configured = payload.get("target", {})
    if not isinstance(configured, dict):
        raise AssetError("demo preparation target must be an object")
    return DemoTarget(
        width=int(_positive_number(configured.get("width", 1080), "target width")),
        height=int(_positive_number(configured.get("height", 1920), "target height")),
        fps=int(_positive_number(configured.get("fps", 30), "target fps")),
        duration=_positive_number(configured.get("duration", 12), "target duration"),
    )


def _source_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise AssetError(f"raw demo has no readable duration: {path}") from exc
    if result.returncode != 0 or duration <= 0:
        raise AssetError(f"raw demo is not readable: {path}")
    return duration


def _segments(item: dict[str, Any], source_duration: float) -> list[DemoSegment]:
    configured = item.get("segments")
    if not isinstance(configured, list) or not configured:
        raise AssetError(f"demo {item.get('id')!r} needs at least one segment")
    segments: list[DemoSegment] = []
    for index, value in enumerate(configured, 1):
        if not isinstance(value, dict):
            raise AssetError(f"segment {index} must be an object")
        try:
            start = float(value["start"])
            end = float(value["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssetError(f"segment {index} needs numeric start and end") from exc
        if start < 0 or end <= start:
            raise AssetError(f"segment {index} has an invalid time range")
        if end > source_duration + 0.1:
            raise AssetError(
                f"segment {index} ends at {end:.3f}s but source is "
                f"{source_duration:.3f}s"
            )
        segments.append(DemoSegment(start, min(end, source_duration)))
    return segments


def _filter(segments: list[DemoSegment], target: DemoTarget) -> str:
    pieces: list[str] = []
    labels: list[str] = []
    if len(segments) > 1:
        source_labels = "".join(f"[source{index}]" for index in range(len(segments)))
        pieces.append(f"[0:v]split={len(segments)}{source_labels}")
    for index, segment in enumerate(segments):
        source = "[0:v]" if len(segments) == 1 else f"[source{index}]"
        label = f"[segment{index}]"
        pieces.append(
            f"{source}trim=start={segment.start:.6f}:end={segment.end:.6f},"
            f"setpts=PTS-STARTPTS{label}"
        )
        labels.append(label)
    if len(labels) == 1:
        joined = labels[0]
    else:
        pieces.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[joined]")
        joined = "[joined]"
    normalize = (
        f"{joined}scale={target.width}:{target.height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={target.width}:{target.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={target.fps},setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={target.duration + 1:.6f},"
        f"trim=end_frame={round(target.duration * target.fps)},format=yuv420p[v]"
    )
    pieces.append(normalize)
    return ";".join(pieces)


def _probe_prepared(path: Path) -> dict[str, Any]:
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
        raise QualityError(f"could not inspect prepared demo {path}")
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        numerator, denominator = video["avg_frame_rate"].split("/", 1)
        return {
            "width": int(video["width"]),
            "height": int(video["height"]),
            "codec": str(video["codec_name"]),
            "fps": float(numerator) / float(denominator),
            "duration": float(payload["format"]["duration"]),
            "has_audio": any(
                stream.get("codec_type") == "audio" for stream in streams
            ),
        }
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise QualityError(f"could not parse prepared demo metadata: {path}") from exc


def validate_prepared_demo(path: Path, target: DemoTarget) -> dict[str, Any]:
    probe = _probe_prepared(path)
    failures: list[str] = []
    if (probe["width"], probe["height"]) != (target.width, target.height):
        failures.append(f"resolution {probe['width']}x{probe['height']}")
    if probe["codec"] != "h264":
        failures.append(f"codec {probe['codec']}")
    if abs(probe["fps"] - target.fps) > 0.05:
        failures.append(f"frame rate {probe['fps']:.3f}")
    if abs(probe["duration"] - target.duration) > 0.1:
        failures.append(f"duration {probe['duration']:.3f}s")
    if probe["has_audio"]:
        failures.append("unexpected audio stream")
    if failures:
        raise QualityError(
            f"invalid prepared demo {path.name}: " + ", ".join(failures)
        )
    return probe


def _render(source: Path, output: Path, segments: list[DemoSegment], target: DemoTarget) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        _filter(segments, target),
        "-map",
        "[v]",
        "-an",
        "-r",
        str(target.fps),
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
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise QualityError(
                f"could not prepare {output.name}: {result.stderr[-3000:]}"
            )
        validate_prepared_demo(temporary, target)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_demo_bank(
    plan_path: Path,
    source_dir: Path,
    output_dir: Path,
) -> list[PreparedDemo]:
    plan_path = plan_path.expanduser().resolve()
    source_dir = source_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    payload = _read_json(plan_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("demos"), list):
        raise AssetError("demo preparation plan needs a demos array")
    target = _target(payload)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[PreparedDemo] = []
    seen_outputs: set[str] = set()
    for item in payload["demos"]:
        if not isinstance(item, dict) or item.get("approved", True) is not True:
            continue
        identifier = str(item.get("id", "")).strip()
        source_name = str(item.get("source", "")).strip()
        output_name = str(item.get("output", "")).strip()
        description = str(item.get("description", "")).strip()
        category = str(item.get("category", "general")).strip() or "general"
        if not identifier or not source_name or not output_name or not description:
            raise AssetError(
                "each approved demo needs id, source, output and description"
            )
        if Path(source_name).name != source_name or Path(output_name).name != output_name:
            raise AssetError(f"demo {identifier!r} filenames must not contain directories")
        if not output_name.lower().endswith(".mp4"):
            raise AssetError(f"demo {identifier!r} output must be an MP4")
        if output_name in seen_outputs:
            raise AssetError(f"duplicate demo output: {output_name}")
        seen_outputs.add(output_name)
        original = source_dir / source_name
        if not original.is_file():
            raise AssetError(f"raw demo source is missing: {original}")
        imported = raw_dir / source_name
        if original.resolve() != imported.resolve():
            shutil.copy2(original, imported)
        duration = _source_duration(imported)
        selected = _segments(item, duration)
        output = output_dir / output_name
        _render(imported, output, selected, target)
        prepared.append(PreparedDemo(identifier, output, description, category))
    if not prepared:
        raise AssetError("demo preparation plan contains no approved demos")
    manifest = {
        "version": 1,
        "format": {
            "width": target.width,
            "height": target.height,
            "fps": target.fps,
            "duration_seconds": target.duration,
            "video_codec": "h264",
            "audio": False,
        },
        "demos": [
            {
                "id": demo.identifier,
                "file": demo.path.name,
                "description": demo.description,
                "category": demo.category,
                "approved": True,
            }
            for demo in prepared
        ],
    }
    manifest_path = output_dir / "manifest.json"
    temporary_manifest = output_dir / ".manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_manifest, manifest_path)
    return prepared

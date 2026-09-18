from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .db import Database
from .errors import AssetError

MUSIC_EXTENSIONS = {".mp3", ".m4a", ".wav", ".mp4"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class Demo:
    path: Path
    description: str


def _files(directory: Path, extensions: set[str]) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path.resolve()
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in extensions
        and not path.name.startswith(".")
    )


def load_character_references(directory: Path) -> list[Path]:
    references = _files(directory, IMAGE_EXTENSIONS)
    if not references:
        raise AssetError(f"no character reference images found in {directory}")
    return references


def load_demos(directory: Path) -> list[Demo]:
    manifest_path = directory / "manifest.json"
    available = {path.name: path for path in _files(directory, VIDEO_EXTENSIONS)}
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = payload["demos"] if isinstance(payload, dict) else payload
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AssetError(f"invalid demo manifest {manifest_path}: {exc}") from exc
        demos: list[Demo] = []
        for item in entries:
            if not isinstance(item, dict) or item.get("approved", True) is not True:
                continue
            filename = str(item.get("file", ""))
            if filename not in available:
                raise AssetError(
                    f"approved demo {filename!r} is missing from {directory}"
                )
            description = str(item.get("description", "")).strip()
            if not description:
                raise AssetError(f"approved demo {filename!r} needs a description")
            demos.append(Demo(available[filename], description))
    else:
        demos = [
            Demo(path, path.stem.replace("_", " ").replace("-", " "))
            for path in available.values()
        ]
    if not demos:
        raise AssetError(f"no approved demonstration videos found in {directory}")
    return demos


def has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "audio"


def list_music(directory: Path, *, verify_audio: bool = True) -> list[Path]:
    candidates = _files(directory, MUSIC_EXTENSIONS)
    if verify_audio:
        candidates = [path for path in candidates if has_audio_stream(path)]
    return candidates


def select_music(
    directory: Path,
    database: Database,
    run_date: date,
    *,
    rng: random.Random | random.SystemRandom | None = None,
    verify_audio: bool = True,
) -> Path:
    candidates = list_music(directory, verify_audio=verify_audio)
    if not candidates:
        raise AssetError(
            f"music bank is empty or contains no playable audio: {directory}"
        )
    recent = database.recent_music(run_date, days=14)
    eligible = [path for path in candidates if str(path.resolve()) not in recent]
    if not eligible:
        raise AssetError(
            "no music is eligible: every playable track was used in the last 14 days"
        )
    chooser = rng or random.SystemRandom()
    return chooser.choice(eligible)


def choose_demos(demos: list[Demo], count: int) -> list[Demo]:
    if len(demos) < count:
        raise AssetError(f"need at least {count} approved demos, found {len(demos)}")
    return random.SystemRandom().sample(demos, count)

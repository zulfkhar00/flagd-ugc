import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from flagd_ugc.errors import QualityError
from flagd_ugc.media import (
    MediaComposer,
    MediaSpec,
    validate_output,
    validate_vertical_asset,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)


def _run(command):
    subprocess.run(command, check=True, capture_output=True)


def _font() -> Path:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    return next(path for path in candidates if path.is_file())


def _sources(tmp_path, music_duration):
    opening = tmp_path / "opening.mp4"
    demo = tmp_path / "demo.mp4"
    music = tmp_path / "music.wav"
    _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=72x128:r=10:d=0.2",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(opening),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=100x100:r=10:d=0.25",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(demo),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={music_duration}",
            "-c:a",
            "pcm_s16le",
            str(music),
        ]
    )
    return opening, demo, music


@pytest.mark.parametrize("music_duration", [0.2, 3.0])
def test_composer_loops_short_media_and_crops_long_music(tmp_path, music_duration):
    opening, demo, music = _sources(tmp_path, music_duration)
    output = tmp_path / "final.mp4"
    spec = MediaSpec(width=90, height=160, fps=10, intro_seconds=0.3, total_seconds=1.5)

    MediaComposer(_font(), spec).compose(
        opening, demo, music, "I can share my plans", output
    )

    result = validate_output(output, spec)
    assert result.duration == pytest.approx(1.5, abs=0.25)
    assert result.audio_codec == "aac"
    assert result.video_codec == "h264"


def test_composer_can_write_a_silent_prototype(tmp_path):
    opening, demo, _music = _sources(tmp_path, music_duration=0.2)
    output = tmp_path / "silent.mp4"
    spec = MediaSpec(width=90, height=160, fps=10, intro_seconds=0.3, total_seconds=1.5)

    MediaComposer(_font(), spec).compose(
        opening, demo, None, "pov: this worked", output
    )

    result = validate_output(output, spec, require_audio=False)
    assert result.audio_codec is None


@pytest.mark.parametrize(
    ("hook", "hook_format"),
    (
        ("i'm good at texting", "quote-receipt"),
        ("rating the chat: 43/100", "scorecard"),
    ),
)
def test_composer_renders_structured_caption_formats(tmp_path, hook, hook_format):
    opening, demo, _music = _sources(tmp_path, music_duration=0.2)
    output = tmp_path / f"{hook_format}.mp4"
    spec = MediaSpec(width=90, height=160, fps=10, intro_seconds=0.3, total_seconds=1.5)

    MediaComposer(_font(), spec).compose(
        opening, demo, None, hook, output, hook_format=hook_format
    )

    result = validate_output(output, spec, require_audio=False)
    assert result.audio_codec is None


def test_generated_visual_must_be_nine_by_sixteen(tmp_path):
    vertical = tmp_path / "vertical.mp4"
    square = tmp_path / "square.mp4"
    _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=90x160:d=0.1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(vertical),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=100x100:d=0.1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(square),
        ]
    )
    assert validate_vertical_asset(vertical) == (90, 160)
    with pytest.raises(QualityError, match="not 9:16"):
        validate_vertical_asset(square)


def test_composer_uses_top_centered_tiktok_caption_without_banner(
    tmp_path, monkeypatch
):
    opening, demo, music = _sources(tmp_path, music_duration=0.2)
    font = _font()
    output = tmp_path / "final.mp4"
    captured = {}

    def fake_run(command, **_kwargs):
        captured["filters"] = command[command.index("-filter_complex") + 1]
        Path(command[-1]).write_bytes(b"encoded")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("flagd_ugc.media.subprocess.run", fake_run)

    MediaComposer(font).compose(opening, demo, music, "pov: this worked", output)

    filters = captured["filters"]
    assert "drawbox=" not in filters
    assert "x=(w-text_w)/2:y=165" in filters
    assert "fontcolor=white" in filters
    assert "bordercolor=black@0.82" in filters

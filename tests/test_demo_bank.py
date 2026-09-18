import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flagd_ugc.assets import load_demos
from flagd_ugc.demo_bank import DemoTarget, prepare_demo_bank, validate_prepared_demo
from flagd_ugc.errors import AssetError


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)


def _source(path: Path, duration: float = 1.2) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=117x253:r=20:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _plan(path: Path, *, segment_end: float = 1.0) -> None:
    path.write_text(
        json.dumps(
            {
                "target": {"width": 90, "height": 160, "fps": 10, "duration": 1},
                "demos": [
                    {
                        "id": "test-demo",
                        "source": "capture.mov",
                        "output": "test-demo.mp4",
                        "description": "A truthful test demonstration.",
                        "category": "test",
                        "segments": [{"start": 0.2, "end": segment_end}],
                        "approved": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_prepare_demo_bank_imports_raw_and_writes_valid_manifest(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "data" / "demos"
    source_dir.mkdir()
    _source(source_dir / "capture.mov")
    plan = tmp_path / "plan.json"
    _plan(plan, segment_end=0.8)

    prepared = prepare_demo_bank(plan, source_dir, output_dir)

    assert len(prepared) == 1
    assert (output_dir / "raw" / "capture.mov").is_file()
    probe = validate_prepared_demo(
        output_dir / "test-demo.mp4",
        target=DemoTarget(width=90, height=160, fps=10, duration=1),
    )
    assert probe["has_audio"] is False
    demos = load_demos(output_dir)
    assert [demo.description for demo in demos] == ["A truthful test demonstration."]
    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["format"]["duration_seconds"] == 1
    assert manifest["demos"][0]["category"] == "test"


def test_prepare_demo_bank_rejects_segment_past_source(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _source(source_dir / "capture.mov", duration=0.5)
    plan = tmp_path / "plan.json"
    _plan(plan, segment_end=2.0)

    with pytest.raises(AssetError, match="but source is"):
        prepare_demo_bank(plan, source_dir, tmp_path / "output")

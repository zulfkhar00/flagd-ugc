import json
import re
from pathlib import Path

import pytest

from flagd_ugc.config import load_local_env
from flagd_ugc.scene_lab import (
    SceneLab,
    default_motion_prompt,
    default_scene_prompt,
    estimated_image_cost,
)


class NeverCalledProvider:
    def generate_image(self, *args, **kwargs):
        raise AssertionError("dry-run must not call the image provider")


def test_dry_run_creates_reproducible_review_bundle(tmp_path, monkeypatch):
    master = tmp_path / "master.jpeg"
    imported = tmp_path / "existing.jpeg"
    master.write_bytes(b"master")
    imported.write_bytes(b"candidate")
    monkeypatch.setattr(
        "flagd_ugc.scene_lab.validate_vertical_asset", lambda path: (1080, 1920)
    )

    result = SceneLab(NeverCalledProvider()).run(
        master=master,
        output_root=tmp_path / "runs",
        samples=4,
        model="gemini-3-pro-image",
        imported_images=[imported],
        dry_run=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.report_path.is_file()
    assert (result.run_dir / "master.jpeg").read_bytes() == b"master"
    assert (result.run_dir / "imported-01.jpeg").read_bytes() == b"candidate"
    assert manifest["mode"] == "dry-run"
    assert manifest["scenario"] == "bedroom-seated"
    assert manifest["prompt_version"] == "bedroom-seated-v8-selfie"
    assert manifest["wardrobe"] == "charcoal-hoodie"
    assert manifest["requested_samples"] == 4
    assert manifest["estimated_generation_cost_usd"] == 0
    assert manifest["candidates"][0]["status"] == "ready"
    assert "Same unmistakable identity" in result.report_path.read_text(
        encoding="utf-8"
    )


def test_selected_scenario_is_recorded_with_its_paired_prompts(tmp_path):
    master = tmp_path / "master.jpeg"
    master.write_bytes(b"master")

    result = SceneLab(None).run(
        master=master,
        output_root=tmp_path / "runs",
        samples=1,
        model="gemini-3-pro-image",
        scenario="city-walk",
        dry_run=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["scenario"] == "city-walk"
    assert manifest["prompt_version"] == "city-walk-v4-selfie"
    assert manifest["wardrobe"] == "olive-street-layers"
    assert "mid-step" in (result.run_dir / "SCENE_PROMPT.txt").read_text()
    assert "background parallax" in (result.run_dir / "MOTION_PROMPT.txt").read_text()


def test_generation_keeps_quality_failures_visible_instead_of_replacing_them(
    tmp_path, monkeypatch
):
    master = tmp_path / "master.jpeg"
    master.write_bytes(b"master")
    calls = 0

    class Provider:
        def generate_image(self, prompt, references, output, **kwargs):
            nonlocal calls
            calls += 1
            output.write_bytes(b"generated")

    def inspect(path: Path):
        if path.name == "generated-01.png":
            from flagd_ugc.errors import QualityError

            raise QualityError("not 9:16")
        return (1080, 1920)

    monkeypatch.setattr("flagd_ugc.scene_lab.validate_vertical_asset", inspect)
    result = SceneLab(Provider()).run(
        master=master,
        output_root=tmp_path / "runs",
        samples=2,
        model="gemini-3-pro-image",
    )

    assert calls == 2
    assert [item.status for item in result.candidates] == ["invalid", "ready"]


@pytest.mark.parametrize(
    ("model", "size", "expected"),
    [
        ("gemini-3-pro-image", "1K", 0.134),
        ("gemini-3-pro-image", "2k", 0.134),
        ("gemini-3-pro-image", "4K", 0.24),
        ("unknown", "2K", None),
    ],
)
def test_estimated_image_cost(model, size, expected):
    assert estimated_image_cost(model, size) == expected


def test_default_prompt_is_frame_zero_and_does_not_specify_age():
    prompt = default_scene_prompt().lower()
    motion = default_motion_prompt().lower()

    assert "instant immediately before the reaction" in prompt
    assert "not the peak reaction" in prompt
    assert "she holds the front-camera phone herself" in prompt
    assert "the free hand remains below the crop" in prompt
    assert "ordinary lived-in bedroom" in prompt
    assert "three-second" in prompt
    assert re.search(r"\bage\b", prompt) is None
    assert "year old" not in prompt
    assert "0.0–0.6 seconds" in motion
    assert "no cut" in motion
    assert "mouth, lips and jaw remain still" in motion.lower()
    assert "silently mouth any words" in motion.lower()


def test_default_prompt_prevents_short_hair_and_selfie_arm_regressions():
    prompt = default_scene_prompt().lower()

    assert "visible strands pass both shoulders" in prompt
    assert "it must not end at her chin, jaw or neck" in prompt
    assert "show only her shoulder caps and upper chest" in prompt
    assert "phone are necessarily outside their own image" in prompt
    assert "natural arm's length" in prompt
    assert "no second camera or mirror appears" in prompt


def test_default_prompt_explicitly_requires_unpolished_real_life_composition():
    prompt = default_scene_prompt().lower()

    assert "subtly off-centre" in prompt
    assert "mild exposure unevenness" in prompt
    assert "allow minor compositional awkwardness" in prompt
    assert "do not clean, balance or perfect the scene" in prompt
    assert "no polished portrait" in prompt


def test_local_env_loads_values_without_overriding_shell(tmp_path, monkeypatch):
    env_file = tmp_path / "settings.txt"
    env_file.write_text("EXISTING_VALUE=from-file\nNEW_VALUE='new-file'\n")
    monkeypatch.setenv("EXISTING_VALUE", "from-shell")
    monkeypatch.delenv("NEW_VALUE", raising=False)

    load_local_env(env_file)

    assert __import__("os").environ["EXISTING_VALUE"] == "from-shell"
    assert __import__("os").environ["NEW_VALUE"] == "new-file"

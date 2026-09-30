from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from string import Template

from .camera_style import (
    US_BEDROOM_SETTING_INSTRUCTION,
    US_ENTRYWAY_SETTING_INSTRUCTION,
    US_KITCHEN_SETTING_INSTRUCTION,
    US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION,
    US_STREET_SETTING_INSTRUCTION,
    with_handheld_motion,
    with_handheld_scene,
)
from .wardrobes import get_wardrobe


@dataclass(frozen=True)
class ScenarioDefinition:
    key: str
    label: str
    prompt_version: str
    scene_file: str
    motion_file: str
    default_wardrobe: str
    allow_hand_entry: bool = False

    def scene_prompt(self, wardrobe: str | None = None) -> str:
        source = (
            files("flagd_ugc.prompts")
            .joinpath(self.scene_file)
            .read_text(encoding="utf-8")
        )
        selected = get_wardrobe(wardrobe or self.default_wardrobe)
        prompt = Template(source).substitute(wardrobe=selected.prompt)
        if self.key in {"bedroom-seated", "bed-reclined", "bed-facepalm"}:
            prompt = f"{prompt.rstrip()}\n\n{US_BEDROOM_SETTING_INSTRUCTION}"
        if self.key == "kitchen-cover-mouth":
            prompt = f"{prompt.rstrip()}\n\n{US_KITCHEN_SETTING_INSTRUCTION}"
        if self.key == "doorway-backout":
            prompt = f"{prompt.rstrip()}\n\n{US_ENTRYWAY_SETTING_INSTRUCTION}"
        if self.key == "city-walk":
            prompt = f"{prompt.rstrip()}\n\n{US_STREET_SETTING_INSTRUCTION}"
        return with_handheld_scene(
            f"{prompt.rstrip()}\n\n{US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION}"
        )

    def motion_prompt(self) -> str:
        source = (
            files("flagd_ugc.prompts")
            .joinpath(self.motion_file)
            .read_text(encoding="utf-8")
        )
        return with_handheld_motion(source)


SCENARIOS = {
    item.key: item
    for item in (
        ScenarioDefinition(
            key="bedroom-seated",
            label="Casually seated in a lived-in bedroom",
            prompt_version="bedroom-seated-v8-selfie",
            scene_file="sudden_realization_v4.txt",
            motion_file="sudden_realization_motion_v3.txt",
            default_wardrobe="charcoal-hoodie",
        ),
        ScenarioDefinition(
            key="bed-reclined",
            label="Reclining imperfectly against rumpled bedding",
            prompt_version="bed-reclined-v3-selfie",
            scene_file="bed_reclined_v1.txt",
            motion_file="bed_reclined_motion_v1.txt",
            default_wardrobe="washed-navy-tee",
        ),
        ScenarioDefinition(
            key="desk-chair",
            label="Sitting sideways in an ordinary desk chair",
            prompt_version="desk-chair-v3-selfie",
            scene_file="desk_chair_v1.txt",
            motion_file="desk_chair_motion_v1.txt",
            default_wardrobe="forest-sweatshirt",
        ),
        ScenarioDefinition(
            key="city-walk",
            label="Walking casually on an ordinary city street",
            prompt_version="city-walk-v4-selfie",
            scene_file="city_walk_v1.txt",
            motion_file="city_walk_motion_v2.txt",
            default_wardrobe="olive-street-layers",
        ),
        ScenarioDefinition(
            key="doorway-backout",
            label="Enters an ordinary room, notices something and backs out",
            prompt_version="doorway-backout-v2-selfie",
            scene_file="doorway_backout_v1.txt",
            motion_file="doorway_backout_motion_v1.txt",
            default_wardrobe="burgundy-long-sleeve",
        ),
        ScenarioDefinition(
            key="chair-confused-rise",
            label="Looks confused at the camera and abruptly starts to stand",
            prompt_version="chair-confused-rise-v2-selfie",
            scene_file="chair_confused_rise_v1.txt",
            motion_file="chair_confused_rise_motion_v1.txt",
            default_wardrobe="heather-lilac-tee",
        ),
        ScenarioDefinition(
            key="bed-facepalm",
            label="Reclines on an unmade bed and gives a restrained facepalm",
            prompt_version="bed-facepalm-v2-selfie",
            scene_file="bed_facepalm_v1.txt",
            motion_file="bed_facepalm_motion_v1.txt",
            default_wardrobe="black-zip-hoodie",
            allow_hand_entry=True,
        ),
        ScenarioDefinition(
            key="kitchen-cover-mouth",
            label="Freezes in an ordinary kitchen and covers her mouth",
            prompt_version="kitchen-cover-mouth-v2-selfie",
            scene_file="kitchen_cover_mouth_v1.txt",
            motion_file="kitchen_cover_mouth_motion_v1.txt",
            default_wardrobe="rust-striped-tee",
            allow_hand_entry=True,
        ),
    )
}

DEFAULT_SCENARIO = "bedroom-seated"


def get_scenario(key: str) -> ScenarioDefinition:
    try:
        return SCENARIOS[key]
    except KeyError as exc:
        raise ValueError(
            f"unknown scenario {key!r}; choose from {', '.join(SCENARIOS)}"
        ) from exc


def scenario_keys() -> tuple[str, ...]:
    return tuple(SCENARIOS)

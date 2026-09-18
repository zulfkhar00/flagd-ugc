from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from .camera_style import with_handheld_motion


@dataclass(frozen=True)
class VisualHookDefinition:
    key: str
    label: str
    prompt_file: str
    recommended_scenario: str
    allow_hand_entry: bool = False

    def motion_prompt(self) -> str:
        source = (
            files("flagd_ugc.prompts")
            .joinpath(self.prompt_file)
            .read_text(encoding="utf-8")
        )
        return with_handheld_motion(source)


VISUAL_HOOKS = {
    item.key: item
    for item in (
        VisualHookDefinition(
            key="bed-facepalm",
            label="Lying in bed, confused, then a restrained facepalm",
            prompt_file="hook_bed_facepalm_v1.txt",
            recommended_scenario="bed-reclined",
            allow_hand_entry=True,
        ),
        VisualHookDefinition(
            key="chair-confused-rise",
            label="Looks confused, leans forward and starts getting up",
            prompt_file="hook_chair_confused_rise_v1.txt",
            recommended_scenario="desk-chair",
        ),
    )
}


def visual_hook_keys() -> tuple[str, ...]:
    return tuple(VISUAL_HOOKS)


def get_visual_hook(key: str) -> VisualHookDefinition:
    try:
        return VISUAL_HOOKS[key]
    except KeyError as exc:
        raise ValueError(
            f"unknown visual hook {key!r}; choose from {', '.join(visual_hook_keys())}"
        ) from exc

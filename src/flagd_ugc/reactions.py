from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from .camera_style import with_handheld_motion


@dataclass(frozen=True)
class ReactionDefinition:
    key: str
    label: str
    prompt_file: str
    mouth_motion: str
    allow_hand_entry: bool = False

    def motion_prompt(self) -> str:
        source = (
            files("flagd_ugc.prompts")
            .joinpath(self.prompt_file)
            .read_text(encoding="utf-8")
        )
        return with_handheld_motion(source)


REACTIONS = {
    item.key: item
    for item in (
        ReactionDefinition(
            "silent-gasp",
            "Strong silent gasp",
            "reaction_silent_gasp_v1.txt",
            "single-open-hold",
        ),
        ReactionDefinition(
            "suppressed-laugh",
            "Suppressed laugh breaks through",
            "reaction_suppressed_laugh_v1.txt",
            "single-laugh-cycle",
        ),
        ReactionDefinition(
            "skeptical-cringe",
            "Asymmetric skeptical cringe",
            "reaction_skeptical_cringe_v1.txt",
            "single-close-hold",
        ),
        ReactionDefinition(
            "caught-off-guard",
            "Caught-off-guard double take",
            "reaction_caught_off_guard_v1.txt",
            "locked",
        ),
        ReactionDefinition(
            "shock-cover-mouth",
            "Small shock with one hand covering her mouth",
            "reaction_shock_cover_mouth_v1.txt",
            "locked-then-covered",
            allow_hand_entry=True,
        ),
        ReactionDefinition(
            "facepalm-disbelief",
            "Dry disbelief with one hand to her forehead",
            "reaction_facepalm_disbelief_v1.txt",
            "single-close-hold",
            allow_hand_entry=True,
        ),
        ReactionDefinition(
            "delighted-hand-chest",
            "Delighted realization with one hand to her chest",
            "reaction_delighted_hand_chest_v1.txt",
            "single-open-close",
            allow_hand_entry=True,
        ),
        ReactionDefinition(
            "no-way-head-shake",
            "Compact no-way head shake",
            "reaction_no_way_head_shake_v1.txt",
            "locked",
        ),
    )
}


def reaction_keys() -> tuple[str, ...]:
    return tuple(REACTIONS)


def get_reaction(key: str) -> ReactionDefinition:
    try:
        return REACTIONS[key]
    except KeyError as exc:
        raise ValueError(
            f"unknown reaction {key!r}; choose from {', '.join(reaction_keys())}"
        ) from exc

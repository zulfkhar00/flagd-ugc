"""Deterministic creative direction for daily self-shot opening clips.

The copy format is chosen before generation and persisted with the video.  This module maps
that already-persisted format to one concrete, tested opening treatment.  It deliberately
does not ask a model to choose a setting, outfit, or reaction: a resumed batch therefore
uses the same creative recipe it started with.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QualityError
from .hook_formats import hook_format_keys
from .reactions import get_reaction
from .scenarios import get_scenario
from .visual_hooks import get_visual_hook
from .wardrobes import get_wardrobe


@dataclass(frozen=True)
class OpeningDirection:
    """One approved, motion-ready selfie opening recipe."""

    key: str
    hook_format: str
    scenario_key: str
    wardrobe_key: str
    reaction_key: str | None = None
    visual_hook_key: str | None = None

    def __post_init__(self) -> None:
        if bool(self.reaction_key) == bool(self.visual_hook_key):
            raise ValueError("an opening direction needs exactly one motion treatment")
        # Resolve eagerly so accidental misspellings cannot reach a paid provider call.
        get_scenario(self.scenario_key)
        get_wardrobe(self.wardrobe_key)
        if self.reaction_key:
            get_reaction(self.reaction_key)
        if self.visual_hook_key:
            get_visual_hook(self.visual_hook_key)

    def scene_prompt(self) -> str:
        """Return the fully specified frame-zero prompt for Nano Banana Pro."""
        scenario = get_scenario(self.scenario_key)
        return (
            f"DAILY OPENING DIRECTION: {self.key}\n"
            f"This is a {scenario.label.lower()} opening. Use the specified wardrobe and "
            "keep the top safe area clear; a caption is composited locally after animation.\n\n"
            f"{scenario.scene_prompt(self.wardrobe_key)}"
        )

    def motion_prompt(self) -> str:
        """Return the chosen silent visual-hook performance for Kling.

        The input still is the authority for the actual room, outfit, framing and starting
        posture.  The performance prompt controls only the three-second reaction.  That
        avoids a reaction preset accidentally changing a scene's wardrobe or location.
        """
        if self.visual_hook_key:
            performance = get_visual_hook(self.visual_hook_key).motion_prompt()
        else:
            assert self.reaction_key is not None
            performance = get_reaction(self.reaction_key).motion_prompt()
        return (
            "Match the input room, clothes, framing and pose exactly. Perform only this "
            "silent reaction in one continuous three-second self-shot clip.\n\n"
            f"{performance}"
        )

    @property
    def allow_hand_entry(self) -> bool:
        """Whether the planned free-hand gesture needs fal's hand-entry allowance."""
        if self.visual_hook_key:
            return get_visual_hook(self.visual_hook_key).allow_hand_entry
        assert self.reaction_key is not None
        return get_reaction(self.reaction_key).allow_hand_entry


# Each format has its own setting, wardrobe and visual action.  Since one daily batch has
# distinct formats, this creates four visibly different openings without random prompt drift.
OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = (
    OpeningDirection(
        key="scorecard-bed-facepalm-v1",
        hook_format="scorecard",
        scenario_key="bed-facepalm",
        wardrobe_key="black-zip-hoodie",
        visual_hook_key="bed-facepalm",
    ),
    OpeningDirection(
        key="inner-thought-kitchen-cover-mouth-v1",
        hook_format="inner-thought",
        scenario_key="kitchen-cover-mouth",
        wardrobe_key="rust-striped-tee",
        reaction_key="shock-cover-mouth",
    ),
    OpeningDirection(
        key="pov-bedroom-delighted-v1",
        hook_format="pov",
        scenario_key="bedroom-seated",
        wardrobe_key="cream-knit",
        reaction_key="delighted-hand-chest",
    ),
    OpeningDirection(
        key="quote-receipt-chair-rise-v1",
        hook_format="quote-receipt",
        scenario_key="chair-confused-rise",
        wardrobe_key="heather-lilac-tee",
        visual_hook_key="chair-confused-rise",
    ),
    OpeningDirection(
        key="when-city-double-take-v1",
        hook_format="when",
        scenario_key="city-walk",
        wardrobe_key="olive-street-layers",
        reaction_key="caught-off-guard",
    ),
    OpeningDirection(
        key="direct-doorway-no-way-v1",
        hook_format="direct-observation",
        scenario_key="doorway-backout",
        wardrobe_key="burgundy-long-sleeve",
        reaction_key="no-way-head-shake",
    ),
    OpeningDirection(
        key="reaction-meme-bed-disbelief-v1",
        hook_format="reaction-meme",
        scenario_key="bed-reclined",
        wardrobe_key="washed-navy-tee",
        reaction_key="facepalm-disbelief",
    ),
)

_BY_KEY = {item.key: item for item in OPENING_DIRECTIONS}
_BY_FORMAT = {item.hook_format: item for item in OPENING_DIRECTIONS}

if set(_BY_FORMAT) != set(hook_format_keys()):  # pragma: no cover - module invariant
    raise RuntimeError("every hook format must have one opening direction")


def get_opening_direction(key: str) -> OpeningDirection:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise QualityError(f"unknown opening direction {key!r}") from exc


def direction_for_hook_format(hook_format: str) -> OpeningDirection:
    try:
        return _BY_FORMAT[hook_format]
    except KeyError as exc:
        raise QualityError(
            f"no approved opening direction for hook format {hook_format!r}"
        ) from exc


def opening_direction_keys() -> tuple[str, ...]:
    return tuple(item.key for item in OPENING_DIRECTIONS)

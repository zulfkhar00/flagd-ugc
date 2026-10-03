"""Persisted silent reaction directions for daily self-shot opening clips.

The copy planner chooses an expressive reaction to match the caption and approved demo.
Settings and wardrobes rotate by date. Older task and reaction recipes remain readable
so a restart never silently replaces an already planned video.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QualityError
from .hook_formats import hook_format_keys
from .reaction_openings import (
    HOOK_REACTIONS,
    LEGACY_HOOK_REACTIONS,
    REACTION_SCENES,
    get_active_hook_reaction,
    get_hook_reaction,
    get_reaction_scene,
)
from .reactions import get_reaction
from .scenarios import get_scenario
from .task_scenes import TASK_SCENES, get_task_scene, task_scene_keys
from .visual_hooks import get_visual_hook
from .wardrobes import get_wardrobe


@dataclass(frozen=True)
class OpeningDirection:
    """One approved, motion-ready selfie opening recipe."""

    key: str
    hook_format: str
    scenario_key: str | None
    wardrobe_key: str
    reaction_key: str | None = None
    visual_hook_key: str | None = None
    task_key: str | None = None
    reaction_scene_key: str | None = None

    def __post_init__(self) -> None:
        if self.reaction_scene_key:
            if self.scenario_key or self.visual_hook_key or self.task_key:
                raise ValueError("a daily reaction cannot also select a legacy treatment")
            if not self.reaction_key:
                raise ValueError("a daily reaction opening needs a reaction")
            get_reaction_scene(self.reaction_scene_key)
            get_hook_reaction(self.reaction_key)
            get_wardrobe(self.wardrobe_key)
            return
        if self.task_key:
            if self.scenario_key or self.reaction_key or self.visual_hook_key:
                raise ValueError(
                    "a task opening direction cannot also select a legacy scenario"
                )
            get_task_scene(self.task_key)
            get_wardrobe(self.wardrobe_key)
            return
        if bool(self.reaction_key) == bool(self.visual_hook_key):
            raise ValueError("an opening direction needs exactly one motion treatment")
        # Resolve eagerly so accidental misspellings cannot reach a paid provider call.
        assert self.scenario_key is not None
        get_scenario(self.scenario_key)
        get_wardrobe(self.wardrobe_key)
        if self.reaction_key:
            get_reaction(self.reaction_key)
        if self.visual_hook_key:
            get_visual_hook(self.visual_hook_key)

    def scene_prompt(self) -> str:
        """Return the fully specified frame-zero prompt for Nano Banana Pro."""
        if self.reaction_scene_key:
            assert self.reaction_key is not None
            reaction = get_hook_reaction(self.reaction_key)
            scene = get_reaction_scene(self.reaction_scene_key)
            return (
                f"DAILY REACTION OPENING: {scene.label}. The planned animation is a "
                f"{reaction.label}; capture its {'onset' if reaction.expressive else 'relaxed starting expression'}.\n\n"
                f"{scene.scene_prompt(expressive=reaction.expressive)}"
            )
        if self.task_key:
            task = get_task_scene(self.task_key)
            return (
                f"DAILY TASK OPENING: {task.label}. Keep the top safe area clear; "
                "the short caption is composited locally after animation.\n\n"
                f"{task.scene_prompt()}"
            )
        assert self.scenario_key is not None
        scenario = get_scenario(self.scenario_key)
        return (
            f"DAILY OPENING DIRECTION: {self.key}\n"
            f"This is a {scenario.label.lower()} opening. Use the specified wardrobe and "
            "keep the top safe area clear; a caption is composited locally after animation.\n\n"
            f"{scenario.scene_prompt(self.wardrobe_key)}"
        )

    def motion_prompt(self) -> str:
        """Return one expressive reaction or a persisted legacy continuation.

        The input still is the authority for the actual room, outfit, framing and starting
        posture. The direction controls only the three-second continuation and does
        not replace the visual information in the starting frame.
        """
        if self.reaction_scene_key:
            assert self.reaction_key is not None
            return get_hook_reaction(self.reaction_key).motion_prompt()
        if self.task_key:
            performance = get_task_scene(self.task_key).motion_prompt()
            return (
                "Continue only the mundane physical task already visible in the input. "
                "Keep its action, room, outfit, identity, framing and starting posture.\n\n"
                f"{performance}"
            )
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
        if self.reaction_scene_key:
            return False
        if self.task_key:
            return False
        if self.visual_hook_key:
            return get_visual_hook(self.visual_hook_key).allow_hand_entry
        assert self.reaction_key is not None
        return get_reaction(self.reaction_key).allow_hand_entry


# Previous keys are retained so already persisted batches can resume their original scenes.
LEGACY_OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = (
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

TASK_OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = tuple(
    OpeningDirection(
        key=f"task-{hook_format}-{task.key}-v1",
        hook_format=hook_format,
        scenario_key=None,
        wardrobe_key=task.wardrobe_key,
        task_key=task.key,
    )
    for hook_format in hook_format_keys()
    for task in TASK_SCENES
)
_TASK_BY_KEY = {item.key: item for item in TASK_OPENING_DIRECTIONS}
_LEGACY_BY_KEY = {item.key: item for item in LEGACY_OPENING_DIRECTIONS}
REACTION_OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = tuple(
    OpeningDirection(
        key=f"reaction-{hook_format}-{scene.key}-{reaction.key}-v2",
        hook_format=hook_format,
        scenario_key=None,
        wardrobe_key=scene.wardrobe_key,
        reaction_key=reaction.key,
        reaction_scene_key=scene.key,
    )
    for hook_format in hook_format_keys()
    for scene in REACTION_SCENES
    for reaction in HOOK_REACTIONS
)
ARCHIVED_REACTION_OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = tuple(
    OpeningDirection(
        key=f"reaction-{hook_format}-{scene.key}-{reaction.key}-v1",
        hook_format=hook_format,
        scenario_key=None,
        wardrobe_key=scene.wardrobe_key,
        reaction_key=reaction.key,
        reaction_scene_key=scene.key,
    )
    for hook_format in hook_format_keys()
    for scene in REACTION_SCENES
    for reaction in LEGACY_HOOK_REACTIONS
)
_REACTION_BY_KEY = {
    item.key: item for item in (*ARCHIVED_REACTION_OPENING_DIRECTIONS, *REACTION_OPENING_DIRECTIONS)
}
_REACTION_BY_TREATMENT = {
    (item.hook_format, item.reaction_scene_key, item.reaction_key): item
    for item in REACTION_OPENING_DIRECTIONS
}

if len(TASK_OPENING_DIRECTIONS) != len(hook_format_keys()) * len(task_scene_keys()):
    raise RuntimeError("task opening direction matrix is incomplete")


def get_opening_direction(key: str) -> OpeningDirection:
    direction = (
        _REACTION_BY_KEY.get(key) or _TASK_BY_KEY.get(key) or _LEGACY_BY_KEY.get(key)
    )
    if direction is None:
        raise QualityError(f"unknown opening direction {key!r}")
    return direction


def direction_for_hook_format(
    hook_format: str, *, rotation: int = 0, reaction_key: str = "shocked"
) -> OpeningDirection:
    formats = hook_format_keys()
    try:
        format_index = formats.index(hook_format)
    except ValueError as exc:
        raise QualityError(
            f"no approved opening direction for hook format {hook_format!r}"
        ) from exc
    reaction = get_active_hook_reaction(reaction_key)
    scene = REACTION_SCENES[(rotation + format_index) % len(REACTION_SCENES)]
    return _REACTION_BY_TREATMENT[(hook_format, scene.key, reaction.key)]


OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = tuple(
    direction_for_hook_format(hook_format) for hook_format in hook_format_keys()
)


def opening_direction_keys() -> tuple[str, ...]:
    return tuple(item.key for item in REACTION_OPENING_DIRECTIONS)

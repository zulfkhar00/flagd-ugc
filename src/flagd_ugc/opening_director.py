"""Deterministic, date-rotated task direction for daily self-shot opening clips.

The copy format and task are chosen before generation and persisted with the video. This
module maps the format and batch date to a concrete task, setting and wardrobe. It does not
ask a model to invent an action or perform a separate reaction for the viewer. Persisted
legacy reaction directions remain readable for existing batches that need to resume.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QualityError
from .hook_formats import hook_format_keys
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

    def __post_init__(self) -> None:
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
        """Return the chosen silent task continuation for Kling or a legacy performance.

        The input still is the authority for the actual room, outfit, framing and starting
        posture. The task direction controls only the three-second continuation and does
        not replace the visual information in the starting frame.
        """
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
_TASK_BY_FORMAT_AND_SCENE = {
    (item.hook_format, item.task_key): item for item in TASK_OPENING_DIRECTIONS
}
_TASK_BY_KEY = {item.key: item for item in TASK_OPENING_DIRECTIONS}
_LEGACY_BY_KEY = {item.key: item for item in LEGACY_OPENING_DIRECTIONS}
OPENING_DIRECTIONS: tuple[OpeningDirection, ...] = tuple(
    _TASK_BY_FORMAT_AND_SCENE[
        (hook_format, task_scene_keys()[index % len(task_scene_keys())])
    ]
    for index, hook_format in enumerate(hook_format_keys())
)

if len(TASK_OPENING_DIRECTIONS) != len(hook_format_keys()) * len(task_scene_keys()):
    raise RuntimeError("task opening direction matrix is incomplete")


def get_opening_direction(key: str) -> OpeningDirection:
    direction = _TASK_BY_KEY.get(key) or _LEGACY_BY_KEY.get(key)
    if direction is None:
        raise QualityError(f"unknown opening direction {key!r}")
    return direction


def direction_for_hook_format(
    hook_format: str, *, rotation: int = 0
) -> OpeningDirection:
    formats = hook_format_keys()
    try:
        format_index = formats.index(hook_format)
    except ValueError as exc:
        raise QualityError(
            f"no approved opening direction for hook format {hook_format!r}"
        ) from exc
    task_key = task_scene_keys()[(rotation + format_index) % len(task_scene_keys())]
    return _TASK_BY_FORMAT_AND_SCENE[(hook_format, task_key)]


def opening_direction_keys() -> tuple[str, ...]:
    return tuple(item.key for item in TASK_OPENING_DIRECTIONS)

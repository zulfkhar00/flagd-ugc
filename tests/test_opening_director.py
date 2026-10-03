import pytest

from flagd_ugc.errors import QualityError
from flagd_ugc.fal_provider import MAX_FAL_PROMPT_CHARS
from flagd_ugc.hook_formats import hook_format_keys
from flagd_ugc.opening_director import (
    LEGACY_OPENING_DIRECTIONS,
    OPENING_DIRECTIONS,
    REACTION_OPENING_DIRECTIONS,
    TASK_OPENING_DIRECTIONS,
    direction_for_hook_format,
    get_opening_direction,
)
from flagd_ugc.reaction_openings import HOOK_REACTIONS


def test_each_caption_format_resolves_to_a_reaction_in_a_distinct_default_setting():
    assert {item.hook_format for item in OPENING_DIRECTIONS} == set(hook_format_keys())
    assert len({item.reaction_scene_key for item in OPENING_DIRECTIONS}) == len(
        OPENING_DIRECTIONS
    )
    assert all(item.reaction_key and not item.task_key for item in OPENING_DIRECTIONS)


def test_direction_preserves_planned_emotion_and_candid_caption_safe_scene():
    direction = direction_for_hook_format("pov", reaction_key="cringing")

    assert get_opening_direction(direction.key) is direction
    assert "SELF-SHOT FRONT CAMERA" in direction.scene_prompt()
    assert "top safe area clear" in direction.scene_prompt().lower()
    assert "strong reaction" in direction.scene_prompt()
    assert "visibly winces" in direction.motion_prompt()
    assert direction.key.endswith("-v2")
    assert "silent front-camera reaction hook" in direction.motion_prompt()
    assert not direction.allow_hand_entry


def test_unknown_reaction_is_rejected_before_paid_media_generation():
    with pytest.raises(QualityError, match="unknown daily hook reaction"):
        direction_for_hook_format("pov", reaction_key="invented-emotion")


def test_each_daily_reaction_is_explicitly_silent_and_fits_fal_budget():
    for reaction in HOOK_REACTIONS:
        prompt = reaction.motion_prompt()
        assert reaction.movement in prompt
        assert "no speech, lip-sync, silent mouthing" in prompt.lower()
        assert "wrist drift" in prompt
        assert len(prompt) <= MAX_FAL_PROMPT_CHARS
    for direction in (
        *REACTION_OPENING_DIRECTIONS, *TASK_OPENING_DIRECTIONS, *LEGACY_OPENING_DIRECTIONS
    ):
        assert len(direction.motion_prompt()) <= MAX_FAL_PROMPT_CHARS


def test_daily_rotation_is_deterministic_and_keeps_four_settings_distinct():
    selected_formats = ("pov", "when", "reaction-meme", "inner-thought")
    first = [
        direction_for_hook_format(item, rotation=739512)
        for item in selected_formats
    ]
    repeated = [
        direction_for_hook_format(item, rotation=739512)
        for item in selected_formats
    ]
    next_day = [
        direction_for_hook_format(item, rotation=739513)
        for item in selected_formats
    ]

    assert [item.key for item in first] == [item.key for item in repeated]
    assert len({item.reaction_scene_key for item in first}) == len(selected_formats)
    assert len({item.wardrobe_key for item in first}) == len(selected_formats)
    assert [item.key for item in first] != [item.key for item in next_day]
    assert all(get_opening_direction(item.key) is item for item in first)


def test_legacy_reaction_and_task_recipes_remain_resolvable_with_original_motion():
    assert get_opening_direction("scorecard-bed-facepalm-v1").scenario_key == "bed-facepalm"
    direction = get_opening_direction("task-pov-bedroom-make-bed-v1")
    assert direction.task_key == "bedroom-make-bed"
    assert "mundane physical task" in direction.motion_prompt()
    assert "gaze stays on the task" in " ".join(direction.motion_prompt().lower().split())


def test_archived_casual_reactions_cannot_be_selected_for_new_hooks():
    archived = get_opening_direction("reaction-pov-bedroom-bed-edge-surprised-v1")
    assert "Her mouth remains relaxed and closed; no gasp." in archived.motion_prompt()
    with pytest.raises(QualityError, match="archived"):
        direction_for_hook_format("pov", reaction_key="surprised")
    assert all(item.reaction_key in {r.key for r in HOOK_REACTIONS} for item in OPENING_DIRECTIONS)

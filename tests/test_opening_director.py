from flagd_ugc.fal_provider import MAX_FAL_PROMPT_CHARS
from flagd_ugc.hook_formats import hook_format_keys
from flagd_ugc.opening_director import (
    OPENING_DIRECTIONS,
    direction_for_hook_format,
    get_opening_direction,
)


def test_every_caption_format_has_one_unique_persistable_opening_direction():
    assert {item.hook_format for item in OPENING_DIRECTIONS} == set(hook_format_keys())
    assert len({item.key for item in OPENING_DIRECTIONS}) == len(OPENING_DIRECTIONS)
    assert len({item.scenario_key for item in OPENING_DIRECTIONS}) == len(
        OPENING_DIRECTIONS
    )
    assert len({item.wardrobe_key for item in OPENING_DIRECTIONS}) == len(
        OPENING_DIRECTIONS
    )


def test_direction_exposes_a_self_shot_scene_and_silent_motion_prompt():
    direction = direction_for_hook_format("scorecard")

    assert get_opening_direction(direction.key) is direction
    assert "SELF-SHOT FRONT CAMERA" in direction.scene_prompt()
    assert "top safe area clear" in direction.scene_prompt().lower()
    assert "silent" in direction.motion_prompt().lower()
    assert "SELF-SHOT FRONT CAMERA" in direction.motion_prompt()


def test_every_daily_motion_prompt_fits_fal_limit():
    for direction in OPENING_DIRECTIONS:
        assert len(direction.motion_prompt()) <= MAX_FAL_PROMPT_CHARS

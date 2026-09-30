from flagd_ugc.fal_provider import MAX_FAL_PROMPT_CHARS
from flagd_ugc.hook_formats import hook_format_keys
from flagd_ugc.opening_director import (
    LEGACY_OPENING_DIRECTIONS,
    OPENING_DIRECTIONS,
    TASK_OPENING_DIRECTIONS,
    direction_for_hook_format,
    get_opening_direction,
)


def test_every_caption_format_has_a_unique_default_task_direction():
    assert {item.hook_format for item in OPENING_DIRECTIONS} == set(hook_format_keys())
    assert len({item.key for item in OPENING_DIRECTIONS}) == len(OPENING_DIRECTIONS)
    assert len({item.task_key for item in OPENING_DIRECTIONS}) == len(
        OPENING_DIRECTIONS
    )
    assert len({item.wardrobe_key for item in OPENING_DIRECTIONS}) == len(
        OPENING_DIRECTIONS
    )
    assert all(item.task_key for item in OPENING_DIRECTIONS)


def test_direction_exposes_a_self_shot_scene_and_silent_motion_prompt():
    direction = direction_for_hook_format("scorecard")

    assert get_opening_direction(direction.key) is direction
    assert "SELF-SHOT FRONT CAMERA" in direction.scene_prompt()
    assert "top safe area clear" in direction.scene_prompt().lower()
    assert "silent" in direction.motion_prompt().lower()
    assert "SELF-SHOT FRONT CAMERA" in direction.motion_prompt()
    assert "posture" in direction.motion_prompt().lower()
    assert "gaze stays on the task" in " ".join(
        direction.motion_prompt().lower().split()
    )


def test_every_daily_motion_prompt_fits_fal_limit():
    for direction in (*TASK_OPENING_DIRECTIONS, *LEGACY_OPENING_DIRECTIONS):
        assert len(direction.motion_prompt()) <= MAX_FAL_PROMPT_CHARS


def test_all_task_scene_prompts_are_front_camera_task_first_and_caption_safe():
    for direction in TASK_OPENING_DIRECTIONS:
        prompt = direction.scene_prompt().lower()
        assert "self-shot front camera" in prompt
        assert "task-first starting frame" in prompt
        assert "top safe area clear" in prompt
        assert "her attention" in prompt


def test_daily_rotation_is_deterministic_and_keeps_four_batch_tasks_distinct():
    selected_formats = ("scorecard", "inner-thought", "pov", "quote-receipt")
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
    assert len({item.task_key for item in first}) == len(selected_formats)
    assert [item.key for item in first] != [item.key for item in next_day]
    assert all(get_opening_direction(item.key) is item for item in first)


def test_legacy_persisted_direction_keys_remain_resolvable():
    assert get_opening_direction("scorecard-bed-facepalm-v1").scenario_key == "bed-facepalm"

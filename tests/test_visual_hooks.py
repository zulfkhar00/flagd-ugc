from flagd_ugc.visual_hooks import get_visual_hook, visual_hook_keys


def test_visual_hooks_are_scene_aware_and_materially_distinct():
    assert visual_hook_keys() == (
        "bed-facepalm",
        "chair-confused-rise",
    )
    assert get_visual_hook("bed-facepalm").recommended_scenario == "bed-reclined"
    assert get_visual_hook("chair-confused-rise").recommended_scenario == "desk-chair"
    prompts = [get_visual_hook(key).motion_prompt() for key in visual_hook_keys()]
    assert len(set(prompts)) == len(prompts)


def test_visual_hooks_start_immediately_and_remain_silent():
    for key in visual_hook_keys():
        prompt = get_visual_hook(key).motion_prompt().lower()
        assert "three-second" in prompt
        assert "no cut" in prompt
        assert "first frame" in prompt
        assert "no speech" in prompt
        assert "lip-sync" in prompt
        assert "silent mouthing" in prompt
        assert "phoneme" in prompt
        assert "preserve her exact identity" in prompt
        assert "she continues holding the front-facing phone herself" in prompt
        assert "camera motion stays physically coupled" in prompt


def test_only_bed_facepalm_permits_hand_entry():
    assert get_visual_hook("bed-facepalm").allow_hand_entry is True
    assert get_visual_hook("chair-confused-rise").allow_hand_entry is False

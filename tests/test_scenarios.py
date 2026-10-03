import re

from flagd_ugc.scenarios import DEFAULT_SCENARIO, get_scenario, scenario_keys
from flagd_ugc.wardrobes import get_wardrobe, wardrobe_keys


def test_catalog_has_distinct_pose_and_setting_scenarios():
    assert DEFAULT_SCENARIO == "bedroom-seated"
    assert scenario_keys() == (
        "bedroom-seated",
        "bed-reclined",
        "desk-chair",
        "city-walk",
        "doorway-backout",
        "chair-confused-rise",
        "bed-facepalm",
        "kitchen-cover-mouth",
    )
    prompts = [get_scenario(key).scene_prompt() for key in scenario_keys()]
    assert len(set(prompts)) == len(scenario_keys())


def test_every_scenario_locks_identity_realism_and_motion():
    for key in scenario_keys():
        scene = get_scenario(key).scene_prompt().lower()
        motion = get_scenario(key).motion_prompt().lower()
        assert "same woman" in scene
        assert "identity" in scene
        assert "real" in scene
        assert "9:16" in scene
        assert "self-shot front camera" in scene
        assert "the woman herself holds" in scene
        assert "show exactly two arms total" in scene
        assert "never add a camera hand, branching limb" in scene
        assert "she continues holding the front-facing phone herself" in motion
        assert "camera motion stays physically coupled" in motion
        assert "as if another person" not in scene
        assert "as though a friend nearby" not in scene
        assert "as though someone nearby" not in scene
        assert re.search(r"\bage\b", scene) is None
        assert "three-second" in motion
        assert "no cut" in motion


def test_scenario_pose_instructions_are_materially_different():
    assert "reclining diagonally" in get_scenario("bed-reclined").scene_prompt()
    assert "desk chair" in get_scenario("desk-chair").scene_prompt()
    assert "mid-step" in get_scenario("city-walk").scene_prompt()
    assert "walked into the entry area of a normal contemporary American apartment" in get_scenario(
        "doorway-backout"
    ).scene_prompt()
    assert "starts getting out of her chair" in get_scenario(
        "chair-confused-rise"
    ).scene_prompt()
    assert "gives one restrained facepalm" in get_scenario(
        "bed-facepalm"
    ).scene_prompt()
    assert "crossing an ordinary kitchen" in get_scenario(
        "kitchen-cover-mouth"
    ).scene_prompt()


def test_scenarios_default_to_different_ordinary_wardrobes():
    defaults = [get_scenario(key).default_wardrobe for key in scenario_keys()]
    assert defaults == [
        "charcoal-hoodie",
        "washed-navy-tee",
        "forest-sweatshirt",
        "olive-street-layers",
        "burgundy-long-sleeve",
        "heather-lilac-tee",
        "black-zip-hoodie",
        "rust-striped-tee",
    ]
    assert len(set(defaults)) == len(scenario_keys())


def test_wardrobe_can_vary_independently_from_scenario():
    scenario = get_scenario("bedroom-seated")
    hoodie_prompt = scenario.scene_prompt()
    knit_prompt = scenario.scene_prompt("cream-knit")

    assert get_wardrobe("charcoal-hoodie").prompt in hoodie_prompt
    assert get_wardrobe("cream-knit").prompt in knit_prompt
    assert hoodie_prompt != knit_prompt
    assert wardrobe_keys() == (
        "charcoal-hoodie",
        "washed-navy-tee",
        "forest-sweatshirt",
        "olive-street-layers",
        "cream-knit",
        "burgundy-long-sleeve",
        "heather-lilac-tee",
        "black-zip-hoodie",
        "rust-striped-tee",
    )


def test_hand_to_face_scenarios_explicitly_allow_the_prompted_hand_entry():
    assert get_scenario("bed-facepalm").allow_hand_entry is True
    assert get_scenario("kitchen-cover-mouth").allow_hand_entry is True
    assert get_scenario("chair-confused-rise").allow_hand_entry is False


def test_action_scenarios_keep_camera_hand_separate_from_free_gesture_hand():
    for key in ("chair-confused-rise", "bed-facepalm", "kitchen-cover-mouth"):
        scene = get_scenario(key).scene_prompt().lower()
        motion = get_scenario(key).motion_prompt().lower()
        assert "free hand" in scene
        assert "free hand" in motion
        assert "phone" in scene
        assert "phone" in motion

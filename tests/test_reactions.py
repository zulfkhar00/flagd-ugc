from flagd_ugc.reactions import get_reaction, reaction_keys


def test_high_arousal_reaction_catalog_is_distinct_and_silent():
    assert reaction_keys() == (
        "silent-gasp",
        "suppressed-laugh",
        "skeptical-cringe",
        "caught-off-guard",
        "shock-cover-mouth",
        "facepalm-disbelief",
        "delighted-hand-chest",
        "no-way-head-shake",
    )
    prompts = [get_reaction(key).motion_prompt().lower() for key in reaction_keys()]
    assert len(set(prompts)) == len(reaction_keys())
    for prompt in prompts:
        assert "three-second" in prompt
        assert "no cut" in prompt
        assert "no speech" in prompt
        assert "lip-sync" in prompt
        assert "silent mouthing" in prompt
        assert "phoneme" in prompt
        assert "preserve her exact identity" in prompt
        assert "she continues holding the front-facing phone herself" in prompt
        assert "camera motion stays physically coupled" in prompt


def test_reactions_declare_mouth_motion_policy():
    assert get_reaction("silent-gasp").mouth_motion == "single-open-hold"
    assert get_reaction("suppressed-laugh").mouth_motion == "single-laugh-cycle"
    assert get_reaction("skeptical-cringe").mouth_motion == "single-close-hold"
    assert get_reaction("caught-off-guard").mouth_motion == "locked"
    assert get_reaction("shock-cover-mouth").mouth_motion == "locked-then-covered"
    assert get_reaction("facepalm-disbelief").mouth_motion == "single-close-hold"
    assert get_reaction("delighted-hand-chest").mouth_motion == "single-open-close"
    assert get_reaction("no-way-head-shake").mouth_motion == "locked"


def test_only_hand_action_reactions_allow_hand_entry():
    hand_actions = {
        "shock-cover-mouth",
        "facepalm-disbelief",
        "delighted-hand-chest",
    }
    for key in reaction_keys():
        assert get_reaction(key).allow_hand_entry is (key in hand_actions)

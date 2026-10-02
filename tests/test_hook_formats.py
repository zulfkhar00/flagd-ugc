from datetime import date, timedelta
from pathlib import Path

import pytest

from flagd_ugc.assets import Demo
from flagd_ugc.errors import QualityError
from flagd_ugc.hook_formats import (
    assign_hook_formats,
    caption_plan,
    format_supports_demo,
    validate_formatted_hook,
)
from flagd_ugc.hooks import validate_hook


def _demos() -> list[Demo]:
    return [
        Demo(Path(f"/{ordinal:02d}-demo.mp4"), description)
        for ordinal, description in enumerate(
            (
                "The user explores practice partners.",
                "The user practises a text opener.",
                "The app explains why a simulated date went poorly.",
                "The user browses conversation drills.",
                "The app gives an immediate Nailed it result.",
                "The app explains a coffee-shop opener score.",
                "The Progress screen shows an overall conversation score of 43 out of 100.",
                "The app explains why a simulated date went well.",
            ),
            start=1,
        )
    ]


def test_daily_assignment_uses_four_distinct_formats_and_keeps_scorecard_grounded():
    assignments = assign_hook_formats(_demos(), 4, date(2026, 9, 18))

    assert [format_definition.key for _, format_definition in assignments] == [
        "scorecard",
        "inner-thought",
        "pov",
        "quote-receipt",
    ]
    assert len({format_definition.key for _, format_definition in assignments}) == 4
    scorecard_demo = assignments[0][0]
    assert "score of 43 out of 100" in scorecard_demo.description


def test_scorecard_rejects_an_invented_or_ungrounded_number():
    description = "The Progress screen shows an overall conversation score of 43 out of 100."
    assert (
        validate_hook(
            "rating the chat: 43/100",
            hook_format="scorecard",
            demo_description=description,
        )
        == "rating the chat: 43/100"
    )
    with pytest.raises(QualityError, match="exactly match"):
        validate_formatted_hook(
            "rating the chat: 42/100",
            "scorecard",
            demo_description=description,
        )
    with pytest.raises(QualityError, match="numeric score"):
        validate_formatted_hook(
            "the 42/100 explained a lot",
            "direct-observation",
            demo_description=description,
        )


@pytest.mark.parametrize(
    ("description", "supported"),
    (
        ("The Discover screen presents practice partners for selection.", False),
        ("A user practises a text opener and receives in-character replies.", False),
        ("The user browses quick conversation drills.", False),
        ("The app gives an immediate Nailed it result.", True),
        ("The app scores a coffee-shop opener and explains the question.", True),
        ("The Progress screen shows an overall conversation score of 43 out of 100.", True),
        ("After a simulated date goes poorly, the app suggests a follow-up.", True),
        ("After a simulated date goes well, the app explains what worked.", True),
        ("The app gives feedback after a practice date.", True),
    ),
)
def test_inner_thought_requires_a_feedback_or_result_demo(description, supported):
    assert format_supports_demo("inner-thought", description) is supported
    if supported:
        assert validate_formatted_hook(
            "i did not expect that feedback",
            "inner-thought",
            demo_description=description,
        ) == "i did not expect that feedback"
    else:
        with pytest.raises(QualityError, match="visible feedback or a result"):
            validate_formatted_hook(
                "i did not expect that feedback",
                "inner-thought",
                demo_description=description,
            )


def test_october_first_inner_thought_is_not_assigned_to_discover():
    assignments = assign_hook_formats(_demos(), 4, date(2026, 10, 1))

    assert [fmt.key for _, fmt in assignments] == [
        "reaction-meme", "scorecard", "inner-thought", "pov"
    ]
    inner_thought_demo = assignments[2][0]
    assert inner_thought_demo.path.name != "01-demo.mp4"
    assert format_supports_demo("inner-thought", inner_thought_demo.description)
    assert len({demo.path for demo, _ in assignments}) == 4


def test_daily_rotation_keeps_feedback_and_scores_grounded_throughout_month():
    for day in range(31):
        run_date = date(2026, 10, 1) + timedelta(days=day)
        assignments = assign_hook_formats(_demos(), 4, run_date)
        assert len(assignments) == 4
        assert len({demo.path for demo, _ in assignments}) == 4
        assert len({fmt.key for _, fmt in assignments}) == 4
        assert all(format_supports_demo(fmt.key, demo.description) for demo, fmt in assignments)
        assert assignments == assign_hook_formats(_demos(), 4, run_date)


def test_rotation_skips_result_formats_when_only_browsing_demos_are_available():
    demos = [
        Demo(Path(f"/{ordinal}.mp4"), "The user browses practice partners.")
        for ordinal in range(4)
    ]
    assignments = assign_hook_formats(demos, 4, date(2026, 10, 1))

    assert len(assignments) == 4
    assert not {"scorecard", "inner-thought"} & {fmt.key for _, fmt in assignments}


@pytest.mark.parametrize(
    ("hook", "hook_format"),
    (
        ("pov: he thought he had game until this", "pov"),
        ("i'm good at texting", "quote-receipt"),
        ("when he says he's good at texting", "when"),
        ("the score explained a lot", "direct-observation"),
        ("my face when i got the feedback", "reaction-meme"),
        ("i did not expect that feedback", "inner-thought"),
    ),
)
def test_each_non_numeric_format_has_a_strict_accepted_grammar(hook, hook_format):
    assert validate_formatted_hook(hook, hook_format) == hook


def test_quote_and_scorecard_get_structured_caption_plans():
    quote = caption_plan("i'm good at texting", "quote-receipt")
    scorecard = caption_plan("rating the chat: 43/100", "scorecard")

    assert [line.text for line in quote.lines] == ["“i'm good at texting”", "— him"]
    assert quote.lines[1].font_scale < quote.lines[0].font_scale
    assert [line.text for line in scorecard.lines] == ["rating the chat:", "43/100"]
    assert scorecard.lines[1].font_scale > scorecard.lines[0].font_scale

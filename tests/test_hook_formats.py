from datetime import date
from pathlib import Path

import pytest

from flagd_ugc.assets import Demo
from flagd_ugc.errors import QualityError
from flagd_ugc.hook_formats import (
    assign_hook_formats,
    caption_plan,
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

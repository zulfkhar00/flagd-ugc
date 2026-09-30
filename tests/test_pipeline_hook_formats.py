from datetime import date
from pathlib import Path

from flagd_ugc.assets import Demo
from flagd_ugc.config import Settings
from flagd_ugc.pipeline import Pipeline
from flagd_ugc.opening_director import get_opening_direction


class FormatAwareGemini:
    calls = 0

    def generate_hooks(self, demos, *, formats):
        self.calls += 1
        samples = {
            "pov": "pov: the feedback explained it",
            "quote-receipt": "i'm good at texting",
            "when": "when i get the feedback",
            "direct-observation": "the score explained a lot",
            "reaction-meme": "my face when i got the feedback",
            "scorecard": "rating the chat: 43/100",
            "inner-thought": "i did not expect that feedback",
        }
        titles = {
            "pov": "the practice date result",
            "quote-receipt": "his texting confidence",
            "when": "the feedback moment",
            "direct-observation": "the conversation feedback",
            "reaction-meme": "the date feedback",
            "scorecard": "the 43 conversation score",
            "inner-thought": "the unexpected feedback",
        }
        return [
            {
                "demo_path": str(demo.path),
                "hook_format": format_definition.key,
                "hook": samples[format_definition.key],
                "title": titles[format_definition.key],
            }
            for demo, format_definition in zip(demos, formats, strict=True)
        ]


def _demos() -> list[Demo]:
    return [
        Demo(Path(f"/{ordinal:02d}.mp4"), description)
        for ordinal, description in enumerate(
            (
                "The user explores practice partners.",
                "The user practises a text opener.",
                "The app gives feedback after a simulated date.",
                "The user browses practice drills.",
                "The app gives an immediate positive result.",
                "The app explains an opener score.",
                "The Progress screen shows an overall conversation score of 43 out of 100.",
                "The app gives feedback after a positive simulated date.",
            ),
            start=1,
        )
    ]


def test_hook_formats_are_persisted_and_never_regenerated_on_resume(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        font_file=tmp_path / "font.ttf",
    )
    gemini = FormatAwareGemini()
    pipeline = Pipeline(settings, gemini=gemini, kling=object(), composer=object())
    batch = pipeline.database.get_or_create_batch(date(2026, 9, 18), 4)

    first = pipeline._ensure_hooks(int(batch["id"]), 4, _demos(), date(2026, 9, 18))
    second = pipeline._ensure_hooks(int(batch["id"]), 4, _demos(), date(2026, 9, 18))

    assert gemini.calls == 1
    assert [row["hook_format"] for row in first] == [
        "scorecard",
        "inner-thought",
        "pov",
        "quote-receipt",
    ]
    assert [row["hook_format"] for row in second] == [
        "scorecard",
        "inner-thought",
        "pov",
        "quote-receipt",
    ]
    assert [row["title"] for row in first] == [
        "the 43 conversation score",
        "the unexpected feedback",
        "the practice date result",
        "his texting confidence",
    ]
    assert len({row["opening_direction"] for row in first}) == 4
    assert len(
        {get_opening_direction(row["opening_direction"]).task_key for row in first}
    ) == 4
    assert [row["opening_direction"] for row in first] == [
        row["opening_direction"] for row in second
    ]

    next_date = date(2026, 9, 19)
    next_batch = pipeline.database.get_or_create_batch(next_date, 4)
    next_day = pipeline._ensure_hooks(
        int(next_batch["id"]), 4, _demos(), next_date
    )
    today_by_format = {row["hook_format"]: row for row in first}
    next_day_by_format = {row["hook_format"]: row for row in next_day}
    assert gemini.calls == 2
    assert all(
        get_opening_direction(today_by_format[hook_format]["opening_direction"]).task_key
        != get_opening_direction(next_day_by_format[hook_format]["opening_direction"]).task_key
        for hook_format in today_by_format.keys() & next_day_by_format.keys()
    )

from datetime import date
from pathlib import Path

import pytest

from flagd_ugc.assets import Demo
from flagd_ugc.config import Settings
from flagd_ugc.pipeline import Pipeline
from flagd_ugc.errors import QualityError
from flagd_ugc.hook_formats import get_hook_format
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
                "reaction_key": "cringing" if format_definition.key == "inner-thought" else "shocked",
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
        "reaction-meme",
        "inner-thought",
        "direct-observation",
        "pov",
    ]
    assert [row["hook_format"] for row in second] == [
        "reaction-meme",
        "inner-thought",
        "direct-observation",
        "pov",
    ]
    assert [row["title"] for row in first] == [
        "the date feedback",
        "the unexpected feedback",
        "the conversation feedback",
        "the practice date result",
    ]
    assert len({row["opening_direction"] for row in first}) == 4
    assert len(
        {get_opening_direction(row["opening_direction"]).reaction_scene_key for row in first}
    ) == 4
    assert all(get_opening_direction(row["opening_direction"]).task_key is None for row in first)
    thought = next(row for row in first if row["hook_format"] == "inner-thought")
    assert get_opening_direction(thought["opening_direction"]).reaction_key == "cringing"
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
        get_opening_direction(today_by_format[hook_format]["opening_direction"]).reaction_scene_key
        != get_opening_direction(next_day_by_format[hook_format]["opening_direction"]).reaction_scene_key
        for hook_format in today_by_format.keys() & next_day_by_format.keys()
    )


@pytest.mark.parametrize("reaction_key", [None, "invented-emotion", "surprised"])
def test_planner_boundary_requires_an_approved_reaction_before_media_generation(reaction_key):
    demo = _demos()[2]
    item = {
        "demo_path": str(demo.path),
        "hook_format": "when",
        "hook": "when i get the feedback",
        "title": "the practice date feedback",
    }
    if reaction_key is not None:
        item["reaction_key"] = reaction_key
    with pytest.raises(QualityError, match="required fields|unknown daily hook reaction|archived"):
        Pipeline._validate_assigned_hooks([item], [(demo, get_hook_format("when"))])


def test_already_planned_task_batch_resumes_without_switching_to_a_reaction(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )
    gemini = FormatAwareGemini()
    pipeline = Pipeline(settings, gemini=gemini, kling=object(), composer=object())
    batch = pipeline.database.get_or_create_batch(date(2026, 10, 1), 1)
    direction_key = "task-pov-bedroom-make-bed-v1"
    pipeline.database.create_videos(batch["id"], [{
        "demo_path": str(_demos()[2].path),
        "hook_format": "pov",
        "hook": "pov: the feedback explained it",
        "title": "the practice date feedback",
        "opening_direction": direction_key,
    }])

    videos = pipeline._ensure_hooks(batch["id"], 1, _demos(), date(2026, 10, 1))

    assert gemini.calls == 0
    assert videos[0]["opening_direction"] == direction_key
    assert pipeline._opening_direction_for_video(videos[0]).task_key == "bedroom-make-bed"


@pytest.mark.parametrize("resume_failed_stage", [False, True])
def test_rejected_hook_plan_receives_feedback_before_persisting_corrected_reaction(
    tmp_path, resume_failed_stage
):
    reason = "Item 4: disbelief does not match timer pressure; use flustered."

    class CorrectingGemini(FormatAwareGemini):
        feedback = []
        assignments = []

        def generate_hooks(self, demos, *, formats, review_feedback=None):
            self.feedback.append(review_feedback)
            self.assignments.append([(str(d.path), f.key) for d, f in zip(demos, formats)])
            if review_feedback is None:
                raise QualityError(reason)
            items = super().generate_hooks(demos, formats=formats)
            items[-1]["reaction_key"] = "flustered"
            return items

    gemini = CorrectingGemini()
    pipeline = Pipeline(
        Settings(
            data_dir=tmp_path, quality_retries=2, gemini_api_key="", kling_api_key="",
            telegram_bot_token="", telegram_chat_id="",
        ),
        gemini=gemini, kling=object(), composer=object(),
    )
    run_date = date(2026, 10, 3)
    batch = pipeline.database.get_or_create_batch(run_date, 4)
    if resume_failed_stage:
        pipeline.database.start_batch_stage(batch["id"], "hooks")
        pipeline.database.fail_batch_stage(batch["id"], "hooks", reason)

    rows = pipeline._ensure_hooks(batch["id"], 4, _demos(), run_date)

    assert gemini.feedback == ([reason] if resume_failed_stage else [None, reason])
    assert all(assignment == gemini.assignments[0] for assignment in gemini.assignments)
    assert len(pipeline.database.list_videos(batch["id"])) == 4
    assert get_opening_direction(rows[-1]["opening_direction"]).reaction_key == "flustered"
    stage = pipeline.database.get_batch_stage(batch["id"], "hooks")
    assert stage["status"] == "complete"
    assert stage["attempts"] == 2
    assert stage["error"] is None


def test_repeated_hook_rejection_stays_bounded_and_creates_no_media_plan(tmp_path):
    class RejectingGemini:
        feedback = []

        def generate_hooks(self, demos, *, formats, review_feedback=None):
            self.feedback.append(review_feedback)
            raise QualityError("reaction still contradicts the demo")

    gemini = RejectingGemini()
    pipeline = Pipeline(
        Settings(
            data_dir=tmp_path, quality_retries=2, gemini_api_key="", kling_api_key="",
            telegram_bot_token="", telegram_chat_id="",
        ),
        gemini=gemini, kling=object(), composer=object(),
    )
    run_date = date(2026, 10, 3)
    batch = pipeline.database.get_or_create_batch(run_date, 4)

    with pytest.raises(QualityError, match="reaction still contradicts"):
        pipeline._ensure_hooks(batch["id"], 4, _demos(), run_date)

    assert gemini.feedback == [None, "reaction still contradicts the demo", "reaction still contradicts the demo"]
    assert pipeline.database.list_videos(batch["id"]) == []
    stage = pipeline.database.get_batch_stage(batch["id"], "hooks")
    assert stage["attempts"] == 3
    assert stage["status"] == "failed"

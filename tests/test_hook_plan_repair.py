import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from flagd_ugc.assets import Demo
from flagd_ugc.config import Settings
from flagd_ugc.errors import HookReviewError, QualityError
from flagd_ugc.hook_formats import get_hook_format
from flagd_ugc.opening_director import get_opening_direction
from flagd_ugc.pipeline import Pipeline
from flagd_ugc.providers import GeminiProvider


def _draft(tmp_path):
    descriptions = [
        "The Progress screen shows an overall conversation score of 43 out of 100.",
        "The app gives negative feedback after a simulated date.",
        "The app gives an immediate positive opener exercise result.",
        "The user browses a list of conversation drills and opens a coffee shop scenario.",
    ]
    demos = [Demo(tmp_path / f"{i}.mp4", text) for i, text in enumerate(descriptions)]
    formats = [get_hook_format(key) for key in ("pov", "when", "reaction-meme", "direct-observation")]
    hooks = [
        "pov: i thought my score would be higher",
        "when the practice feedback calls me out",
        "my face when i nail the practice opener",
        "overthinking which opener practice scenario to pick first",
    ]
    titles = [
        "the conversation score breakdown", "the simulated date feedback",
        "the positive opener exercise result", "browsing the conversation drills",
    ]
    reactions = ["shocked", "cringing", "excited", "laughing"]
    proposal = [
        {"demo_path": str(demo.path), "hook_format": fmt.key, "hook": hook,
         "title": title, "reaction_key": reaction}
        for demo, fmt, hook, title, reaction in zip(demos, formats, hooks, titles, reactions)
    ]
    return demos, formats, proposal


def test_only_rejected_browsing_hook_is_regenerated_and_reviewed(tmp_path):
    demos, formats, proposal = _draft(tmp_path)
    requests = []
    replacement = dict(proposal[-1], reaction_key="flustered")

    def handler(request):
        prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        requests.append(prompt)
        if len(requests) == 1:
            result = [GeminiProvider._hook_response_item(item) for item in proposal]
        elif len(requests) == 2:
            result = {"items": [
                {"demo_file": demo.path.name, "passed": i != 3,
                 "reason": "browsing is not inherently amusing; use flustered" if i == 3 else "ok"}
                for i, demo in enumerate(demos)
            ]}
        elif len(requests) == 3:
            result = [GeminiProvider._hook_response_item(replacement)]
        else:
            result = {"items": [{"demo_file": "3.mp4", "passed": True, "reason": "ok"}]}
        return httpx.Response(200, json={"candidates": [{"content": {
            "parts": [{"text": json.dumps(result)}]
        }}]})

    provider = GeminiProvider(
        "secret", text_model="test", image_model="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(HookReviewError) as caught:
        provider.generate_hooks(demos, formats=formats)
    rejection = caught.value
    assert rejection.proposal == proposal
    assert rejection.rejected_demo_files == ["3.mp4"]

    result = provider.generate_hooks(
        demos, formats=formats, review_feedback=str(rejection),
        previous_hooks=rejection.proposal,
        rejected_demo_files=rejection.rejected_demo_files,
    )

    assert result[:3] == proposal[:3]
    assert result[-1] == replacement
    pending = json.loads(requests[2].split("Approved demonstrations:\n", 1)[1].split("\n\n", 1)[0])
    assert [item["file"] for item in pending] == ["3.mp4"]
    reviewed = json.loads(requests[3].split("Items: ", 1)[1])
    assert [item["demo_file"] for item in reviewed] == ["3.mp4"]
    assert "browsing is not inherently amusing; use flustered" in requests[2]
    assert len(requests) == 4


@pytest.mark.parametrize("decisions", [
    [],
    [{"demo_file": "3.mp4", "passed": True}, {"demo_file": "3.mp4", "passed": True}],
    [{"demo_file": "other.mp4", "passed": True}, {"demo_file": "3.mp4", "passed": True}],
    [{"demo_file": "2.mp4", "passed": "true"}, {"demo_file": "3.mp4", "passed": True}],
])
def test_incomplete_or_invalid_review_cannot_approve_a_plan(tmp_path, decisions):
    demos, _, proposal = _draft(tmp_path)
    provider = GeminiProvider("secret", text_model="test", image_model="test")
    provider._text_json = lambda prompt: {"items": decisions}
    with pytest.raises(QualityError, match="every supplied demo|invalid or duplicate"):
        provider.review_hooks(proposal[2:], {demo.path.name: demo for demo in demos})


@pytest.mark.parametrize("restart", [False, True])
def test_rejected_draft_is_saved_and_approved_items_survive_retry_or_restart(tmp_path, restart):
    demos, _, _ = _draft(tmp_path)
    calls = []

    class PartialReviewGemini:
        def generate_hooks(self, chosen, *, formats, review_feedback=None,
                           previous_hooks=None, rejected_demo_files=None):
            calls.append((previous_hooks, rejected_demo_files, review_feedback))
            if previous_hooks is None:
                samples = {
                    "pov": "pov: i did not expect that feedback",
                    "when": "when i'm choosing a practice scenario",
                    "reaction-meme": "my face when i get practice feedback",
                    "inner-thought": "i did not expect that feedback",
                    "direct-observation": "overthinking which practice scenario to choose",
                }
                draft = [
                    {"demo_path": str(d.path), "hook_format": f.key,
                     "hook": samples[f.key],
                     "title": f"the practice demo {('first', 'second', 'third', 'fourth')[i]}",
                     "reaction_key": "laughing" if i == 3 else "shocked"}
                    for i, (d, f) in enumerate(zip(chosen, formats))
                ]
                raise HookReviewError(
                    "browsing is not amusing; use flustered", proposal=draft,
                    rejected_demo_files=[chosen[-1].path.name],
                )
            assert rejected_demo_files == [chosen[-1].path.name]
            assert review_feedback == "browsing is not amusing; use flustered"
            return [dict(item, reaction_key="flustered") if i == 3 else dict(item)
                    for i, item in enumerate(previous_hooks)]

    settings = Settings(
        data_dir=tmp_path, quality_retries=0 if restart else 2,
        gemini_api_key="", kling_api_key="", telegram_bot_token="", telegram_chat_id="",
    )
    run_date = date(2026, 10, 5)
    pipeline = Pipeline(settings, gemini=PartialReviewGemini(), kling=object(), composer=object())
    batch = pipeline.database.get_or_create_batch(run_date, 4)
    if restart:
        with pytest.raises(HookReviewError):
            pipeline._ensure_hooks(batch["id"], 4, demos, run_date)
        assert pipeline.database.list_videos(batch["id"]) == []
        saved = json.loads(pipeline.database.get_batch_stage(batch["id"], "hooks")["metadata_json"])
        assert len(saved["draft_hooks"]) == 4
        assert len(saved["rejected_demo_files"]) == 1
        pipeline = Pipeline(settings, gemini=PartialReviewGemini(), kling=object(), composer=object())

    rows = pipeline._ensure_hooks(batch["id"], 4, demos, run_date)

    assert len(calls) == 2
    reviewed_draft = calls[-1][0]
    assert [row["hook"] for row in rows] == [item["hook"] for item in reviewed_draft]
    assert [row["title"] for row in rows] == [item["title"] for item in reviewed_draft]
    assert get_opening_direction(rows[-1]["opening_direction"]).reaction_key == "flustered"
    stage = pipeline.database.get_batch_stage(batch["id"], "hooks")
    assert stage["status"] == "complete"
    assert stage["attempts"] == 2
    assert "draft_hooks" not in json.loads(stage["metadata_json"])

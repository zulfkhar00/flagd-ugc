import asyncio
import json

import pytest

from flagd_phone.authorization import AuthorizationState
from flagd_phone.runner import Journal, run_agent, validate_task
from flagd_phone.vision import Frame, validate_action
from test_agent import FakePortal, decision, png64


def reel_task():
    return validate_task({
        "goal": "Publish one Reel with music", "mode": "publish", "max_steps": 8,
        "text_fields": {"caption": "Exact caption"},
        "authorization": {"app": "Instagram", "account": "try.flagd", "limits": {"publish": 1},
                          "verification": {"account": False, "preview": False}},
        "publication": {"source_id": "source-reel", "slide_count": 6,
                        "required_text_fields": ["caption"], "media_format": "reel", "require_music": True},
    })


def media(frame, **changes):
    return decision(frame, "confirm_media", **{
        "media_format": "reel", "selected_sound": "Visible song title", "music_enabled": True,
        "evidence": "Reel composer and the applied song name, with music volume above zero.", **changes,
    })


def test_waiving_extra_preview_checks_cannot_allow_reel_without_music_confirmation():
    frame = Frame.from_portal(png64())
    state = AuthorizationState(reel_task())
    state.record_after_input(decision(frame, "type_text", text_key="caption"))
    publish = decision(frame, x=10, y=20, effect="publish")
    with pytest.raises(ValueError, match="media format and music"):
        state.check(publish)
    checked = media(frame)
    validate_action(checked, frame, {})
    state.check(checked)
    state.record_before_input(checked)
    state.check(publish)
    state.record_before_input(publish)
    with pytest.raises(ValueError, match="authorization"):
        state.check(publish)


@pytest.mark.parametrize("changes", [
    {"media_format": "photo_carousel"}, {"music_enabled": False},
    {"selected_sound": ""}, {"selected_sound": "No music"},
])
def test_incorrect_format_or_silent_media_cannot_unlock_posting(changes):
    frame = Frame.from_portal(png64())
    state = AuthorizationState(reel_task())
    with pytest.raises(ValueError):
        state.check(media(frame, **changes))
    assert state.media_confirmed is False and state.used["publish"] == 0


def test_editing_or_navigating_requires_reconfirmation_before_share():
    frame = Frame.from_portal(png64())
    state = AuthorizationState(reel_task())
    state.record_after_input(decision(frame, "type_text", text_key="caption"))
    state.record_before_input(media(frame))
    state.record_before_input(decision(frame, x=20, y=30))
    assert state.context()["last_media_observation"]["selected_sound"] == "Visible song title"
    with pytest.raises(ValueError, match="media format and music"):
        state.check(decision(frame, x=20, y=30, effect="publish"))


@pytest.mark.parametrize("change", [{"media_format": ""}, {"media_format": 1}, {"require_music": "true"}])
def test_bad_media_requirements_fail_before_execution(change):
    task = reel_task()
    task["publication"].update(change)
    with pytest.raises(ValueError):
        validate_task(task)


def test_reel_execution_records_actual_music_and_sends_only_one_publish(tmp_path):
    async def scenario():
        class Model:
            def __init__(self):
                self.calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                actions = [
                    decision(frame, "type_text", text_key="caption"),
                    media(frame),
                    decision(frame, x=20, y=30, effect="publish"),
                    decision(frame, "done", published_account="try.flagd"),
                ]
                return actions[self.calls - 1], {"usage": {"totalTokenCount": 10}}

        portal, model = FakePortal(), Model()
        journal = Journal(tmp_path / "reel")
        result = await run_agent(portal, model, reel_task(), journal, execute=True)
        assert result["status"] == "completion_candidate"
        assert result["public_action_attempts"]["publish"] == 1
        assert result["confirmed_media_format"] == "reel"
        assert result["selected_sound"] == "Visible song title"
        assert [method for method, _, _ in portal.calls].count("tap") == 1
        assert "confirm_media" not in [method for method, _, _ in portal.calls]
        trace = [json.loads(line) for line in journal.path.read_text().splitlines()]
        checkpoint = next(index for index, entry in enumerate(trace) if entry.get("action", {}).get("kind") == "confirm_media")
        publication = next(index for index, entry in enumerate(trace) if entry.get("action", {}).get("effect") == "publish")
        assert checkpoint < publication
    asyncio.run(scenario())


def test_unnamed_feed_after_share_does_not_finish_publication(tmp_path):
    async def scenario():
        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 5:
                    assert "must never be repeated" in task["execution_context"]["decision_rejection"]
                actions = [
                    decision(frame, "type_text", text_key="caption"),
                    media(frame),
                    decision(frame, x=20, y=30, effect="publish"),
                    decision(frame, "done", published_account=""),
                    decision(frame, x=40, y=50),  # Inspect own profile after Share.
                    decision(frame, "done", published_account="try.flagd"),
                ]
                return actions[self.calls - 1], {}

        portal = FakePortal()
        result = await run_agent(portal, Model(), reel_task(),
                                 Journal(tmp_path / "reel"), execute=True)
        assert result["status"] == "completion_candidate"
        assert result["publication_account"] == "try.flagd"
        assert result["public_action_attempts"]["publish"] == 1
        assert [method for method, _, _ in portal.calls].count("tap") == 2

    asyncio.run(scenario())


def test_rejected_share_can_reconfirm_after_next_without_dispatching_early(tmp_path):
    async def scenario():
        class Model:
            def __init__(self):
                self.calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                actions = [
                    decision(frame, "type_text", text_key="caption"),
                    media(frame),
                    decision(frame, x=20, y=30),  # Next invalidates the checkpoint.
                    decision(frame, x=30, y=40, effect="publish"),  # Rejected, never dispatched.
                    media(frame),
                    decision(frame, x=30, y=40, effect="publish"),
                    decision(frame, "done", published_account="try.flagd"),
                ]
                if self.calls == 5:
                    assert "confirm the requested media format" in task["execution_context"]["decision_rejection"]
                    assert task["execution_context"]["remaining"]["publish"] == 1
                return actions[self.calls-1], {"usage": {"totalTokenCount": 10}}

        portal, model = FakePortal(), Model()
        journal = Journal(tmp_path / "reel")
        result = await run_agent(portal, model, reel_task(), journal, execute=True)
        assert result["status"] == "completion_candidate"
        assert result["public_action_attempts"]["publish"] == 1
        assert result["model_calls"] == 7
        assert [method for method, _, _ in portal.calls].count("tap") == 2  # Next and one Share.
        trace = [json.loads(line) for line in journal.path.read_text().splitlines()]
        rejected = [e for e in trace if e["event"] == "authorization_rejected"]
        assert len(rejected) == 1 and rejected[0]["input_executed"] is False
    asyncio.run(scenario())


def test_repeated_missing_checkpoint_stops_without_publishing(tmp_path):
    async def scenario():
        class Model:
            async def decide(self, task, frame, history):
                return decision(frame, x=20, y=30, effect="publish"), {}
        portal = FakePortal()
        result = await run_agent(portal, Model(), reel_task(), Journal(tmp_path / "reel"), execute=True)
        assert result["status"] == "blocked"
        assert all(method == "screenshot" for method, _, _ in portal.calls)
        result = json.loads((tmp_path / "reel" / "report.json").read_text())["result"]
        assert result["model_calls"] == 3
        assert result["public_action_attempts"]["publish"] == 0
    asyncio.run(scenario())


def test_recoverable_handoff_returns_to_agent_without_guessing_a_ui_action(tmp_path):
    async def scenario():
        class Model:
            calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, "handoff", reason="Informational audio notice"), {}
                if self.calls == 2:
                    assert "You proposed a handoff" in task["execution_context"]["decision_rejection"]
                    return decision(frame, "type_text", text_key="caption"), {}
                if self.calls == 3:
                    return media(frame), {}
                if self.calls == 4:
                    return decision(frame, x=20, y=30, effect="publish"), {}
                return decision(frame, "done", published_account="try.flagd"), {}
        portal = FakePortal()
        result = await run_agent(portal, Model(), reel_task(), Journal(tmp_path / "reel"), execute=True)
        assert result["status"] == "completion_candidate"
        assert result["model_calls"] == 5
        assert result["public_action_attempts"]["publish"] == 1
        assert [method for method, _, _ in portal.calls].count("tap") == 1
    asyncio.run(scenario())


def test_handoff_reconsideration_is_bounded_and_does_not_relax_music(tmp_path):
    async def scenario():
        class Model:
            async def decide(self, task, frame, history):
                return decision(frame, "handoff", reason="Actual missing operator input"), {}
        portal = FakePortal()
        result = await run_agent(portal, Model(), reel_task(), Journal(tmp_path / "reel"), execute=True)
        assert result["status"] == "blocked"
        assert result["model_calls"] == 3
        assert result["public_action_attempts"]["publish"] == 0
        assert all(method == "screenshot" for method, _, _ in portal.calls)
    asyncio.run(scenario())

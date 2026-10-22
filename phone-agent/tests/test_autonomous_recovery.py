import asyncio
import base64
import io
import json

import pytest
from PIL import Image

from flagd_phone.protocol import UncertainAction
from flagd_phone.runner import Journal, execute_action, run_agent
from flagd_phone.vision import Frame, validate_action
from test_agent import FakePortal, decision, png64
from test_media_requirements import media, reel_task


def test_reviewed_popup_returns_to_agent_and_does_not_dispatch_the_flagged_input(tmp_path):
    async def scenario():
        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, x=10, y=20, requires_review=True,
                                    reason="Unfamiliar popup"), {}
                if self.calls == 2:
                    assert "autonomous" in task["execution_context"]["decision_rejection"]
                    return decision(frame, x=30, y=40, reason="Use the visible close control"), {}
                return decision(frame, "done"), {}

        portal = FakePortal()
        result = await run_agent(portal, Model(), {"goal": "Close the popup", "max_steps": 6},
                                 Journal(tmp_path / "popup"), execute=True)
        taps = [params for method, params, _ in portal.calls if method == "tap"]
        assert result["status"] == "completion_candidate"
        assert len(taps) == 1
        frame = Frame.from_portal(png64())
        assert taps[0] == dict(zip(("x", "y"), frame.to_device(30, 40)))
    asyncio.run(scenario())


def test_separate_recoverable_handoffs_each_get_a_new_opportunity(tmp_path):
    async def scenario():
        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls in (1, 3):
                    return decision(frame, "handoff", requires_review=True,
                                    reason="An ordinary editor is unfinished"), {}
                if self.calls in (2, 4):
                    assert task["execution_context"].get("decision_rejection")
                    return decision(frame, x=self.calls * 30, y=40), {}
                return decision(frame, "done"), {}

        portal = FakePortal()
        result = await run_agent(portal, Model(), {"goal": "Complete the editors", "max_steps": 8},
                                 Journal(tmp_path / "editors"), execute=True)
        assert result["status"] == "completion_candidate"
        assert sum(method == "tap" for method, _, _ in portal.calls) == 2
    asyncio.run(scenario())


def test_checkpoint_recovery_resets_after_progress_without_allowing_an_early_post(tmp_path):
    async def scenario():
        task = reel_task()
        task["max_steps"] = 12
        task["authorization"]["verification"]["account"] = True

        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                actions = [
                    media(frame),  # Rejected until account is known.
                    decision(frame, "confirm_account", app="Instagram", account="try.flagd",
                             evidence="@try.flagd visible"),
                    decision(frame, "type_text", text_key="caption"),
                    decision(frame, x=30, y=40, effect="publish"),  # Missing music checkpoint.
                    media(frame),
                    decision(frame, x=50, y=60),  # Navigation invalidates music checkpoint.
                    decision(frame, x=30, y=40, effect="publish"),
                    media(frame),
                    decision(frame, x=30, y=40, effect="publish"),
                    decision(frame, "done"),
                ]
                return actions[self.calls - 1], {}

        portal = FakePortal()
        journal = Journal(tmp_path / "checkpoints")
        result = await run_agent(portal, Model(), task, journal, execute=True)
        assert result["status"] == "completion_candidate"
        assert result["public_action_attempts"]["publish"] == 1
        trace = [json.loads(line) for line in journal.path.read_text().splitlines()]
        assert sum(row["event"] == "authorization_rejected" for row in trace) == 3
        assert sum(row["event"] == "input_intent" and row["action"].get("effect") == "publish"
                   for row in trace) == 1
    asyncio.run(scenario())


def test_small_coordinate_and_status_changes_do_not_hide_a_navigation_loop(tmp_path):
    async def scenario():
        class Portal(FakePortal):
            screenshots = 0

            async def call(self, method, params=None, *, mutation=False):
                if method != "screenshot":
                    return await super().call(method, params, mutation=mutation)
                self.calls.append((method, params, mutation))
                self.screenshots += 1
                image = Image.new("RGB", (1080, 1920), (20, 30, 40))
                image.putpixel((900, 10), (self.screenshots, 255, 255))
                output = io.BytesIO()
                image.save(output, format="PNG")
                return base64.b64encode(output.getvalue()).decode()

        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls <= 3:
                    return decision(frame, x=30 + self.calls * .5, y=50), {}
                assert "screen unchanged" in task["execution_context"]["decision_rejection"]
                return decision(frame, "done"), {}

        portal = Portal()
        result = await run_agent(portal, Model(), {"goal": "Exit camera", "max_steps": 6},
                                 Journal(tmp_path / "loop"), execute=True)
        assert result["status"] == "completion_candidate"
        assert sum(method == "tap" for method, _, _ in portal.calls) == 2
    asyncio.run(scenario())


def test_only_edge_back_navigation_may_repeat_and_has_no_intermediate_model_call(monkeypatch):
    async def scenario():
        frame = Frame.from_portal(png64())
        back = decision(frame, "swipe", x=5, y=500, end_x=200, end_y=500,
                        duration_ms=300, repeat=2)
        validate_action(back, frame, {})
        for bad in (
            {**back, "repeat": 3}, {**back, "x": 150}, {**back, "end_y": 100},
            decision(frame, x=10, y=20, effect="publish", repeat=2),
        ):
            with pytest.raises(ValueError):
                validate_action(bad, frame, {})
        pauses = []

        async def sleep(seconds):
            pauses.append(seconds)

        monkeypatch.setattr("flagd_phone.runner.asyncio.sleep", sleep)
        portal = FakePortal()
        await execute_action(portal, back, frame, {})
        assert [method for method, _, _ in portal.calls] == ["swipe", "swipe"]
        assert pauses[0] == .1
    asyncio.run(scenario())


def test_uncertain_first_back_prevents_the_second_gesture():
    async def scenario():
        class Portal(FakePortal):
            async def call(self, method, params=None, *, mutation=False):
                self.calls.append((method, params, mutation))
                raise UncertainAction("first gesture outcome unknown")

        frame = Frame.from_portal(png64())
        portal = Portal()
        with pytest.raises(UncertainAction):
            await execute_action(portal, decision(frame, "swipe", x=5, y=500, end_x=200,
                                                 end_y=500, repeat=2), frame, {})
        assert len(portal.calls) == 1
    asyncio.run(scenario())

from __future__ import annotations

import asyncio
import json

import pytest

from flagd_phone.cli import build_parser
from flagd_phone.controller import ControlError, TaskController
from flagd_phone.device import ensure_screen_awake, park_phone
from flagd_phone.protocol import PortalError, UncertainAction
from flagd_phone.runner import Journal
from test_controller import ConnectedPortal, NavigationModel, host_with, request, wait_for_job


class ScreenPortal(ConnectedPortal):
    def __init__(self):
        super().__init__()
        self.enabled = True
        self.interactive = True

    async def call(self, method, params=None, *, mutation=False):
        if method in ("screen/keepAwake/set", "screen/keepAwake/status", "global"):
            self.calls.append((method, params, mutation))
            if method == "screen/keepAwake/set":
                self.enabled = params["enabled"]
                if self.enabled:
                    self.interactive = True
                return {"enabled": self.enabled}
            if method == "screen/keepAwake/status":
                return {"enabled": self.enabled, "interactive": self.interactive, "deviceLocked": False}
            if params["action"] == 8:
                assert self.enabled is False  # The watchdog must not re-wake the display.
                self.interactive = False
            return True
        return await super().call(method, params, mutation=mutation)


def test_park_leaves_app_and_locks_then_next_task_can_wake(tmp_path):
    async def scenario():
        portal = ScreenPortal()
        journal = Journal(tmp_path / "cleanup")
        result = await park_phone(portal, journal)
        assert result == {"screen_off": True, "home_dispatched": True, "already_off": False}
        assert portal.calls == [
            ("screen/keepAwake/status", None, False),
            ("global", {"action": 2}, True),
            ("screen/keepAwake/set", {"enabled": False}, True),
            ("screen/keepAwake/status", None, False),
            ("global", {"action": 8}, True),
            ("screen/keepAwake/status", None, False),
        ]
        await ensure_screen_awake(portal, journal)
        assert portal.interactive is True and portal.enabled is True
    asyncio.run(scenario())


def test_already_off_does_not_send_another_home_or_lock(tmp_path):
    async def scenario():
        portal = ScreenPortal()
        portal.interactive = False
        result = await park_phone(portal, Journal(tmp_path / "cleanup"))
        assert result["already_off"] is True
        assert all(method != "global" for method, _, _ in portal.calls)
    asyncio.run(scenario())


@pytest.mark.parametrize("obstacle", ["watchdog", "lock", "uncertain"])
def test_cleanup_failure_cannot_be_reported_as_screen_off(tmp_path, obstacle):
    async def scenario():
        class RefusingPortal(ScreenPortal):
            async def call(self, method, params=None, *, mutation=False):
                value = await super().call(method, params, mutation=mutation)
                if obstacle == "watchdog" and method == "screen/keepAwake/status":
                    value["enabled"] = True
                if method == "global" and params["action"] == 8:
                    if obstacle == "lock":
                        self.interactive = True
                    if obstacle == "uncertain":
                        self.uncertain = True
                        raise UncertainAction("lock outcome unknown")
                return value
        portal = RefusingPortal()
        with pytest.raises(PortalError):
            await park_phone(portal, Journal(tmp_path / "cleanup"), timeout=0)
        trace = [json.loads(line) for line in (tmp_path / "cleanup" / "trace.jsonl").read_text().splitlines()]
        assert not any(event["event"] == "screen_park_finished" for event in trace)
        if obstacle == "watchdog":
            assert not any(method == "global" and params["action"] == 8 for method, params, _ in portal.calls)
    asyncio.run(scenario())


def test_successful_jobs_cleanup_before_releasing_phone_and_wake_next_job(tmp_path):
    async def scenario():
        portal, model = ScreenPortal(), NavigationModel()
        controller = TaskController(host_with(portal), model, tmp_path, keep_awake=True, park_on_finish=True)
        try:
            for goal in ("Read first page", "Read second page"):
                job_id = (await controller.dispatch(request(goal)))["job"]["id"]
                job = await wait_for_job(controller, job_id)
                assert job["result"]["status"] == "completion_candidate"
                assert job["cleanup"]["status"] == "completed"
                assert portal.interactive is False and portal.enabled is False
            assert len(model.goals) == 4  # No inference is spent on device cleanup.
            assert [params["action"] for method, params, _ in portal.calls if method == "global"] == [2, 8, 2, 8]
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_new_task_and_manual_park_blocked_while_completion_cleanup_runs(tmp_path):
    async def scenario():
        locking, release = asyncio.Event(), asyncio.Event()

        class PausingPortal(ScreenPortal):
            async def call(self, method, params=None, *, mutation=False):
                if method == "global" and params["action"] == 8:
                    locking.set()
                    await release.wait()
                return await super().call(method, params, mutation=mutation)

        portal = PausingPortal()
        controller = TaskController(host_with(portal), NavigationModel(), tmp_path, park_on_finish=True)
        try:
            job_id = (await controller.dispatch(request()))["job"]["id"]
            await asyncio.wait_for(locking.wait(), 2)
            assert controller.status()["busy"] is True
            with pytest.raises(ControlError, match="owns the phone"):
                await controller.dispatch(request("Next task"))
            with pytest.raises(ControlError, match="active task"):
                await controller.dispatch({"operation": "park"})
            release.set()
            assert (await wait_for_job(controller, job_id))["cleanup"]["screen_off"] is True
            assert controller.status()["busy"] is False
        finally:
            release.set()
            await controller.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["preview", "review", "failed"])
def test_unfinished_tasks_and_previews_do_not_leave_the_current_screen(tmp_path, case):
    async def scenario():
        class Model(NavigationModel):
            async def decide(self, task, frame, history):
                if case == "failed":
                    raise RuntimeError("test model failure")
                action, metadata = await super().decide(task, frame, history)
                if case == "review":
                    action["requires_review"] = True
                return action, metadata

        portal = ScreenPortal()
        controller = TaskController(host_with(portal), Model(), tmp_path, park_on_finish=True)
        try:
            payload = request()
            if case == "preview":
                payload["execute"] = False
            job_id = (await controller.dispatch(payload))["job"]["id"]
            job = await wait_for_job(controller, job_id)
            assert "cleanup" not in job
            assert portal.interactive is True
            assert all(method != "global" for method, _, _ in portal.calls)
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_cleanup_problem_preserves_completed_job_result(tmp_path):
    async def scenario():
        class RefusingPortal(ScreenPortal):
            async def call(self, method, params=None, *, mutation=False):
                if method == "global" and params["action"] == 8:
                    self.uncertain = True
                    raise UncertainAction("lock outcome unknown")
                return await super().call(method, params, mutation=mutation)

        portal = RefusingPortal()
        controller = TaskController(host_with(portal), NavigationModel(), tmp_path, park_on_finish=True)
        try:
            job_id = (await controller.dispatch(request()))["job"]["id"]
            job = await wait_for_job(controller, job_id)
            assert job["state"] == "finished"
            assert job["result"]["status"] == "completion_candidate"
            assert job["cleanup"] == {"status": "failed", "error_type": "UncertainAction"}
            assert controller.status()["recovery_required"] is True
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_return_home_happens_before_disabling_watchdog_can_sleep_display(tmp_path):
    async def scenario():
        class SleepingPortal(ScreenPortal):
            async def call(self, method, params=None, *, mutation=False):
                value = await super().call(method, params, mutation=mutation)
                if method == "screen/keepAwake/set" and params["enabled"] is False:
                    self.interactive = False
                return value
        portal = SleepingPortal()
        result = await park_phone(portal, Journal(tmp_path / "cleanup"))
        assert result["home_dispatched"] is True and result["screen_off"] is True
        assert not any(method == "global" and params["action"] == 8 for method, params, _ in portal.calls)
    asyncio.run(scenario())


def test_cli_cleanup_setting_and_manual_park_command(monkeypatch):
    monkeypatch.setenv("PHONE_PARK_ON_FINISH", "1")
    parser = build_parser()
    assert parser.parse_args(["serve"]).park_on_finish is True
    assert parser.parse_args(["serve", "--no-park-on-finish"]).park_on_finish is False
    assert parser.parse_args(["park"]).command == "park"

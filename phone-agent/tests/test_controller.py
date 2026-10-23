from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from flagd_phone.cli import PhoneHost, build_parser, task_from_args
from flagd_phone.controller import ControlError, TaskController, control_request, control_server
from flagd_phone.model import ModelOutputError, VisionTransportError
from flagd_phone.protocol import UncertainAction
from test_agent import FakePortal, decision, png64


class ConnectedPortal(FakePortal):
    def __init__(self):
        super().__init__()
        self.uncertain = False
        self.reader = SimpleNamespace(done=lambda: False)


class NavigationModel:
    def __init__(self):
        self.goals = []

    async def decide(self, task, frame, history):
        self.goals.append(task["goal"])
        action = decision(frame, x=20, y=30) if not history else decision(frame, "done")
        return action, {"usage": {"totalTokenCount": 100}}


def host_with(portal):
    return SimpleNamespace(current=portal, device_id="owned-phone")


def request(goal="Open Settings", **extra):
    return {"operation": "submit", "task": {"goal": goal, "max_steps": 3},
            "execute": True, "request_id": str(uuid.uuid4()), **extra}


async def wait_for_job(controller, job_id):
    await asyncio.wait_for(asyncio.shield(controller.active), 5)
    return controller.status(job_id)["job"]


def test_two_submissions_share_a_real_websocket_and_have_separate_reports(tmp_path):
    async def scenario():
        host = PhoneHost("t" * 32, "owned-phone", persistent=True)
        model = NavigationModel()
        async with serve(host.handle, "127.0.0.1", 0, process_request=host.authorize) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/portal", proxy=None, additional_headers={
                "Authorization": "Bearer " + "t" * 32, "X-Device-ID": "owned-phone",
            }) as phone:
                methods = []

                async def answer():
                    async for raw in phone:
                        message = json.loads(raw)
                        methods.append(message["method"])
                        await phone.send(json.dumps({
                            "id": message["id"], "status": "success",
                            "result": png64() if message["method"] == "screenshot" else "accepted",
                        }))

                responder = asyncio.create_task(answer())
                portal = await asyncio.wait_for(host.connected, 2)
                controller = TaskController(host, model, tmp_path / "runs")
                try:
                    with tempfile.TemporaryDirectory(dir="/tmp", prefix="phone-control-") as directory:
                        control = Path(directory) / "control.sock"
                        async with control_server(controller, control):
                            assert stat.S_IMODE(control.stat().st_mode) == 0o600
                            ids = []
                            for goal in ("Open Settings", "Open browser"):
                                result = await control_request(control, request(goal))
                                job_id = result["job"]["id"]
                                ids.append(job_id)
                                job = await wait_for_job(controller, job_id)
                                assert job["state"] == "finished"
                                assert job["result"]["status"] == "completion_candidate"
                                assert Path(job["result"]["reports"]["markdown"]).is_file()
                                assert host.current is portal and not portal.reader.done()
                            assert ids[0] != ids[1]
                            status = await control_request(control, {"operation": "status"})
                            assert status["connected"] is True and status["busy"] is False
                            assert methods == ["screenshot", "tap", "screenshot"] * 2
                            assert model.goals == ["Open Settings"] * 2 + ["Open browser"] * 2
                        assert not control.exists()
                finally:
                    await controller.close()
                    host.finished.set()
                    responder.cancel()
                    await asyncio.gather(responder, return_exceptions=True)

    asyncio.run(scenario())


def test_request_id_deduplicates_even_after_completion_and_restart(tmp_path):
    async def scenario():
        portal = ConnectedPortal()
        model = NavigationModel()
        controller = TaskController(host_with(portal), model, tmp_path)
        payload = request()
        first = await controller.dispatch(payload)
        second = await controller.dispatch(payload)
        assert second["duplicate"] is True and first["job"]["id"] == second["job"]["id"]
        with pytest.raises(ControlError, match="different instruction"):
            await controller.dispatch({**payload, "execute": False})
        await wait_for_job(controller, first["job"]["id"])
        assert (await controller.dispatch(payload))["duplicate"] is True
        assert len(model.goals) == 2
        await controller.close()
        reopened = TaskController(host_with(portal), model, tmp_path)
        try:
            old = await reopened.dispatch(payload)
            assert old["duplicate"] is True and old["job"]["state"] == "finished"
            assert len(model.goals) == 2
            assert stat.S_IMODE(reopened.state_path.stat().st_mode) == 0o600
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_busy_task_blocks_other_tasks_and_captures_but_cancel_keeps_phone(tmp_path):
    async def scenario():
        entered = asyncio.Event()

        class WaitingModel:
            async def decide(self, *args):
                entered.set()
                await asyncio.Event().wait()

        portal = ConnectedPortal()
        controller = TaskController(host_with(portal), WaitingModel(), tmp_path)
        try:
            job_id = (await controller.dispatch(request()))["job"]["id"]
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(ControlError, match="owns the phone"):
                await controller.dispatch(request("Open browser"))
            with pytest.raises(ControlError, match="active task"):
                await controller.capture()
            controller.cancel(job_id)
            controller.cancel(job_id)
            job = await wait_for_job(controller, job_id)
            assert job["state"] == "cancelled"
            assert job["result"]["status"] == "cancelled"
            assert not controller.busy and not portal.reader.done()
            assert [c[0] for c in portal.calls] == ["screenshot"]
            assert (await controller.capture())["path"].endswith("000.png")
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_cancel_before_job_starts_is_terminal_and_does_not_call_phone(tmp_path):
    async def scenario():
        portal = ConnectedPortal()
        controller = TaskController(host_with(portal), NavigationModel(), tmp_path)
        try:
            job_id = (await controller.dispatch(request()))["job"]["id"]
            controller.cancel(job_id)
            await asyncio.gather(controller.active, return_exceptions=True)
            assert controller.status(job_id)["job"]["state"] == "cancelled"
            assert portal.calls == []
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_failed_model_does_not_disconnect_or_prevent_a_new_instruction(tmp_path):
    async def scenario():
        class Model(NavigationModel):
            async def decide(self, task, frame, history):
                if task["goal"] == "bad":
                    raise ModelOutputError({"usage": {"totalTokenCount": 100}})
                return await super().decide(task, frame, history)

        portal = ConnectedPortal()
        controller = TaskController(host_with(portal), Model(), tmp_path)
        try:
            bad = (await controller.dispatch(request("bad")))["job"]["id"]
            assert (await wait_for_job(controller, bad))["state"] == "failed"
            assert controller.status()["connected"] is True
            good = (await controller.dispatch(request("good")))["job"]["id"]
            assert (await wait_for_job(controller, good))["state"] == "finished"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_durable_queue_waits_for_phone_and_survives_controller_restart(tmp_path):
    async def scenario():
        host = host_with(None)
        model = NavigationModel()
        first = TaskController(host, model, tmp_path, retry_base_seconds=0.01)
        payload = {"operation": "enqueue", "task": {"goal": "Open Settings", "max_steps": 3},
                   "execute": True, "request_id": str(uuid.uuid4())}
        queued = await first.dispatch(payload)
        item_id = queued["queue_item"]["id"]
        assert (await first.dispatch(payload))["duplicate"] is True
        await asyncio.sleep(1.1)
        assert first.queue_status(item_id)["queue_item"]["state"] == "queued"
        await first.close()

        host.current = ConnectedPortal()
        second = TaskController(host, model, tmp_path, retry_base_seconds=0.01)
        try:
            async with asyncio.timeout(5):
                while second.queue_status(item_id)["queue_item"]["state"] != "finished":
                    await asyncio.sleep(0.05)
            item = second.queue_status(item_id)["queue_item"]
            assert item["attempts"] == 1
            assert second.jobs[item["attempt_job_id"]]["result"]["status"] == "completion_candidate"
            assert model.goals == ["Open Settings", "Open Settings"]
        finally:
            await second.close()

    asyncio.run(scenario())


def test_queue_retries_transport_failure_only_when_no_public_action_occurred(tmp_path):
    async def scenario():
        class FlakyModel(NavigationModel):
            def __init__(self):
                super().__init__()
                self.failed = False

            async def decide(self, task, frame, history):
                if not self.failed:
                    self.failed = True
                    raise VisionTransportError([{"model": "fake", "transport_error": True, "usage": {}}])
                return await super().decide(task, frame, history)

        controller = TaskController(host_with(ConnectedPortal()), FlakyModel(), tmp_path,
                                    retry_base_seconds=0.01)
        try:
            payload = {"operation": "enqueue", "task": {"goal": "Open Settings", "max_steps": 3},
                       "execute": True, "request_id": str(uuid.uuid4())}
            item_id = (await controller.dispatch(payload))["queue_item"]["id"]
            async with asyncio.timeout(8):
                while controller.queue_status(item_id)["queue_item"]["state"] != "finished":
                    await asyncio.sleep(0.05)
            item = controller.queue_status(item_id)["queue_item"]
            assert item["retry_count"] == 1 and item["attempts"] == 2
            prior = controller.jobs[item["last_job_id"]]
            assert prior["error_type"] == "VisionTransportError"
            assert prior["result"]["public_action_attempts"]["publish"] == 0
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", ["VisionTransportError", "PortalError"])
def test_queue_blocks_after_a_publication_attempt(tmp_path, error_type):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            item = {"id": uuid.uuid4().hex, "task": {"goal": "post", "publication": {"source_id": "one"},
                    "authorization": {"account": "example"}}, "state": "running", "retry_count": 0}
            controller.queue.append(item)
            job = {"id": uuid.uuid4().hex, "state": "failed", "error_type": error_type,
                   "result": {"status": "failed", "public_action_attempts": {
                       "publish": 1, "delete": 0, "like": 0, "comment": 0}}}
            controller._settle_queue_item(item, job)
            assert item["state"] == "blocked"
            assert item["retry_count"] == 0
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_skip_blocked_queue_item_keeps_uncertain_post_and_later_work(tmp_path):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            blocked_id, next_id = uuid.uuid4().hex, uuid.uuid4().hex
            controller.queue.extend([
                {"id": blocked_id, "task": {"goal": "post A"}, "state": "blocked",
                 "block_reason": "completion_candidate", "attempt_job_id": "job-a"},
                {"id": next_id, "task": {"goal": "post B"}, "state": "queued"},
            ])
            receipt = controller.skip_blocked_queue_item(
                blocked_id, "Observed the Reel after Share; destination receipt was missing."
            )["queue_item"]
            assert receipt["state"] == "skipped"
            assert receipt["block_reason"] == "completion_candidate"
            assert receipt["attempt_job_id"] == "job-a"
            assert receipt["skip_reason"].startswith("Observed the Reel")
            assert controller.queue[1]["state"] == "queued"
            assert controller.status()["queue"]["skipped"] == 1
            with pytest.raises(ControlError):
                controller.skip_blocked_queue_item(next_id, "Cannot skip queued work")
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_queue_waits_for_credit_after_http_402_without_public_action(tmp_path):
    async def scenario():
        run_dir = tmp_path / "failed-model-request"
        run_dir.mkdir()
        (run_dir / "trace.jsonl").write_text(
            '{"event": "error", "error_type": "VisionRequestError", "http_status": 402}\n'
        )
        controller = TaskController(host_with(None), NavigationModel(), tmp_path)
        try:
            item = {"id": uuid.uuid4().hex, "task": {"goal": "post"},
                    "state": "running", "retry_count": 0}
            controller.queue.append(item)
            job = {"id": uuid.uuid4().hex, "state": "failed",
                   "error_type": "VisionRequestError", "run_dir": str(run_dir),
                   "result": {"status": "failed", "public_action_attempts": {
                       "publish": 0, "delete": 0, "like": 0, "comment": 0}}}
            controller._settle_queue_item(item, job)
            assert item["state"] == "queued"
            assert item["retry_count"] == 1
            assert item["next_attempt_at"] >= time.time() + 1790
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_queue_restores_old_http_402_block_on_restart(tmp_path):
    async def scenario():
        run_dir = tmp_path / "old-failed-request"
        run_dir.mkdir()
        (run_dir / "trace.jsonl").write_text(
            '{"event": "error", "error_type": "VisionRequestError", "http_status": 402}\n'
        )
        first = TaskController(host_with(None), NavigationModel(), tmp_path)
        item_id, job_id, request_id = uuid.uuid4().hex, uuid.uuid4().hex, str(uuid.uuid4())
        task = {"goal": "Open Settings", "max_steps": 3}
        first.jobs[job_id] = {
            "id": job_id, "request_id": request_id, "task": task,
            "state": "failed", "error_type": "VisionRequestError", "run_dir": str(run_dir),
            "result": {"status": "failed", "public_action_attempts": {
                "publish": 0, "delete": 0, "like": 0, "comment": 0}},
        }
        first.queue.append({
            "id": item_id, "task": task, "state": "blocked", "retry_count": 0,
            "block_reason": "VisionRequestError", "attempt_job_id": job_id,
        })
        first._save()
        await first.close()

        second = TaskController(host_with(None), NavigationModel(), tmp_path)
        try:
            item = second.queue_status(item_id)["queue_item"]
            assert item["state"] == "queued"
            assert item["retry_count"] == 1
            assert item["last_job_id"] == job_id
        finally:
            await second.close()

    asyncio.run(scenario())


def test_queue_restores_portal_failure_before_public_action(tmp_path):
    async def scenario():
        first = TaskController(host_with(None), NavigationModel(), tmp_path)
        item_id, job_id, request_id = uuid.uuid4().hex, uuid.uuid4().hex, str(uuid.uuid4())
        task = {"goal": "Open Settings", "max_steps": 3}
        first.jobs[job_id] = {
            "id": job_id, "request_id": request_id, "task": task,
            "state": "failed", "error_type": "PortalError", "run_dir": str(tmp_path),
            "result": {"status": "failed", "public_action_attempts": {
                "publish": 0, "delete": 0, "like": 0, "comment": 0}},
        }
        first.queue.append({
            "id": item_id, "task": task, "state": "blocked", "retry_count": 0,
            "block_reason": "PortalError", "attempt_job_id": job_id,
        })
        first._save()
        await first.close()

        second = TaskController(host_with(None), NavigationModel(), tmp_path)
        try:
            item = second.queue_status(item_id)["queue_item"]
            assert item["state"] == "queued"
            assert item["retry_count"] == 1
            assert item["last_job_id"] == job_id
        finally:
            await second.close()

    asyncio.run(scenario())


def test_expedite_queue_only_after_safe_retry(tmp_path):
    async def scenario():
        controller = TaskController(host_with(None), NavigationModel(), tmp_path)
        try:
            item_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
            item = {"id": item_id, "task": {"goal": "post"}, "state": "queued",
                    "next_attempt_at": time.time() + 1800, "last_job_id": job_id}
            controller.queue.append(item)
            controller.jobs[job_id] = {"result": {"public_action_attempts": {
                "publish": 0, "delete": 0, "like": 0, "comment": 0}}}
            assert controller.expedite_queue(item_id)["queue_item"]["next_attempt_at"] == 0
            item["next_attempt_at"] = time.time() + 1800
            controller.jobs[job_id]["result"]["public_action_attempts"]["publish"] = 1
            with pytest.raises(ControlError) as error:
                controller.expedite_queue(item_id)
            assert error.value.code == "unsafe_retry"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_queue_reconciliation_requires_matching_profile_post(tmp_path):
    async def scenario():
        class ProfileModel(NavigationModel):
            valid = True

            async def verify_profile_publication(self, task, frame, expected_headline):
                return {"valid": self.valid, "account": "try.flagd",
                        "visible_text": expected_headline, "evidence": "profile header and newest tile"}, {}

        model = ProfileModel()
        controller = TaskController(host_with(ConnectedPortal()), model, tmp_path)
        try:
            job_id, item_id = uuid.uuid4().hex, uuid.uuid4().hex
            task = {"goal": "post", "authorization": {"app": "TikTok", "account": "try.flagd"},
                    "publication": {"source_id": "new-post"}}
            controller.jobs[job_id] = {"id": job_id, "task": task, "state": "failed",
                                       "result": {"status": "failed", "public_action_attempts": {
                                           "publish": 1, "delete": 0, "like": 0, "comment": 0}}}
            controller.queue.append({"id": item_id, "task": task, "state": "blocked",
                                     "attempt_job_id": job_id, "block_reason": "PortalError"})
            model.valid = False
            with pytest.raises(ControlError) as error:
                await controller.reconcile_queue_profile(item_id, "NEW POST")
            assert error.value.code == "unconfirmed_publication"
            assert controller.queue_status(item_id)["queue_item"]["state"] == "blocked"
            model.valid = True
            receipt = await controller.reconcile_queue_profile(item_id, "NEW POST")
            assert receipt["queue_item"]["state"] == "finished"
            assert receipt["job"]["result"]["publication_account"] == "try.flagd"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_uncertain_input_requires_fresh_capture_and_acknowledgement_not_replay(tmp_path):
    async def scenario():
        class UncertainPortal(ConnectedPortal):
            async def call(self, method, params=None, *, mutation=False):
                if mutation:
                    self.calls.append((method, params, mutation))
                    self.uncertain = True
                    raise UncertainAction("unknown input outcome")
                return await super().call(method, params, mutation=mutation)

        portal = UncertainPortal()
        host = host_with(portal)
        controller = TaskController(host, NavigationModel(), tmp_path)
        try:
            job_id = (await controller.dispatch(request()))["job"]["id"]
            assert (await wait_for_job(controller, job_id))["result"]["status"] == "uncertain_input"
            assert controller.status()["recovery_required"] is True
            host.current = ConnectedPortal()  # A reconnect does not clear the hold.
            with pytest.raises(ControlError, match="Capture and inspect"):
                await controller.dispatch(request("new goal"))
            capture = await controller.capture()
            with pytest.raises(ControlError, match="fresh frame"):
                controller.acknowledge("unseen-frame")
            controller.acknowledge(capture["frame_id"])
            assert controller.status()["recovery_required"] is False
            assert len([c for c in portal.calls if c[2]]) == 1
            assert host.current.calls == [("screenshot", {"hideOverlay": True}, False)]
            assert controller.jobs[job_id]["result"]["status"] == "uncertain_input"
            new = (await controller.dispatch(request("new goal")))["job"]["id"]
            assert (await wait_for_job(controller, new))["state"] == "finished"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_stale_capture_and_capture_from_previous_connection_cannot_clear_hold(tmp_path):
    async def scenario():
        portal = ConnectedPortal()
        host = host_with(portal)
        controller = TaskController(host, NavigationModel(), tmp_path)
        try:
            controller.recovery_required = True
            controller.recovery_capture = ("old", time.monotonic() - 91, portal)
            with pytest.raises(ControlError):
                controller.acknowledge("old")
            capture = await controller.capture()
            host.current = ConnectedPortal()
            with pytest.raises(ControlError):
                controller.acknowledge(capture["frame_id"])
            assert controller.recovery_required is True
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_interrupted_jobs_survive_restart_without_automatic_execution(tmp_path):
    async def scenario():
        portal = ConnectedPortal()
        model = NavigationModel()
        payload = request()
        job_id = uuid.uuid4().hex
        saved = {"jobs": {job_id: {
            "id": job_id, "request_id": payload["request_id"], "task": payload["task"],
            "execute": True, "state": "running", "run_dir": str(tmp_path / "unfinished"),
        }}, "recovery_required": False}
        (tmp_path / "controller-jobs.json").write_text(json.dumps(saved))
        controller = TaskController(host_with(portal), model, tmp_path)
        try:
            job = (await controller.dispatch(payload))["job"]
            assert job["state"] == "interrupted"
            assert controller.recovery_required is True
            assert portal.calls == [] and model.goals == []
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_only_one_controller_can_own_a_task_directory(tmp_path):
    async def scenario():
        host = host_with(ConnectedPortal())
        first = TaskController(host, NavigationModel(), tmp_path)
        try:
            with pytest.raises(ControlError, match="owns this task directory"):
                TaskController(host, NavigationModel(), tmp_path)
            assert first.status()["connected"] is True
        finally:
            await first.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    {}, [], {"operation": "shell", "cmd": "anything"},
    request(task={"goal": "test", "max_steps": 0}),
    request(execute="true"), request(request_id="not-a-uuid"),
    {"operation": "capture", "hidden": True},
])
def test_invalid_requests_never_start_phone_or_ai_calls(tmp_path, payload):
    async def scenario():
        portal = ConnectedPortal()
        model = NavigationModel()
        controller = TaskController(host_with(portal), model, tmp_path)
        try:
            with pytest.raises(ControlError):
                await controller.dispatch(payload)
            assert portal.calls == [] and model.goals == [] and controller.jobs == {}
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_control_socket_preserves_regular_files_and_rejects_a_second_listener(tmp_path):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            with tempfile.TemporaryDirectory(dir="/tmp", prefix="phone-control-") as directory:
                path = Path(directory) / "control.sock"
                path.write_text("keep me")
                with pytest.raises(ControlError, match="leave it unchanged"):
                    async with control_server(controller, path):
                        pass
                assert path.read_text() == "keep me"
                path.unlink()
                async with control_server(controller, path):
                    with pytest.raises(ControlError, match="already listening"):
                        async with control_server(controller, path):
                            pass
                    assert (await control_request(path, {"operation": "status"}))["connected"] is True
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_stale_owned_socket_can_be_replaced(tmp_path):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            with tempfile.TemporaryDirectory(dir="/tmp", prefix="phone-control-") as directory:
                path = Path(directory) / "control.sock"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
                    abandoned.bind(str(path))
                async with control_server(controller, path):
                    assert (await control_request(path, {"operation": "status"}))["busy"] is False
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_submit_cli_accepts_natural_goal_without_device_or_model_environment(monkeypatch):
    for name in ("PHONE_DEVICE_TOKEN", "PHONE_GEMINI_API_KEY", "PHONE_VISION_MODEL"):
        monkeypatch.delenv(name, raising=False)
    args = build_parser().parse_args([
        "submit", "--goal", "Open the browser", "--execute", "--wait", "--max-steps", "8",
    ])
    assert task_from_args(args)["goal"] == "Open the browser"
    assert args.execute and args.wait and args.max_steps == 8

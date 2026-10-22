from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from flagd_phone.cli import PhoneHost, build_parser, task_from_args
from flagd_phone.model import GeminiVision
from flagd_phone.protocol import PortalConnection, PortalError, UncertainAction
from flagd_phone.runner import Journal, load_task, run_agent, screenshot
from flagd_phone.vision import Frame, validate_action
from flagd_phone.device import ensure_screen_awake


def png64(width=1080, height=1920):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 30, 40)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def decision(frame, kind="tap", **extra):
    return {"kind": kind, "frame_id": frame.id, "reason": "visible app icon",
            "expected_change": "app opens", "confidence": 0.95, "requires_review": False, **extra}


class FakeSocket:
    def __init__(self, *, respond=True, reject=False):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.respond = respond
        self.reject = reject

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        # A live event can arrive before the response and has no request ID.
        await self.incoming.put(json.dumps({"method": "events/device", "params": {"type": "APP_ENTERED"}}))
        if self.respond:
            await self.incoming.put(json.dumps({"id": message["id"],
                "status": "error" if self.reject else "success",
                "error": "input accepted but unverified"} if self.reject else {
                    "id": message["id"], "status": "success", "result": "accepted",
                }))


def test_coordinates_round_trip_edges_and_center():
    frame = Frame.from_portal(png64())
    assert (frame.view_width, frame.view_height) == (576, 1024)
    assert frame.to_device(0, 0) == (0, 0)
    assert frame.to_device(575, 1023) == (1079, 1919)
    assert frame.to_device(287.5, 511.5) == (540, 960)
    for x, y in ((576, 0), (-1, 20), (float("nan"), 10), (True, 5), (10, 1024)):
        with pytest.raises(ValueError):
            frame.to_device(x, y)


def test_actions_reject_stale_coordinates_or_invented_text():
    frame = Frame.from_portal(png64())
    for action in (
        decision(frame, x=100, y=200, frame_id="old-screen"),
        decision(frame, x=9999, y=200),
        decision(frame, "type_text", text_key="invented"),
        decision(frame, "type_text", text_key="title", x=12),
        decision(frame, x=100, y=200, confidence=float("nan")),
        decision(frame, x=100, y=200, requires_review="false"),
        decision(frame, "swipe", x=20, y=200, end_x=20, end_y=10, duration_ms=10000),
        decision(frame, "swipe", x=20, y=200, end_x=20, end_y=10, playback_ms=15001),
        decision(frame, "swipe", x=20, y=200, end_x=20, end_y=10, playback_ms=True),
        decision(frame, "watch", duration_ms=60000),
    ):
        with pytest.raises(ValueError):
            validate_action(action, frame, {"title": "exact text"})


def test_title_limit_applies_before_device_or_model(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"goal": "prepare draft", "text_fields": {"title": "x" * 90},
                                "text_limits": {"title": 90}}))
    assert len(load_task(path)["text_fields"]["title"]) == 90
    path.write_text(json.dumps({"goal": "prepare draft", "text_fields": {"title": "x" * 91},
                                "text_limits": {"title": 90}}))
    with pytest.raises(ValueError, match="90"):
        load_task(path)


def test_plain_language_command_preserves_instruction_and_supplied_text():
    args = build_parser().parse_args([
        "run", "--goal", "Open the browser and search for this query.",
        "--text", "query=places in Thailand = Bangkok", "--max-steps", "12",
    ])
    task = task_from_args(args)
    assert task["goal"] == "Open the browser and search for this query."
    assert task["text_fields"]["query"] == "places in Thailand = Bangkok"
    assert task["max_steps"] == 12
    assert args.execute is False


def test_generic_task_does_not_inherit_tiktok_title_limit():
    args = build_parser().parse_args([
        "run", "--goal", "Create a note with this title.", "--text", "title=" + "x" * 150,
    ])
    assert len(task_from_args(args)["text_fields"]["title"]) == 150


def test_robot_check_authorization_is_explicit_and_task_scoped():
    ordinary = task_from_args(build_parser().parse_args(["run", "--goal", "Search Thailand"]))
    permitted = task_from_args(build_parser().parse_args([
        "run", "--goal", "Complete this robot check", "--allow-robot-verification",
    ]))
    frame = Frame.from_portal(png64())
    assert GeminiVision._context(ordinary, frame, [])["allow_robot_verification"] is False
    assert GeminiVision._context(permitted, frame, [])["allow_robot_verification"] is True
    from flagd_phone.runner import validate_task
    with pytest.raises(ValueError, match="boolean"):
        validate_task({"goal": "Search", "allow_robot_verification": "true"})


@pytest.mark.parametrize("arguments", [
    ["--goal", "   "], ["--goal", "Open Settings", "--max-steps", "0"],
    ["--goal", "Search", "--text", "missing-separator"],
    ["--goal", "Search", "--text", "query=one", "--text", "query=two"],
])
def test_invalid_direct_instructions_are_rejected_before_any_request(arguments):
    with pytest.raises(ValueError):
        task_from_args(build_parser().parse_args(["run", *arguments]))


def test_instruction_sources_cannot_be_combined():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--goal", "Open Settings", "--task", "task.json"])


def test_events_cannot_satisfy_command_and_uuid_correlates_reply():
    async def scenario():
        socket = FakeSocket()
        portal = PortalConnection(socket)
        try:
            assert await portal.call("screenshot", {"hideOverlay": True}) == "accepted"
            assert len(socket.sent[0]["id"]) == 36
        finally:
            await portal.close()
    asyncio.run(scenario())


def test_screenshot_can_skip_unused_overlay_delay_and_records_capture_phases(monkeypatch):
    async def scenario():
        class ScreenshotPortal:
            def __init__(self):
                self.calls = []

            async def call(self, method, params):
                self.calls.append((method, params))
                return png64()

        portal = ScreenshotPortal()
        monkeypatch.setenv("PHONE_SCREENSHOT_HIDE_OVERLAY", "0")
        frame = await screenshot(portal)
        assert portal.calls == [("screenshot", {"hideOverlay": False})]
        assert frame.portal_screenshot_ms >= 0
        assert frame.host_prepare_ms >= 0
        assert frame.jpeg.startswith(b"\xff\xd8")

    asyncio.run(scenario())


def test_browse_schema_allows_one_decision_to_watch_then_swipe():
    async def scenario():
        frame = Frame.from_portal(png64())
        def handler(request):
            body = json.loads(request.content)
            variants = {v["properties"]["kind"]["enum"][0]: v for v in
                        body["generationConfig"]["responseJsonSchema"]["properties"]["action"]["anyOf"]}
            swipe = variants["swipe"]
            assert swipe["properties"]["playback_ms"]["maximum"] == 15000
            assert "playback_ms" not in swipe["required"]
            assert variants["watch"]["properties"]["duration_ms"]["maximum"] == 15000
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps({"action": decision(frame, "swipe", x=500, y=850,
                    end_x=500, end_y=200, playback_ms=7000)})}],
            }}], "usageMetadata": {"totalTokenCount": 10}})
        model = GeminiVision("test-key", "gemini-3.1-flash-lite",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            action, _ = await model.decide({"goal": "Browse videos", "mode": "browse"}, frame, [])
            assert action["playback_ms"] == 7000
        finally:
            await model.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("reject", [False, True])
def test_uncertain_input_is_never_replayed(reject):
    async def scenario():
        socket = FakeSocket(respond=reject, reject=reject)
        portal = PortalConnection(socket, timeout=0.02)
        try:
            with pytest.raises(UncertainAction):
                await portal.call("tap", {"x": 10, "y": 20}, mutation=True)
            with pytest.raises(UncertainAction):
                await portal.call("tap", {"x": 10, "y": 20}, mutation=True)
            assert len(socket.sent) == 1
        finally:
            await portal.close()
    asyncio.run(scenario())


def test_disconnect_unblocks_pending_read():
    async def scenario():
        socket = FakeSocket(respond=False)
        portal = PortalConnection(socket)
        async def disconnect():
            while not socket.sent:
                await asyncio.sleep(0)
            await socket.incoming.put(None)
        closer = asyncio.create_task(disconnect())
        try:
            with pytest.raises(PortalError):
                await asyncio.wait_for(portal.call("screenshot"), 1)
        finally:
            await closer
            await portal.close()
    asyncio.run(scenario())


def test_device_authentication_and_identity():
    async def scenario():
        host = PhoneHost("t" * 32, "owned-phone")
        connection = SimpleNamespace(respond=lambda status, body: status)
        request = SimpleNamespace(path="/portal", headers={"Authorization": "Bearer " + "t" * 32,
                                                          "X-Device-ID": "owned-phone"})
        assert host.authorize(connection, request) is None
        request.headers["X-Device-ID"] = "other-phone"
        assert host.authorize(connection, request) == 403
        request.headers["Authorization"] = "Bearer wrong"
        assert host.authorize(connection, request) == 401
        request.path = "/unexpected"
        assert host.authorize(connection, request) == 404
    asyncio.run(scenario())


class FakePortal:
    def __init__(self, *, fail_read_after_input=False):
        self.calls = []
        self.fail_read_after_input = fail_read_after_input

    async def call(self, method, params=None, *, mutation=False):
        self.calls.append((method, params, mutation))
        if method == "screenshot":
            if self.fail_read_after_input and len(self.calls) > 1:
                raise PortalError("read failed")
            return png64()
        return "accepted"


class FakeModel:
    def __init__(self):
        self.calls = 0

    async def decide(self, task, frame, history):
        self.calls += 1
        action = decision(frame, x=287.5, y=511.5) if self.calls == 1 else decision(frame, "done")
        return action, {"model": "fake", "usage": {"totalTokenCount": 100}}


def test_preview_spends_one_decision_but_sends_no_input(tmp_path):
    async def scenario():
        portal = FakePortal()
        model = FakeModel()
        result = await run_agent(portal, model, {"goal": "open TikTok", "max_steps": 10}, Journal(tmp_path / "run"))
        assert result["status"] == "preview"
        assert model.calls == 1
        assert [c[0] for c in portal.calls] == ["screenshot"]
    asyncio.run(scenario())


def test_repeated_black_capture_stops_without_ai_or_guessed_inputs(tmp_path):
    async def scenario():
        class BlackPortal(FakePortal):
            async def call(self, method, params=None, *, mutation=False):
                self.calls.append((method, params, mutation))
                output = io.BytesIO()
                Image.new("RGB", (720, 1612), "black").save(output, format="PNG")
                return base64.b64encode(output.getvalue()).decode()
        portal, model = BlackPortal(), FakeModel()
        result = await run_agent(portal, model, {"goal": "Open Chrome"}, Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "blank_capture"
        assert result["acknowledged_inputs"] == 0 and model.calls == 0
        assert [c[0] for c in portal.calls] == ["screenshot"] * 3
    asyncio.run(scenario())


def test_unchanged_navigation_returns_feedback_so_agent_can_choose_another_route(tmp_path):
    async def scenario():
        class Model:
            calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls <= 3:
                    return decision(frame, "swipe", x=50, y=100, end_x=50, end_y=50, duration_ms=100), {}
                if self.calls == 4:
                    assert "screen unchanged" in task["execution_context"]["decision_rejection"]
                    return decision(frame, x=20, y=30, reason="Take a different observed route"), {}
                return decision(frame, "done"), {}
        portal = FakePortal()
        result = await run_agent(portal, Model(), {"goal":"Find the supplied content", "max_steps":8},
                                 Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "completion_candidate"
        assert [call[0] for call in portal.calls].count("swipe") == 2
        assert [call[0] for call in portal.calls].count("tap") == 1
    asyncio.run(scenario())


def test_repeated_ineffective_inputs_are_withheld_until_the_decision_budget_ends(tmp_path):
    async def scenario():
        class Model:
            async def decide(self, task, frame, history):
                return decision(frame, "swipe", x=50, y=100, end_x=50, end_y=50, duration_ms=100), {}
        portal = FakePortal()
        result = await run_agent(portal, Model(), {"goal":"Find content", "max_steps":8},
                                 Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "step_limit"
        assert result["model_calls"] == 8
        assert [call[0] for call in portal.calls].count("swipe") == 2
    asyncio.run(scenario())


def test_fresh_screenshot_follows_input_and_completion_is_unverified(tmp_path):
    async def scenario():
        portal = FakePortal()
        result = await run_agent(portal, FakeModel(), {"goal": "open TikTok", "max_steps": 10},
                                 Journal(tmp_path / "run"), execute=True)
        assert [c[0] for c in portal.calls] == ["screenshot", "tap", "screenshot"]
        assert portal.calls[1][1] == {"x": 540, "y": 960}
        assert result["status"] == "completion_candidate"
        assert result["verified_success"] is False
        assert result["reported_total_tokens"] == 200
    asyncio.run(scenario())


def test_input_receipt_survives_failed_followup_read(tmp_path):
    async def scenario():
        portal = FakePortal(fail_read_after_input=True)
        model = FakeModel()
        journal = Journal(tmp_path / "run")
        with pytest.raises(PortalError):
            await run_agent(portal, model, {"goal": "open TikTok", "max_steps": 10}, journal, execute=True)
        events = [json.loads(line)["event"] for line in journal.path.read_text().splitlines()]
        assert events.index("input_intent") < events.index("input_acknowledged") < events.index("error")
        assert model.calls == 1
        assert [c[0] for c in portal.calls] == ["screenshot", "tap", "screenshot"]
    asyncio.run(scenario())


def test_review_action_never_executes(tmp_path):
    async def scenario():
        class ReviewModel:
            async def decide(self, task, frame, history):
                return decision(frame, x=10, y=10, requires_review=True), {}
        portal = FakePortal()
        result = await run_agent(portal, ReviewModel(), {"goal": "prepare draft", "max_steps": 10},
                                 Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "blocked"
        assert len(portal.calls) == 3
        assert all(method == "screenshot" for method, _, _ in portal.calls)
    asyncio.run(scenario())


def test_search_submission_sends_enter_then_observes_without_replaying(tmp_path):
    async def scenario():
        class SubmitModel:
            def __init__(self):
                self.calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                return decision(frame, "submit_search" if self.calls == 1 else "done"), {}

        portal = FakePortal()
        result = await run_agent(portal, SubmitModel(), {"goal": "Search Thailand"},
                                 Journal(tmp_path / "submit"), execute=True)
        assert [c[0] for c in portal.calls] == ["screenshot", "keyboard/key", "screenshot"]
        assert portal.calls[1][1] == {"key_code": 66}
        assert portal.calls[1][2] is True
        assert result["acknowledged_inputs"] == 1
        frame = Frame.from_portal(png64())
        with pytest.raises(ValueError):
            validate_action(decision(frame, "submit_search", key_code=4), frame, {})

    asyncio.run(scenario())


def test_gemini_uses_pixels_and_schema_and_reports_usage():
    async def scenario():
        frame = Frame.from_portal(png64())
        def handler(request):
            body = json.loads(request.content)
            assert "state" not in body
            parts = body["contents"][0]["parts"]
            context = json.loads(parts[0]["text"])
            assert context["coordinate_space"] == {
                "system": "normalized_0_1000", "minimum": 0, "maximum": 1000,
            }
            assert context["screenshot_size"] == {"width": 576, "height": 1024}
            assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
            assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"
            variants = {v["properties"]["kind"]["enum"][0]: v
                        for v in body["generationConfig"]["responseJsonSchema"]["properties"]["action"]["anyOf"]}
            assert body["generationConfig"]["responseJsonSchema"]["required"] == ["action"]
            assert "end_x" not in variants["tap"]["properties"]
            assert variants["tap"]["properties"]["x"]["maximum"] == 1000
            assert all(v["additionalProperties"] is False for v in variants.values())
            assert "title_matches_source" in variants["confirm_preview"]["required"]
            assert "title_matches_source" not in variants["confirm_account"]["properties"]
            assert "end_x" in variants["swipe"]["required"]
            assert variants["swipe"]["properties"]["end_x"]["maximum"] == 1000
            assert variants["wait"]["properties"]["duration_ms"]["maximum"] == 10000
            assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
            assert body["generationConfig"]["maxOutputTokens"] == 8192
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps({"action": decision(frame, "type_text", text_key="title")})}],
            }}], "usageMetadata": {"totalTokenCount": 222}, "modelVersion": "test-model"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = GeminiVision("test-key", "gemini-3.8-flash", client=client)
        try:
            action, metadata = await model.decide({"goal": "fill title", "text_fields": {"title": "exact text"}}, frame, [])
            assert action["text_key"] == "title"
            assert metadata["usage"]["totalTokenCount"] == 222
        finally:
            await model.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("size,coordinates,expected", [
    ((720, 1612), (622, 913), (447, 1471)),
    ((1920, 1080), (0, 1000), (0, 1079)),
    ((1080, 1920), (1000, 0), (1079, 0)),
    ((1080, 1920), (500, 500), (540, 960)),
])
def test_gemini_coordinates_map_to_real_device_for_both_orientations(size, coordinates, expected):
    frame = Frame.from_portal(png64(*size))
    action = GeminiVision._action_pixels(decision(frame, x=coordinates[0], y=coordinates[1]), frame)
    validate_action(action, frame, {})
    assert frame.to_device(action["x"], action["y"]) == expected
    context = GeminiVision._context({"goal": "navigate"}, frame, [{"action": action}])
    assert context["recent_actions"][0]["action"]["x"] == pytest.approx(coordinates[0])
    assert context["recent_actions"][0]["action"]["y"] == pytest.approx(coordinates[1])
    assert action["x"] == coordinates[0] * (frame.view_width - 1) / 1000


@pytest.mark.parametrize("coordinate", [-1, 1001, True, float("nan"), float("inf")])
def test_gemini_adapter_rejects_invalid_coordinates_without_clamping(coordinate):
    frame = Frame.from_portal(png64())
    with pytest.raises(ValueError, match="normalized"):
        GeminiVision._action_pixels(decision(frame, x=coordinate, y=500), frame)


def test_real_websocket_roundtrip_against_simulated_portal():
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    async def scenario():
        host = PhoneHost("t" * 32, "simulated-phone")
        async with serve(host.handle, "127.0.0.1", 0, process_request=host.authorize) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/portal", proxy=None, additional_headers={
                "Authorization": "Bearer " + "t" * 32, "X-Device-ID": "simulated-phone",
            }) as device:
                async def answer():
                    request = json.loads(await device.recv())
                    assert request["method"] == "screenshot"
                    await device.send(json.dumps({"id": request["id"], "status": "success", "result": png64(720, 1280)}))
                responder = asyncio.create_task(answer())
                portal = await asyncio.wait_for(host.connected, 1)
                frame = Frame.from_portal(await portal.call("screenshot", {"hideOverlay": True}))
                assert (frame.width, frame.height) == (720, 1280)
                await responder
                host.finished.set()
    asyncio.run(scenario())


@pytest.mark.parametrize("keep_awake", [False, True])
def test_inspection_keeps_websocket_open_after_screenshot(monkeypatch, tmp_path, keep_awake):
    from contextlib import asynccontextmanager
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from flagd_phone import cli

    async def scenario():
        listening = asyncio.get_running_loop().create_future()
        captured = asyncio.Event()
        original_frame = Journal.frame

        @asynccontextmanager
        async def observed_server(*args, **kwargs):
            async with serve(*args, **kwargs) as server:
                listening.set_result(server.sockets[0].getsockname()[1])
                yield server

        def record_frame(self, *args):
            original_frame(self, *args)
            captured.set()

        monkeypatch.setattr(cli, "serve", observed_server)
        monkeypatch.setattr(Journal, "frame", record_frame)
        monkeypatch.setenv("PHONE_DEVICE_TOKEN", "t" * 32)
        monkeypatch.setenv("PHONE_DEVICE_ID", "simulated-phone")
        monkeypatch.setenv("PHONE_KEEP_SCREEN_AWAKE", "1" if keep_awake else "0")
        args = build_parser().parse_args([
            "inspect", "--port", "0", "--keep-connected", "--connect-timeout", "2",
            "--runs-dir", str(tmp_path),
        ])
        inspection = asyncio.create_task(cli.run(args))
        try:
            port = await asyncio.wait_for(listening, 2)
            async with connect(f"ws://127.0.0.1:{port}/portal", proxy=None, additional_headers={
                "Authorization": "Bearer " + "t" * 32, "X-Device-ID": "simulated-phone",
            }) as device:
                if keep_awake:
                    request = json.loads(await asyncio.wait_for(device.recv(), 2))
                    assert request["method"] == "screen/keepAwake/set"
                    assert request["params"] == {"enabled": True}
                    # The mutation reply is a target acknowledgement, not proof
                    # that the display has awakened.
                    await device.send(json.dumps({
                        "id": request["id"], "status": "success",
                        "result": {"enabled": True, "interactive": False},
                    }))
                    for awake in (False, True):
                        request = json.loads(await asyncio.wait_for(device.recv(), 2))
                        assert request["method"] == "screen/keepAwake/status"
                        await device.send(json.dumps({
                            "id": request["id"], "status": "success",
                            "result": {"enabled": True, "interactive": awake, "deviceLocked": False},
                        }))
                request = json.loads(await asyncio.wait_for(device.recv(), 2))
                assert request["method"] == "screenshot"
                await device.send(json.dumps({
                    "id": request["id"], "status": "success", "result": png64(720, 1280),
                }))
                await asyncio.wait_for(captured.wait(), 2)
                # A one-shot inspection would close here. This connection must
                # stay open and send no additional capture or input requests.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(device.recv(), 0.05)
                assert not inspection.done()
                assert len(list(tmp_path.glob("*/000.png"))) == 1
            # Reconnection must be accepted without repeating capture or tasks.
            async with connect(f"ws://127.0.0.1:{port}/portal", proxy=None, additional_headers={
                "Authorization": "Bearer " + "t" * 32, "X-Device-ID": "simulated-phone",
            }) as device:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(device.recv(), 0.05)
                assert not inspection.done()
        finally:
            inspection.cancel()
            await asyncio.gather(inspection, return_exceptions=True)

    asyncio.run(scenario())


def test_screen_wake_stops_on_secure_lock_without_phone_navigation(tmp_path):
    async def scenario():
        class LockedPortal:
            def __init__(self):
                self.calls = []

            async def call(self, method, params=None, *, mutation=False):
                self.calls.append((method, mutation))
                return {"enabled": True, "interactive": True, "deviceLocked": True}

        portal = LockedPortal()
        with pytest.raises(PortalError, match="operator unlock"):
            await ensure_screen_awake(portal, Journal(tmp_path / "run"))
        assert portal.calls == [("screen/keepAwake/set", True), ("screen/keepAwake/status", False)]

    asyncio.run(scenario())


def test_model_failure_keeps_listener_available_without_replaying_task(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from flagd_phone import cli
    from flagd_phone.model import ModelOutputError

    async def scenario():
        listening = asyncio.get_running_loop().create_future()
        rejected = asyncio.Event()

        class BadModel:
            calls = 0

            async def decide(self, *args):
                self.calls += 1
                rejected.set()
                raise ModelOutputError({}, reason="invalid action fields")

            async def close(self):
                pass

        model = BadModel()

        @asynccontextmanager
        async def observed_server(*args, **kwargs):
            async with serve(*args, **kwargs) as server:
                listening.set_result(server.sockets[0].getsockname()[1])
                yield server

        monkeypatch.setattr(cli, "serve", observed_server)
        monkeypatch.setattr(cli, "GeminiVision", lambda *args: model)
        monkeypatch.setenv("PHONE_DEVICE_TOKEN", "t" * 32)
        monkeypatch.setenv("PHONE_DEVICE_ID", "simulated-phone")
        monkeypatch.setenv("PHONE_KEEP_SCREEN_AWAKE", "0")
        args = build_parser().parse_args([
            "run", "--goal", "Find brightness", "--port", "0", "--keep-connected",
            "--runs-dir", str(tmp_path),
        ])
        task = asyncio.create_task(cli.run(args))
        headers = {"Authorization": "Bearer " + "t" * 32, "X-Device-ID": "simulated-phone"}
        try:
            port = await asyncio.wait_for(listening, 2)
            url = f"ws://127.0.0.1:{port}/portal"
            async with connect(url, proxy=None, additional_headers=headers) as device:
                request = json.loads(await asyncio.wait_for(device.recv(), 2))
                assert request["method"] == "screenshot"
                await device.send(json.dumps({
                    "id": request["id"], "status": "success", "result": png64(),
                }))
                await asyncio.wait_for(rejected.wait(), 2)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(device.recv(), 0.05)
                assert not task.done()
            async with connect(url, proxy=None, additional_headers=headers) as device:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(device.recv(), 0.05)
                assert model.calls == 1
                assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

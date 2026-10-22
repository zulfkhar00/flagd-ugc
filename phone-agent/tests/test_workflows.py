import asyncio
import base64
import copy
import io
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image, ImageDraw

from flagd_phone.authorization import AuthorizationState, validate_authorization
from flagd_phone.controller import ControlError, TaskController
from flagd_phone.gateway import api_request, instruction_request, instruction_server
from flagd_phone.runner import Journal, run_agent
from flagd_phone.scheduler import schedule_preview, validate_schedule
from flagd_phone.vision import CAROUSEL_CHECKS, Frame, validate_action
from flagd_phone.workflows import browse_task, next_california_slot, slideshow_task, split_caption
from test_agent import FakePortal, decision, png64
from test_controller import ConnectedPortal, NavigationModel, host_with


def post(**changes):
    values = dict(account="try.flagd", volume_id="volume-123", share_url="https://example.com/share/123",
                  caption="An awkward first date?\n\nExact description.\n\n#dating", slide_count=6)
    return slideshow_task(**{**values, **changes})


def checked_preview(frame, **changes):
    return decision(frame, "confirm_preview", **{
        "slide_count": 6, "selected_sound": "Suggested track",
        "evidence": "Cover shows the original first slide; six original images inspected in order.",
        **{key: True for key in CAROUSEL_CHECKS}, **changes,
    })


def test_caption_split_preserves_description_and_counts_unicode():
    assert split_caption("é" * 59 + "\r\n\r\nFirst line.\r\n\r\nLast line.") == {
        "title": "é" * 59, "description": "First line.\n\nLast line.",
    }
    with pytest.raises(ValueError, match="got 60"):
        split_caption("é" * 60 + "\n\nBody")
    for value in ("Title only", "Title\nwrapped\n\nBody", "Title\n\n"):
        with pytest.raises(ValueError):
            split_caption(value)


def test_posting_job_is_exact_and_authorization_does_not_imply_execution():
    task = post(publish=True)
    assert task["text_limits"] == {"title": 59}
    assert task["text_fields"]["description"] == "Exact description.\n\n#dating"
    assert task["authorization"]["limits"] == {"publish": 1}
    assert task["publication"]["required_text_fields"] == ["title", "description"]
    assert post()["authorization"]["limits"] == {"publish": 0}
    request = instruction_request({"instruction": task["goal"],
                                   "options": {k: v for k, v in task.items() if k != "goal"},
                                   "request_id": str(uuid.uuid4())})
    assert request["execute"] is False


@pytest.mark.parametrize("change", [{"slide_count": 5}, {"share_url": "http://example.com"},
    {"share_url": "https://user:password@example.com"}, {"account": "not a username"},
    {"publish": "yes"}, {"caption": "x" * 60 + "\n\nBody"}])
def test_bad_post_inputs_fail_before_any_device_call(change):
    with pytest.raises(ValueError):
        post(**change)


def test_public_actions_require_matching_account_preview_and_remaining_scope():
    frame = Frame.from_portal(png64())
    state = AuthorizationState(post(publish=True))
    publish = decision(frame, x=20, y=30, effect="publish")
    with pytest.raises(ValueError):
        state.check(publish)
    wrong = decision(frame, "confirm_account", app="TikTok", account="nomoreawkwardness", evidence="visible username")
    with pytest.raises(ValueError):
        state.check(wrong)
    account = {**wrong, "account": "try.flagd"}
    state.check(account)
    state.record_before_input(account)
    with pytest.raises(ValueError, match="preview"):
        state.check(publish)
    for field in ("title", "description"):
        typed = decision(frame, "type_text", text_key=field)
        state.record_before_input(typed)
        state.record_after_input(typed)
    preview = checked_preview(frame)
    validate_action(preview, frame, {})
    state.check(preview)
    state.record_before_input(preview)
    state.check(publish)
    state.record_before_input(publish)
    with pytest.raises(ValueError):
        state.check(publish)
    assert state.selected_sound == "Suggested track"


def test_preview_without_explicit_image_checks_cannot_unlock_posting():
    frame = Frame.from_portal(png64())
    legacy = decision(frame, "confirm_preview", slide_count=6, selected_sound="Suggested track",
                      evidence="checked slide order and exact copy")
    with pytest.raises(ValueError, match="explicit"):
        validate_action(legacy, frame, {})
    for field in CAROUSEL_CHECKS:
        with pytest.raises(ValueError, match="explicit"):
            validate_action(checked_preview(frame, **{field: "true"}), frame, {})


@pytest.mark.parametrize("observed", [
    {"first_slide_matches_source": False, "cover_matches_first_slide": False,
     "no_extra_images": False},  # collage inserted before the originals
    {"cover_matches_first_slide": False},  # cover alone is wrong
    {"no_extra_images": False},  # correct cover, but extra collage still included
    {"slide_order_matches_source": False},
    {"native_photo_carousel": False},
    {"title_matches_source": False},
    {"description_matches_source": False},
    {"slide_count": 7},  # extra photo independently fails the source count
])
def test_bad_carousel_preview_cannot_unlock_posting_during_recovery(tmp_path, observed):
    async def scenario():
        class Model:
            calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, "confirm_account", app="TikTok", account="try.flagd",
                                    evidence="@try.flagd visible"), {}
                if self.calls == 2:
                    return checked_preview(frame, **observed), {}
                if self.calls == 3:
                    assert task["execution_context"].get("decision_rejection")
                    # Even if the model tries Share without repairing the
                    # failed preview, recovery must dispatch no public input.
                    return decision(frame, x=20, y=30, effect="publish"), {}
                return decision(frame, "handoff", requires_review=True), {}
        model, portal = Model(), FakePortal()
        result = await run_agent(portal, model, post(publish=True), Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "blocked"
        assert model.calls == 6
        assert all(call[0] == "screenshot" for call in portal.calls)
        result = json.loads((tmp_path / "run" / "report.json").read_text())["result"]
        assert result["public_action_attempts"]["publish"] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [
    {"kind": "tap", "x": 20, "y": 30},
    {"kind": "swipe", "x": 20, "y": 30, "end_x": 10, "end_y": 30},
    {"kind": "type_text", "text_key": "title"},
])
def test_edit_after_preview_requires_another_check_before_posting(edit):
    frame = Frame.from_portal(png64())
    state = AuthorizationState(post(publish=True))
    account = decision(frame, "confirm_account", app="TikTok", account="try.flagd", evidence="@try.flagd")
    state.check(account)
    state.record_before_input(account)
    for field in ("title", "description"):
        state.record_after_input(decision(frame, "type_text", text_key=field))
    preview = checked_preview(frame)
    state.check(preview)
    state.record_before_input(preview)
    publish = decision(frame, x=20, y=30, effect="publish")
    state.check(publish)
    navigation = decision(frame, **edit)
    validate_action(navigation, frame, {"title": "Title"})
    state.check(navigation)
    state.record_before_input(navigation)
    with pytest.raises(ValueError, match="preview"):
        state.check(publish)
    assert state.used["publish"] == 0
    state.check(preview)
    state.record_before_input(preview)
    state.check(publish)


def test_preview_cannot_skip_acknowledged_title_and_description_entry():
    frame = Frame.from_portal(png64())
    state = AuthorizationState(post(publish=True))
    state.record_before_input(decision(frame, "confirm_account", app="TikTok", account="try.flagd", evidence="@try.flagd"))
    preview = checked_preview(frame)
    with pytest.raises(ValueError, match="fields entered"):
        state.check(preview)
    title = decision(frame, "type_text", text_key="title")
    state.record_before_input(title)
    with pytest.raises(ValueError, match="fields entered"):
        state.check(preview)  # intent is not acknowledgement
    state.record_after_input(title)
    with pytest.raises(ValueError, match="fields entered"):
        state.check(preview)  # description still missing
    state.record_after_input(decision(frame, "type_text", text_key="description"))
    state.check(preview)


def test_explicitly_waived_checks_still_require_caption_and_allow_only_one_post():
    task = post(publish=True)
    task["authorization"]["verification"] = {"account": False, "preview": False}
    validate_authorization(task["authorization"])
    state = AuthorizationState(task)
    frame = Frame.from_portal(png64())
    publish = decision(frame, x=20, y=30, effect="publish")
    with pytest.raises(ValueError, match="posting fields"):
        state.check(publish)
    for field in ("title", "description"):
        state.record_after_input(decision(frame, "type_text", text_key=field))
    state.check(publish)
    state.record_before_input(publish)
    with pytest.raises(ValueError, match="authorization"):
        state.check(publish)
    assert not state.confirmed and not state.preview and state.used["publish"] == 1
    with pytest.raises(ValueError, match="verification"):
        validate_authorization({**task["authorization"], "verification": {"account": "false", "preview": False}})


def test_next_decision_is_discarded_if_composer_appears_during_inference(tmp_path):
    async def scenario():
        buffer = io.BytesIO()
        Image.new("RGB", (1080, 1920), "white").save(buffer, format="PNG")
        composer = base64.b64encode(buffer.getvalue()).decode()
        class Portal(FakePortal):
            screenshots = 0
            async def call(self, method, params=None, *, mutation=False):
                self.calls.append((method, params, mutation))
                if method == "screenshot":
                    self.screenshots += 1
                    return png64() if self.screenshots == 1 else composer
                raise AssertionError("a stale Next tap must never be sent")
        class Model:
            calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, x=500, y=980, reason="Next in the editor"), {}
                assert any(item.get("action_discarded") is True for item in history)
                return decision(frame, "handoff", reason="Now the composer is visible; the old Next target is gone"), {}
        portal, model = Portal(), Model()
        result = await run_agent(portal, model, post(publish=True), Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "blocked"
        assert result["acknowledged_inputs"] == 0
        assert result["public_action_attempts"]["publish"] == 0
        events = [json.loads(line)["event"] for line in (tmp_path / "run" / "trace.jsonl").read_text().splitlines()]
        assert "stale_decision_discarded" in events
        assert "input_intent" not in events
    asyncio.run(scenario())


def test_unrelated_carousel_changes_do_not_hide_a_changed_tap_target():
    original = Image.new("RGB", (720, 1612), (20, 30, 40))
    slide_changed = original.copy()
    ImageDraw.Draw(slide_changed).rectangle((0, 200, 720, 1300), fill="white")
    button_changed = slide_changed.copy()
    ImageDraw.Draw(button_changed).rectangle((490, 1480, 570, 1540), fill="white")
    def frame(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Frame.from_portal(base64.b64encode(buffer.getvalue()).decode())
    a, b, c = frame(original), frame(slide_changed), frame(button_changed)
    assert a.materially_differs(b)
    assert not a.materially_differs(b, at=(530, 1510))
    assert a.materially_differs(c, at=(530, 1510))


def test_live_video_above_bottom_navigation_does_not_block_profile_tap():
    original = Image.new("RGB", (720, 1612), (20, 30, 40))
    moving_video = original.copy()
    ImageDraw.Draw(moving_video).rectangle((0, 80, 719, 1492), fill="white")
    def frame(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Frame.from_portal(base64.b64encode(buffer.getvalue()).decode())
    before, after = frame(original), frame(moving_video)
    assert before.materially_differs(after)
    assert not before.materially_differs(after, at=(646, 1531))


def test_next_becoming_post_is_still_detected_inside_the_smaller_control_window():
    from PIL import ImageFont
    font = ImageFont.load_default(size=30)
    def button_frame(label):
        image = Image.new("RGB", (720, 1612), "white")
        ImageDraw.Draw(image).text((630, 1514), label, font=font, fill="black", anchor="mm")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Frame.from_portal(base64.b64encode(buffer.getvalue()).decode())
    assert button_frame("Next").materially_differs(button_frame("Post"), at=(630, 1514))


def test_home_swipe_reaches_phone_even_when_carousel_changes(tmp_path):
    async def scenario():
        buffer = io.BytesIO()
        Image.new("RGB", (1080, 1920), "white").save(buffer, format="PNG")
        changed_screen = base64.b64encode(buffer.getvalue()).decode()
        class Portal(FakePortal):
            screenshots = 0
            async def call(self, method, params=None, *, mutation=False):
                self.calls.append((method, params, mutation))
                if method == "screenshot":
                    self.screenshots += 1
                    return png64() if self.screenshots == 1 else changed_screen
                return "accepted"
        class Model:
            calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, "swipe", x=200, y=1020, end_x=200, end_y=500, duration_ms=300), {}
                assert history[-1]["action"]["kind"] == "swipe"
                assert history[-1]["input_acknowledged"] is True
                return decision(frame, "done"), {}
        portal = Portal()
        result = await run_agent(portal, Model(), post(), Journal(tmp_path / "run"), execute=True)
        assert result["acknowledged_inputs"] == 1
        assert [c[0] for c in portal.calls] == ["screenshot", "swipe", "screenshot"]
        assert portal.calls[1][2] is True
    asyncio.run(scenario())


def test_comment_generation_needs_context_and_authorization():
    frame = Frame.from_portal(png64())
    comment = decision(frame, "type_comment", comment="What helped you find the words?",
                       visible_context="I never know what to say")
    validate_action(comment, frame, {})
    state = AuthorizationState(browse_task(app="TikTok", account="try.flagd", keywords=["social skills"], max_comments=1))
    with pytest.raises(ValueError):
        state.check(comment)
    checkpoint = decision(frame, "confirm_account", app="TikTok", account="try.flagd", evidence="@try.flagd visible")
    state.check(checkpoint)
    state.record_before_input(checkpoint)
    state.check(comment)
    with pytest.raises(ValueError, match="context"):
        validate_action({**comment, "visible_context": ""}, frame, {})
    assert state.used["comment"] == 0  # typing is not posting


def test_timed_browsing_uses_compact_decisions_and_no_ai_during_playback(tmp_path):
    async def scenario():
        class BrowseModel:
            def __init__(self):
                self.calls = 0
                self.contexts = []
            async def plan(self, *_):
                raise AssertionError("browsing must skip the research planner")
            async def assess(self, *_):
                raise AssertionError("browsing must skip the research notebook")
            async def decide(self, task, frame, history):
                self.calls += 1
                self.contexts.append(task["execution_context"])
                return (decision(frame, "confirm_account", app="TikTok", account="try.flagd", evidence="@try.flagd")
                        if self.calls == 1 else decision(frame, "watch", duration_ms=15000)), {}
        model, portal = BrowseModel(), FakePortal()
        task = browse_task(app="TikTok", account="try.flagd", keywords=["conversation"], seconds=1)
        running = asyncio.create_task(run_agent(portal, model, task, Journal(tmp_path / "run"), execute=True))
        while model.calls < 2:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        assert model.calls == 2
        result = await running
        assert result["status"] == "duration_limit" and result["account_confirmed"]
        assert result["watch_seconds"] <= 1.1
        assert result["verified_success"] is False
        assert [c[0] for c in portal.calls] == ["screenshot", "screenshot"]
        assert model.contexts[0]["timer_started"] is False
        assert model.contexts[1]["timer_started"] is True
    asyncio.run(scenario())


def test_browse_counts_model_latency_toward_one_short_watch_and_swipe(tmp_path):
    async def scenario():
        class BrowseModel:
            def __init__(self):
                self.calls = 0
            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, "confirm_account", app="TikTok", account="try.flagd",
                                    evidence="@try.flagd active profile"), {}
                await asyncio.sleep(0.35)  # Video plays while the model decides.
                return decision(frame, "swipe", x=280, y=850, end_x=280, end_y=200,
                                duration_ms=100, playback_ms=1000), {}
        model, portal = BrowseModel(), FakePortal()
        task = browse_task(app="TikTok", account="try.flagd", keywords=["conversation"], seconds=10)
        task["max_steps"] = 2
        started = time.monotonic()
        result = await run_agent(portal, model, task, Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "step_limit"
        assert model.calls == 2  # No separate model call between watching and swiping.
        assert [c[0] for c in portal.calls] == ["screenshot", "screenshot", "swipe", "screenshot"]
        assert 0.85 <= result["watch_seconds"] <= 1.3
        assert time.monotonic() - started < 1.8
    asyncio.run(scenario())


def test_browse_rejects_repeated_watch_until_navigation(tmp_path):
    async def scenario():
        class BrowseModel:
            def __init__(self):
                self.calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                if self.calls == 1:
                    return decision(frame, "confirm_account", app="TikTok",
                                    account="try.flagd", evidence="@try.flagd profile"), {}
                if self.calls in (2, 3, 5):
                    return decision(frame, "watch", duration_ms=100), {}
                return decision(frame, "swipe", x=280, y=850, end_x=280,
                                end_y=200, duration_ms=100), {}

        portal = FakePortal()
        task = browse_task(app="TikTok", account="try.flagd", keywords=["conversation"], seconds=10)
        task["max_steps"] = 5
        result = await run_agent(portal, BrowseModel(), task, Journal(tmp_path / "run"), execute=True)
        events = [json.loads(line) for line in (tmp_path / "run" / "trace.jsonl").read_text().splitlines()]
        assert result["status"] == "step_limit"
        assert sum(event["event"] == "repeated_watch_rejected" for event in events) == 1
        assert len([call for call in portal.calls if call[0] == "swipe"]) == 1
        assert 0.2 <= result["watch_seconds"] < 1

    asyncio.run(scenario())


def test_cancel_playback_does_not_send_a_phone_input_or_make_another_ai_call(tmp_path):
    async def scenario():
        started = asyncio.Event()
        class Model:
            async def decide(self, task, frame, history):
                if not history:
                    return decision(frame, "confirm_account", app="TikTok", account="try.flagd", evidence="@try.flagd"), {}
                started.set()
                return decision(frame, "watch", duration_ms=15000), {}
        portal = FakePortal()
        task = browse_task(app="TikTok", account="try.flagd", keywords=["conversation"])
        running = asyncio.create_task(run_agent(portal, Model(), task, Journal(tmp_path / "run"), execute=True))
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert all(c[0] == "screenshot" for c in portal.calls)
        assert json.loads((tmp_path / "run" / "report.json").read_text())["result"]["status"] == "cancelled"
    asyncio.run(scenario())


def test_publication_source_is_deduplicated_across_new_requests_and_restart(tmp_path):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        task = post(publish=True)
        kwargs = {"execute": True, "request_id": str(uuid.uuid4())}
        receipt = controller.submit(task, **kwargs)
        assert controller.submit(task, **kwargs)["duplicate"] is True
        with pytest.raises(ControlError, match="already has a publication job"):
            controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
        await controller.close()
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            with pytest.raises(ControlError, match="already has a publication job"):
                controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_explicit_continuation_requires_terminal_receipts_with_zero_post_attempts(tmp_path):
    async def scenario():
        controller = TaskController(host_with(ConnectedPortal()), NavigationModel(), tmp_path)
        try:
            task = post(publish=True)
            receipt = controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
            await asyncio.wait_for(asyncio.shield(controller.active), 5)
            old_id = receipt["job"]["id"]
            continuation = {**task, "resume_from_job_id": old_id}
            controller.jobs[old_id]["result"]["public_action_attempts"]["publish"] = 1
            with pytest.raises(ControlError, match="already has a publication job"):
                controller.submit(continuation, execute=True, request_id=str(uuid.uuid4()))
            controller.jobs[old_id]["result"]["public_action_attempts"]["publish"] = 0
            controller.jobs[old_id]["state"] = "interrupted"
            with pytest.raises(ControlError):
                controller.submit(continuation, execute=True, request_id=str(uuid.uuid4()))
            controller.jobs[old_id]["state"] = "finished"
            again = controller.submit(continuation, execute=True, request_id=str(uuid.uuid4()))
            assert again["job"]["id"] != old_id
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_california_schedule_changes_utc_hour_after_dst():
    before = next_california_slot(datetime(2026, 10, 31, 0, tzinfo=timezone.utc), "09:00")
    after = next_california_slot(datetime(2026, 11, 2, 0, tzinfo=timezone.utc), "09:00")
    assert before.astimezone(timezone.utc).hour == 16
    assert after.astimezone(timezone.utc).hour == 17
    gap = next_california_slot(datetime(2027, 3, 14, 8, tzinfo=timezone.utc), "02:30")
    assert gap.day == 15  # nonexistent spring-forward time skipped


def test_schedule_is_explicit_disabled_and_splits_daily_minutes():
    config = {"enabled": False, "timezone": "America/Los_Angeles", "accounts": [{
        "app": "TikTok", "account": "try.flagd", "keywords": ["conversation"],
        "minutes_per_day": 30, "times": ["09:00", "18:00"],
        "max_likes_per_session": 3, "max_comments_per_session": 1,
    }]}
    preview = schedule_preview(config, datetime(2026, 10, 13, 0, tzinfo=timezone.utc))
    assert preview["enabled"] is False
    assert [s["session_seconds"] for s in preview["next_slots"]] == [900, 900]
    invalid = copy.deepcopy(config)
    invalid["accounts"][0]["times"] = ["25:00", "18:00"]
    with pytest.raises(ValueError):
        validate_schedule(invalid)


def test_instruction_api_rejects_unauthenticated_and_malformed_input_without_ai(monkeypatch, tmp_path):
    calls = []
    async def controller_request(path, payload):
        calls.append(payload)
        return {"connected": True}
    monkeypatch.setattr("flagd_phone.gateway.control_request", controller_request)
    with instruction_server(tmp_path / "socket", "t" * 32, port=0) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(trust_env=False) as client:
            assert client.get(base + "/status").status_code == 401
            headers = {"Authorization": "Bearer " + "t" * 32}
            assert client.post(base + "/tasks", headers=headers, json={"instruction": "Browse"}).status_code == 400
            assert calls == []
            body = {"instruction": "Open TikTok. Leave the profile unchanged.", "request_id": str(uuid.uuid4())}
            assert client.post(base + "/tasks", headers=headers, json=body).status_code == 202
            assert calls[0]["task"]["goal"] == body["instruction"]
            assert calls[0]["execute"] is False
    with pytest.raises(ValueError, match="loopback"):
        with instruction_server(tmp_path / "socket", "t" * 32, bind="0.0.0.0"):
            pass


def test_api_client_preserves_instruction_receipt_and_cancel_routes(monkeypatch, tmp_path):
    calls = []
    async def controller_request(path, payload):
        calls.append(payload)
        return {"ok": True, "job": {"id": "a" * 32, "state": "running"}}
    monkeypatch.setattr("flagd_phone.gateway.control_request", controller_request)
    with instruction_server(tmp_path / "socket", "t" * 32, port=0) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        async def scenario():
            payload = {"operation": "submit", "task": {"goal": "Open TikTok"},
                       "execute": True, "request_id": str(uuid.uuid4())}
            assert (await api_request(base, "t" * 32, payload))["job"]["id"] == "a" * 32
            await api_request(base, "t" * 32, {"operation": "status", "job_id": "a" * 32})
            await api_request(base, "t" * 32, {"operation": "cancel", "job_id": "a" * 32})
            with pytest.raises(ValueError, match="loopback"):
                await api_request("http://192.168.1.188:8788", "t" * 32, payload)
            with pytest.raises(ValueError):
                await api_request(base, "t" * 32, {"operation": "capture"})
            assert calls[0]["request_id"] == payload["request_id"]
            assert calls[0]["task"]["goal"] == "Open TikTok"
            assert [x["operation"] for x in calls] == ["submit", "status", "cancel"]
        asyncio.run(scenario())

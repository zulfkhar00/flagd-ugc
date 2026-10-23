from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
from PIL import Image

from flagd_phone.model import GeminiVision
from flagd_phone.runner import Journal, run_agent
from flagd_phone.semantic import SemanticScreen
from flagd_phone.vision import Frame


def frame() -> Frame:
    output = io.BytesIO()
    Image.new("RGB", (1080, 1920), (45, 51, 58)).save(output, format="PNG")
    return Frame.from_portal(base64.b64encode(output.getvalue()).decode())


def state() -> dict:
    return {"phone_state": {"packageName": "com.example.app"}, "a11y_tree": json.dumps({
        "children": [
            {"boundsInScreen": {"left": 0, "top": 100, "right": 1080, "bottom": 1600},
             "className": "android.widget.ScrollView", "isScrollable": True,
             "children": [
                 {"boundsInScreen": {"left": 90, "top": 250, "right": 550, "bottom": 350},
                  "text": "Search", "isClickable": True, "isVisibleToUser": True},
                 {"boundsInScreen": {"left": 90, "top": 420, "right": 800, "bottom": 520},
                  "text": "Post", "isClickable": True},
                 {"boundsInScreen": {"left": 90, "top": 600, "right": 800, "bottom": 700},
                  "text": "Ignore me", "isClickable": True, "isVisibleToUser": False},
                 {"boundsInScreen": {"left": 90, "top": 800, "right": 800, "bottom": 900},
                  "text": "secret", "isEditable": True, "isFocused": True,
                  "isPassword": True},
             ]},
        ]})}


def choice(operation: str, target_id: int) -> dict:
    return {"operation": operation, "target_id": target_id, "text_key": "", "query": "",
            "duration_ms": 500, "reason": "Open visible search", "expected_change": "Search opens",
            "confidence": 0.94}


def search_screen(text: str, *, focused: bool = True) -> SemanticScreen:
    return SemanticScreen.from_portal({"a11y_tree": {"children": [
        {"boundsInScreen": [20, 100, 900, 200], "className": "android.widget.EditText",
         "text": text, "resourceId": "com.example:id/search_input", "isEditable": True,
         "isFocused": focused},
        {"boundsInScreen": [900, 100, 1050, 200], "text": "Clear", "isClickable": True},
        {"boundsInScreen": [20, 250, 500, 350], "text": "Top", "isClickable": True},
    ]}}, frame())


def test_browse_keyword_can_use_focused_search_field_without_name_matching():
    current = frame()
    task = {"mode": "browse", "text_fields": {"keyword_1": "dating conversation tips"}}
    selected = {**choice("type_text", 0), "text_key": "keyword_1"}
    action = GeminiVision._semantic_action(selected, search_screen("Search"), current, task)
    assert action is not None and action["kind"] == "type_text"
    assert GeminiVision._semantic_action(selected, search_screen("Search", focused=False), current, task) is None
    assert GeminiVision._semantic_action(selected, search_screen("Search"), current,
                                         {"mode": "publish", "text_fields": task["text_fields"]}) is None


def test_search_submission_needs_visible_entered_browse_keyword():
    current = frame()
    task = {"mode": "browse", "text_fields": {"keyword_1": "dating conversation tips"}}
    selected = choice("submit_search", 0)
    action = GeminiVision._semantic_action(selected,
                                          search_screen("dating conversation tips"), current, task)
    assert action is not None and action["kind"] == "submit_search"
    assert GeminiVision._semantic_action(selected, search_screen("Search"), current, task) is None
    assert GeminiVision._semantic_action(selected,
                                         search_screen("dating conversation tips", focused=False),
                                         current, task) is None


def test_system_lock_screen_skips_text_model():
    async def scenario():
        current = frame()
        raw = state()
        raw["phone_state"]["packageName"] = "com.android.systemui"
        screen = SemanticScreen.from_portal(raw, current)
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(any("inlineData" in part for part in body["contents"][0]["parts"]))
            output = {"action": {"kind": "wait", "frame_id": current.id,
                     "reason": "Inspect the lock screen", "expected_change": "Screen remains visible",
                     "confidence": 0.9, "requires_review": False, "effect": "none",
                     "duration_ms": 500}}
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output)}]}}],
                "usageMetadata": {"promptTokenCount": 200, "totalTokenCount": 220}})

        model = GeminiVision("test-key", "gemini-3.8-flash",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            action, metadata = await model.decide_hybrid({"goal": "Unlock the phone"}, current, [], screen)
            assert action["kind"] == "wait"
            assert metadata["perception"] == "vision"
            assert requests == [True]
        finally:
            await model.close()

    asyncio.run(scenario())


def test_portal_state_is_compact_and_passwords_are_redacted():
    screen = SemanticScreen.from_portal(state(), frame())
    assert screen.app == "com.example.app"
    assert screen.useful
    assert len(screen.elements) == 4
    assert "Search" in screen.compact()
    assert "Ignore me" not in screen.compact()
    assert "secret" not in screen.compact()
    assert "[password field]" in screen.compact()
    assert screen.target(2).label == "Search"


def test_semantic_tap_uses_tree_coordinates_without_sending_image():
    async def scenario():
        current = frame()
        screen = SemanticScreen.from_portal(state(), current)
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            assert all("inlineData" not in part for part in body["contents"][0]["parts"])
            assert request.url.path.endswith("/gemini-3.1-flash-lite:generateContent")
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(choice("tap", 2))}]}}],
                "usageMetadata": {"promptTokenCount": 500, "totalTokenCount": 540}})

        model = GeminiVision("test-key", "gemini-3.8-flash", semantic_model="gemini-3.1-flash-lite",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            action, metadata = await model.decide_hybrid({"goal": "Open search"}, current, [], screen)
            assert action["kind"] == "tap"
            assert current.to_device(action["x"], action["y"]) == screen.target(2).center
            assert metadata["perception"] == "semantic"
            assert metadata["api_calls"] == 1
            assert len(requests) == 1
        finally:
            await model.close()

    asyncio.run(scenario())


def test_public_control_requires_visual_fallback():
    async def scenario():
        current = frame()
        screen = SemanticScreen.from_portal(state(), current)
        requests = []

        def handler(request):
            body = json.loads(request.content)
            has_image = any("inlineData" in part for part in body["contents"][0]["parts"])
            requests.append(has_image)
            if has_image:
                output = {"action": {"kind": "wait", "frame_id": current.id,
                          "reason": "Need visual evidence", "expected_change": "Inspect later",
                          "confidence": 0.9, "requires_review": False, "effect": "none",
                          "duration_ms": 500}}
            else:
                output = choice("tap", 3)
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output)}]}}],
                "usageMetadata": {"promptTokenCount": 200, "totalTokenCount": 220}})

        model = GeminiVision("test-key", "gemini-3.8-flash",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            action, metadata = await model.decide_hybrid({"goal": "Post the draft"}, current, [], screen)
            assert action["kind"] == "wait"
            assert metadata["perception"] == "vision_fallback"
            assert metadata["api_calls"] == 2
            assert requests == [False, True]
        finally:
            await model.close()

    asyncio.run(scenario())


def test_opt_in_runner_reads_state_and_keeps_vision_fallback(tmp_path, monkeypatch):
    async def scenario():
        class Portal:
            def __init__(self):
                self.calls = []

            async def call(self, method, params=None, *, mutation=False):
                self.calls.append(method)
                if method == "screenshot":
                    return base64.b64encode(frame().png).decode()
                if method == "state":
                    return state()
                raise AssertionError(method)

        class Model:
            def __init__(self):
                self.hybrid = 0
                self.visual = 0

            async def decide_hybrid(self, task, current, history, screen):
                self.hybrid += 1
                assert screen.useful
                return ({"kind": "wait", "frame_id": current.id, "reason": "read tree",
                         "expected_change": "nothing", "confidence": 0.9,
                         "requires_review": False, "duration_ms": 500}, {"perception": "semantic"})

            async def decide(self, task, current, history):
                self.visual += 1
                return ({"kind": "wait", "frame_id": current.id, "reason": "read pixels",
                         "expected_change": "nothing", "confidence": 0.9,
                         "requires_review": False, "duration_ms": 500}, {"perception": "vision"})

        monkeypatch.setenv("PHONE_SEMANTIC_NAV", "1")
        portal, model = Portal(), Model()
        result = await run_agent(portal, model, {"goal": "Open search"}, Journal(tmp_path / "on"))
        assert result["status"] == "preview"
        assert portal.calls == ["screenshot", "state"]
        assert (model.hybrid, model.visual) == (1, 0)

        monkeypatch.setenv("PHONE_SEMANTIC_NAV", "0")
        portal, model = Portal(), Model()
        result = await run_agent(portal, model, {"goal": "Open search"}, Journal(tmp_path / "off"))
        assert result["status"] == "preview"
        assert portal.calls == ["screenshot"]
        assert (model.hybrid, model.visual) == (0, 1)

        monkeypatch.setenv("PHONE_SEMANTIC_NAV", "1")
        class EmptyStatePortal(Portal):
            async def call(self, method, params=None, *, mutation=False):
                if method == "state":
                    self.calls.append(method)
                    return {"phone_state": {"packageName": "com.example.app"}}
                return await super().call(method, params, mutation=mutation)

        portal, model = EmptyStatePortal(), Model()
        result = await run_agent(portal, model, {"goal": "Open search"}, Journal(tmp_path / "empty"))
        assert result["status"] == "preview"
        assert portal.calls == ["screenshot", "state"]
        assert (model.hybrid, model.visual) == (0, 1)

    asyncio.run(scenario())

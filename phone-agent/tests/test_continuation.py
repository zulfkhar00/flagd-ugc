from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
from PIL import Image

from flagd_phone.continuation import resolve_followup, validate_followup
from flagd_phone.model import GeminiVision
from flagd_phone.runner import Journal, run_agent
from flagd_phone.semantic import SemanticScreen
from flagd_phone.vision import Frame


def frame() -> Frame:
    output = io.BytesIO()
    Image.new("RGB", (1080, 1920), (42, 47, 52)).save(output, format="PNG")
    return Frame.from_portal(base64.b64encode(output.getvalue()).decode())


def action(current: Frame, kind: str = "tap", **fields):
    return {"kind": kind, "frame_id": current.id, "reason": "Open search",
            "expected_change": "Search editor appears", "confidence": 0.95,
            "requires_review": False, "effect": "none", **fields}


def state(*, editor=False, app="com.example.app"):
    element = ({"boundsInScreen": [50, 100, 900, 200], "text": "Search",
                "resourceId": "com.example.app:id/search_field", "isEditable": True,
                "isFocused": True} if editor else
               {"boundsInScreen": [50, 100, 900, 200], "text": "Search",
                "isClickable": True})
    return {"phone_state": {"packageName": app}, "a11y_tree": {"children": [element]}}


def test_guard_rejects_public_and_ambiguous_followups():
    current = frame()
    first = action(current, x=40, y=40)
    assert validate_followup({"operation": "tap", "target_label": "Post", "query": "",
                              "reason": "continue"}, first) is None
    assert validate_followup({"operation": "tap", "target_label": "Next", "query": "",
                              "reason": "continue"}, first) is None
    assert validate_followup({"operation": "type_query", "target_label": "Search",
                              "query": "first date tips", "reason": "search"}, first)
    spec = {"operation": "type_query", "target_label": "Search",
            "query": "first date tips", "reason": "search"}
    wrong_app = SemanticScreen.from_portal(state(editor=True, app="com.other.app"), current)
    assert resolve_followup(spec, wrong_app, current, expected_app="com.example.app",
                            text_fields={}) is None
    no_editor = SemanticScreen.from_portal(state(), current)
    assert resolve_followup(spec, no_editor, current, expected_app="com.example.app",
                            text_fields={}) is None
    exact = {"keyword_1": "first date tips"}
    focused = SemanticScreen.from_portal(state(editor=True), current)
    assert resolve_followup(spec, focused, current, expected_app="com.example.app",
                            text_fields=exact) is None
    first_text = action(current, "type_text", text_key="keyword_1")
    submit = {"operation": "submit_search", "target_label": "first date tips",
              "query": "first date tips", "reason": "Submit the search"}
    assert validate_followup(submit, first_text, exact) == submit
    assert validate_followup(submit, first_text, {"keyword_1": "different query"}) is None


def test_guarded_followup_skips_one_model_round_trip(tmp_path, monkeypatch):
    async def scenario():
        class Portal:
            def __init__(self):
                self.stage = 0
                self.calls = []

            async def call(self, method, params=None, *, mutation=False):
                self.calls.append(method)
                if method == "screenshot":
                    return base64.b64encode(frame().png).decode()
                if method == "state":
                    return state(editor=self.stage >= 1)
                if method == "tap":
                    self.stage = 1
                    return "ok"
                if method == "keyboard/input":
                    self.stage = 2
                    return "ok"
                raise AssertionError(method)

        class Model:
            def __init__(self):
                self.calls = 0

            async def decide(self, task, current, history):
                self.calls += 1
                assert task["execution_context"]["guarded_continuations"]
                if self.calls == 1:
                    return action(current, x=100, y=100), {"guarded_followup": {
                        "operation": "type_query", "target_label": "Search",
                        "query": "first date tips", "reason": "Enter the requested search"}}
                assert history[-1]["action"]["kind"] == "type_query"
                return action(current, "done"), {}

        monkeypatch.setenv("PHONE_GUARDED_CONTINUATIONS", "1")
        monkeypatch.setenv("PHONE_SEMANTIC_NAV", "0")
        portal, model = Portal(), Model()
        result = await run_agent(portal, model, {"goal": "Search for first date tips",
                                                  "max_steps": 3},
                                 Journal(tmp_path / "run"), execute=True)
        assert result["status"] == "completion_candidate"
        assert result["model_calls"] == model.calls == 2
        assert portal.calls.count("tap") == portal.calls.count("keyboard/input") == 1
        events = [json.loads(line)["event"] for line in (tmp_path / "run" / "trace.jsonl").read_text().splitlines()]
        assert "guarded_followup_check" in events

    asyncio.run(scenario())


def test_gemini_guarded_response_keeps_action_and_followup():
    async def scenario():
        current = frame()

        def handler(request):
            body = json.loads(request.content)
            assert set(body["generationConfig"]["responseJsonSchema"]["properties"]) == {"action", "followup"}
            output = {"action": {"kind": "tap", "frame_id": current.id,
                     "reason": "Open the visible search field", "expected_change": "Search editor opens",
                     "confidence": 0.95, "requires_review": False, "effect": "none",
                     "x": 100, "y": 100},
                     "followup": {"operation": "type_query", "target_label": "Search",
                                  "query": "first date tips", "reason": "Enter a search query"}}
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output)}]}}],
                "usageMetadata": {"promptTokenCount": 400, "totalTokenCount": 450}})

        model = GeminiVision("test-key", "gemini-3.8-flash",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            selected, metadata = await model.decide(
                {"goal": "Search for first date tips", "execution_context": {
                    "guarded_continuations": True}}, current, [])
            assert selected["kind"] == "tap"
            assert metadata["guarded_followup"]["operation"] == "type_query"
        finally:
            await model.close()

    asyncio.run(scenario())

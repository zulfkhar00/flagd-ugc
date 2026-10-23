from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from flagd_phone.continuation import resolve_followup, validate_followup
from flagd_phone.model import GeminiVision
from flagd_phone.runner import Journal, run_agent, validate_task
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
    tap_search = {"operation": "tap", "target_label": "Search",
                  "query": "first date tips", "reason": "Search the exact query"}
    assert validate_followup(tap_search, first_text, exact) == tap_search
    search_result = {"phone_state": {"packageName": "com.example.app"}, "a11y_tree": {
        "children": [
            {"boundsInScreen": [20, 100, 800, 200], "text": "first date tips",
             "isEditable": True, "isFocused": True},
            {"boundsInScreen": [850, 100, 1050, 200], "text": "Search", "isClickable": True},
        ]}}
    observed = SemanticScreen.from_portal(search_result, current)
    assert resolve_followup(tap_search, observed, current,
                            expected_app="com.example.app", text_fields=exact)["kind"] == "tap"
    search_result["a11y_tree"]["children"][0]["text"] = "different query"
    observed = SemanticScreen.from_portal(search_result, current)
    assert resolve_followup(tap_search, observed, current,
                            expected_app="com.example.app", text_fields=exact) is None


def test_exact_text_followup_needs_matching_focused_field_hint():
    current = frame()
    first = action(current, x=100, y=100)
    fields = {"title": "One reply. Twelve drafts.", "description": "Long supplied copy"}
    spec = {"operation": "type_text", "target_label": "title",
            "query": "title", "reason": "Enter the supplied title"}
    assert validate_followup(spec, first, fields) == spec
    assert validate_followup({**spec, "query": "description"}, first, fields) is None
    assert validate_followup({**spec, "target_label": "Post"}, first, fields) is None

    def editor(*, hint, text="", password=False, duplicate=False):
        nodes = [{"boundsInScreen": [50, 100, 900, 200], "text": text,
                  "hint": hint, "resourceId": "com.example.app:id/editor",
                  "isEditable": True, "isFocused": True, "isPassword": password}]
        if duplicate:
            nodes.append({**nodes[0], "boundsInScreen": [50, 300, 900, 400]})
        return SemanticScreen.from_portal({"phone_state": {"packageName": "com.example.app"},
                                           "a11y_tree": {"children": nodes}}, current)

    selected = resolve_followup(spec, editor(hint="Add a catchy title"), current,
                                expected_app="com.example.app", text_fields=fields)
    assert selected["kind"] == "type_text" and selected["text_key"] == "title"
    # Matching a word in entered body copy is insufficient; the hint or view ID
    # must identify the editor itself.
    assert resolve_followup(spec, editor(hint="Write a description", text="title"), current,
                            expected_app="com.example.app", text_fields=fields) is None
    assert resolve_followup(spec, editor(hint="Add a catchy title", text="existing title"), current,
                            expected_app="com.example.app", text_fields=fields) is None
    assert resolve_followup(spec, editor(hint="Add a catchy title", password=True), current,
                            expected_app="com.example.app", text_fields=fields) is None
    assert resolve_followup(spec, editor(hint="Add a catchy title", duplicate=True), current,
                            expected_app="com.example.app", text_fields=fields) is None
    assert resolve_followup(spec, editor(hint="Add a catchy title"), current,
                            expected_app="com.other.app", text_fields=fields) is None

    url_fields = {"volume_url": "https://example.test/post"}
    url_spec = {"operation": "type_text", "target_label": "url",
                "query": "volume_url", "reason": "Open supplied URL"}
    diagnostics = {}
    url_screen = editor(hint="Search Google or type URL", text="Search Google or type URL")
    selected = resolve_followup(url_spec, url_screen, current,
                                expected_app="com.example.app", text_fields=url_fields,
                                diagnostics=diagnostics)
    assert selected["text_key"] == "volume_url"
    assert diagnostics == {"focused_editor_count": 1, "matching_editor_count": 1}


def test_publish_task_opt_in_types_exact_field_without_second_model_call(tmp_path, monkeypatch):
    async def scenario():
        class Portal:
            stage = 0
            calls = []

            async def call(self, method, params=None, *, mutation=False):
                self.calls.append(method)
                if method == "screenshot":
                    return base64.b64encode(frame().png).decode()
                if method == "state":
                    hint = "Add a catchy title" if self.stage else ""
                    return {"phone_state": {"packageName": "com.example.app"},
                            "a11y_tree": {"children": [{
                                "boundsInScreen": [50, 100, 900, 200], "hint": hint,
                                "isEditable": bool(self.stage), "isFocused": bool(self.stage)}]}}
                if method == "tap":
                    self.stage = 1
                    return "ok"
                if method == "keyboard/input":
                    self.stage = 2
                    return "ok"
                raise AssertionError(method)

        class Model:
            calls = 0

            async def decide(self, task, current, history):
                self.calls += 1
                assert self.calls == 1
                assert task["execution_context"]["guarded_continuations"]
                return action(current, x=100, y=100), {"guarded_followup": {
                    "operation": "type_text", "target_label": "title", "query": "title",
                    "reason": "Enter exact title"}}

        monkeypatch.setenv("PHONE_GUARDED_CONTINUATIONS", "0")
        portal, model = Portal(), Model()
        task = {"mode": "publish", "goal": "Prepare a draft", "max_steps": 2,
                "guarded_continuations": True, "text_fields": {"title": "Exact title"},
                "authorization": {"app": "TikTok", "account": "try.flagd",
                                  "limits": {"publish": 0}},
                "publication": {"source_id": "test", "slide_count": 6,
                                "required_text_fields": ["title"]}}
        result = await run_agent(portal, model, task, Journal(tmp_path / "publish"), execute=True)
        assert result["status"] == "step_limit"
        assert result["model_calls"] == model.calls == 1
        assert portal.calls.count("tap") == portal.calls.count("keyboard/input") == 1
        events = [json.loads(line)["event"] for line in
                  (tmp_path / "publish" / "trace.jsonl").read_text().splitlines()]
        assert "guarded_followup_check" in events

    asyncio.run(scenario())


def test_guarded_pilot_can_be_selected_per_task():
    assert validate_task({"goal": "Search", "guarded_continuations": True})[
        "guarded_continuations"] is True
    with pytest.raises(ValueError):
        validate_task({"goal": "Search", "guarded_continuations": "true"})


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


def test_gemini_publish_decision_accepts_exact_text_followup():
    async def scenario():
        current = frame()

        def handler(request):
            schema = json.loads(request.content)["generationConfig"]["responseJsonSchema"]
            followup = schema["properties"]["followup"]["properties"]
            assert "type_text" in followup["operation"]["enum"]
            output = {"action": action(current, x=100, y=100),
                      "followup": {"operation": "type_text", "target_label": "title",
                                   "query": "title", "reason": "Enter supplied title"}}
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output)}]}}],
                "usageMetadata": {"promptTokenCount": 400, "totalTokenCount": 450}})

        model = GeminiVision("test-key", "gemini-3.8-flash",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            selected, metadata = await model.decide(
                {"goal": "Enter the exact title", "mode": "publish",
                 "text_fields": {"title": "Exact title"},
                 "execution_context": {"guarded_continuations": True}}, current, [])
            assert selected["kind"] == "tap"
            assert metadata["guarded_followup"]["query"] == "title"
        finally:
            await model.close()

    asyncio.run(scenario())


def test_planned_agent_can_use_guarded_followup(tmp_path, monkeypatch):
    async def scenario():
        class Portal:
            stage = 0

            async def call(self, method, params=None, *, mutation=False):
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
            assessments = 0

            async def plan(self, task):
                return {"subgoals": [{"id": "search", "description": "Search"}],
                        "success_criteria": ["Results visible"], "search_queries": []}, {}

            async def assess(self, task, current, history, memory):
                self.assessments += 1
                assert task["execution_context"]["guarded_continuations"]
                selected = (action(current, x=100, y=100) if self.assessments == 1
                            else action(current, "done"))
                assessment = {"action": selected, "summary": "Search navigation",
                              "findings": [], "visited_sources": [], "progress": [],
                              "answer": "", "answer_finding_ids": []}
                metadata = ({"guarded_followup": {"operation": "type_query",
                            "target_label": "Search", "query": "first date tips",
                            "reason": "Enter search text"}} if self.assessments == 1 else {})
                return assessment, metadata

        monkeypatch.setenv("PHONE_GUARDED_CONTINUATIONS", "0")
        monkeypatch.setenv("PHONE_SEMANTIC_NAV", "0")
        model = Model()
        result = await run_agent(Portal(), model, {"goal": "Search for first date tips",
                                                   "max_steps": 3,
                                                   "guarded_continuations": True},
                                 Journal(tmp_path / "planned"), execute=True)
        assert result["status"] == "completion_candidate"
        assert model.assessments == 2
        assert result["model_calls"] == 3  # plan plus two assessments; no third assessment for typing

    asyncio.run(scenario())


def test_gemini_assessment_keeps_notebook_and_guarded_followup():
    async def scenario():
        current = frame()

        def handler(request):
            schema = json.loads(request.content)["generationConfig"]["responseJsonSchema"]
            assert "followup" in schema["properties"]
            output = {"action": {"kind": "tap", "frame_id": current.id,
                     "reason": "Open search", "expected_change": "Search editor opens",
                     "confidence": 0.95, "requires_review": False, "effect": "none",
                     "x": 100, "y": 100},
                     "summary": "At search navigation", "findings": [], "visited_sources": [],
                     "progress": [], "answer": "", "answer_finding_ids": [],
                     "followup": {"operation": "type_query", "target_label": "Search",
                                  "query": "first date tips", "reason": "Enter search text"}}
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output)}]}}],
                "usageMetadata": {"promptTokenCount": 400, "totalTokenCount": 450}})

        model = GeminiVision("test-key", "gemini-3.8-flash",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            assessment, metadata = await model.assess(
                {"goal": "Search", "execution_context": {"guarded_continuations": True}},
                current, [], {"plan": {"subgoals": []}, "findings": []})
            assert assessment["summary"] == "At search navigation"
            assert "followup" not in assessment
            assert metadata["guarded_followup"]["operation"] == "type_query"
        finally:
            await model.close()

    asyncio.run(scenario())

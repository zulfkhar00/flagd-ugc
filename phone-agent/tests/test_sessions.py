from __future__ import annotations

import asyncio
import base64
import copy
import json

import httpx
import pytest

from flagd_phone.cli import build_parser, run
from flagd_phone.model import GeminiVision, ModelOutputError
from flagd_phone.protocol import PortalError, UncertainAction
from flagd_phone.reports import write_report
from flagd_phone.runner import Journal, execute_action, run_agent
from flagd_phone.session import SessionStore, validate_assessment, validate_plan
from flagd_phone.vision import Frame, validate_action
from test_agent import FakePortal, decision, png64


def plan():
    return {"subgoals": [{"id": "research", "description": "Read useful search results"}],
            "success_criteria": ["Observed recommendations with sources"],
            "search_queries": ["Thailand places to visit"]}


def finding(**extra):
    return {"claim": "A page recommends visiting Chiang Mai.",
            "visible_text": "Visit Chiang Mai for temples and markets.",
            "source_label": "Browser: Thailand trip ideas", "source_url": "",
            "uncertainty": "A recommendation on one page, not independently corroborated.", **extra}


def assessment(frame, kind="wait", **extra):
    return {"action": decision(frame, kind), "summary": "Reading the search results.",
            "findings": [], "visited_sources": [], "progress": [],
            "answer": "", "answer_finding_ids": [], **extra}


def notebook(tmp_path):
    journal = Journal(tmp_path / "run")
    store = SessionStore(journal.directory)
    store.start({"goal": "Find Thailand trip ideas"})
    store.plan(plan())
    frame = Frame.from_portal(png64())
    journal.frame(0, frame)
    store.observation(0, frame)
    return store, frame


def test_notebook_reopens_without_replaying_and_deduplicates_evidence(tmp_path):
    store, frame = notebook(tmp_path)
    value = assessment(frame, findings=[finding()], visited_sources=["Thailand trip ideas"])
    store.assess(value, frame, {})
    store.assess(value, frame, {})
    assert store.context()["finding_count"] == 1
    assert len(store.context()["visited_sources"]) == 1
    directory = store.directory
    store.close()
    reopened = SessionStore(directory, create=False)
    try:
        assert reopened.context()["findings"][0]["id"] == "F0001"
        assert reopened.context()["findings"][0]["frame_id"] == frame.id
        paths = write_report(reopened)
        report = json.loads((directory / "report.json").read_text())
        assert report["findings"][0]["screenshot"] == "000.png"
        assert report["findings"][0]["observed_at"] > 0
        assert "not independently verified" in (directory / "report.md").read_text()
        assert paths["markdown"].endswith("report.md")
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [
    {"source_url": "https://example.com/unseen"},
    {"source_url": "javascript:alert(1)", "visible_text": "javascript:alert(1)"},
    {"source_url": "https://user:password@example.com/", "visible_text": "https://user:password@example.com/"},
    {"visible_text": ""},
])
def test_rejected_finding_does_not_partially_update_memory(tmp_path, change):
    store, frame = notebook(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.assess(assessment(frame, findings=[finding(), finding(**change)]), frame, {})
        assert store.context()["finding_count"] == 0
        assert store.context()["summary"] == ""
    finally:
        store.close()


def test_finding_cannot_use_an_unrecorded_observation(tmp_path):
    store, _ = notebook(tmp_path)
    other = Frame.from_portal(png64())
    try:
        with pytest.raises(ValueError, match="stored observation"):
            store.assess(assessment(other, findings=[finding()]), other, {})
    finally:
        store.close()


def test_answer_citations_and_progress_must_reference_recorded_ids(tmp_path):
    store, frame = notebook(tmp_path)
    try:
        for value in (
            assessment(frame, answer="Chiang Mai"),
            assessment(frame, answer="Chiang Mai", answer_finding_ids=["F9999"]),
            assessment(frame, progress=[{"subgoal_id": "invented", "status": "done", "evidence": "seen"}]),
        ):
            with pytest.raises(ValueError):
                validate_assessment(value, frame, {}, store.context())
        store.assess(assessment(frame, findings=[finding()]), frame, {})
        store.assess(assessment(frame, "done", answer="One page recommends Chiang Mai.",
            answer_finding_ids=["F0001"], progress=[{
                "subgoal_id": "research", "status": "done", "evidence": "Recommendation is visible."}]), frame, {})
        assert store.get("answer_finding_ids") == ["F0001"]
        assert store.context()["progress"]["research"]["frame_id"] == frame.id
    finally:
        store.close()


@pytest.mark.parametrize("status", ["pending", "done", "blocked"])
def test_only_pending_progress_can_have_empty_evidence(tmp_path, status):
    store, frame = notebook(tmp_path)
    value = assessment(frame, progress=[{
        "subgoal_id": "research", "status": status, "evidence": "",
    }])
    try:
        if status == "pending":
            store.assess(value, frame, {})
            assert store.context()["progress"]["research"]["status"] == "pending"
        else:
            with pytest.raises(ValueError, match="supporting evidence"):
                store.assess(value, frame, {})
    finally:
        store.close()


def test_context_is_bounded_but_report_retains_all_findings(tmp_path):
    store, frame = notebook(tmp_path)
    try:
        for i in range(35):
            store.assess(assessment(frame, findings=[finding(claim=f"Recommendation {i}")],
                visited_sources=[f"Source {i}"]), frame, {})
        assert len(store.context()["findings"]) == 20
        assert len(store.context()["visited_sources"]) == 30
        write_report(store)
        assert len(json.loads((store.directory / "report.json").read_text())["findings"]) == 35
    finally:
        store.close()


def test_report_escapes_screen_content_and_only_links_observed_urls(tmp_path):
    store, frame = notebook(tmp_path)
    try:
        store.assess(assessment(frame, findings=[finding(
            claim="<script>alert(1)</script> [misleading](https://wrong.example)",
            visible_text="Read https://example.com/trip", source_url="https://example.com/trip")]), frame, {})
        write_report(store)
        text = (store.directory / "report.md").read_text()
        assert "<script>" not in text
        assert "\\[misleading\\]" in text
        assert "[Open observed source](<https://example.com/trip>)" in text
        assert "[Inspect screenshot](000.png)" in text
    finally:
        store.close()


@pytest.mark.parametrize("query", ["", "   ", "x" * 201, "search\ncomment", "\x00query"])
def test_search_queries_are_bounded_and_cannot_add_other_typing_fields(query):
    frame = Frame.from_portal(png64())
    with pytest.raises(ValueError):
        validate_action(decision(frame, "type_query", query=query), frame, {})


def test_generated_query_and_exact_field_use_separate_typing_paths():
    async def scenario():
        frame = Frame.from_portal(png64())
        portal = FakePortal()
        await execute_action(portal, decision(frame, "type_query", query="cafés in Chiang Mai"), frame, {})
        await execute_action(portal, decision(frame, "type_text", text_key="title"), frame, {"title": "EXACT = title"})
        typed = [base64.b64decode(params["base64_text"]).decode() for _, params, _ in portal.calls]
        assert typed == ["cafés in Chiang Mai", "EXACT = title"]
        assert all(params["clear"] for _, params, _ in portal.calls)
        with pytest.raises(ValueError):
            validate_action(decision(frame, "type_query", query="search", text_key="title"), frame, {"title": "exact"})
    asyncio.run(scenario())


class ResearchModel:
    def __init__(self):
        self.steps = 0

    async def plan(self, task):
        return plan(), {"usage": {"totalTokenCount": 50}}

    async def assess(self, task, frame, history, memory):
        self.steps += 1
        if self.steps == 1:
            assert memory["plan"]["search_queries"] == ["Thailand places to visit"]
            value = assessment(frame, action=decision(frame, "type_query", query="Thailand places to visit"))
        elif self.steps == 2:
            assert history[-1]["action"]["query"] == "Thailand places to visit"
            value = assessment(frame, findings=[finding()], visited_sources=["Thailand trip ideas"])
        else:
            assert memory["findings"][0]["id"] == "F0001"
            value = assessment(frame, "done", answer="A page recommends Chiang Mai; the recommendation is unchecked.",
                answer_finding_ids=["F0001"], progress=[{
                    "subgoal_id": "research", "status": "done", "evidence": "A useful recommendation was read."}])
        return value, {"usage": {"totalTokenCount": 100}}


def test_planned_research_loop_returns_saved_cited_answer(tmp_path):
    async def scenario():
        portal = FakePortal()
        directory = tmp_path / "run"
        result = await run_agent(portal, ResearchModel(), {"goal": "Find one Thailand trip idea", "max_steps": 4},
                                 Journal(directory), execute=True)
        assert [c[0] for c in portal.calls] == ["screenshot", "keyboard/input", "screenshot", "screenshot"]
        assert result["model_calls"] == 4  # one plan plus three screen assessments
        assert result["reported_total_tokens"] == 350
        assert result["finding_count"] == 1
        assert result["verified_success"] is False
        report = json.loads((directory / "report.json").read_text())
        assert report["answer_finding_ids"] == ["F0001"]
        assert report["findings"][0]["screenshot"] == "001.png"
    asyncio.run(scenario())


def test_planned_preview_is_two_requests_without_input(tmp_path):
    async def scenario():
        portal = FakePortal()
        result = await run_agent(portal, ResearchModel(), {"goal": "Find trip ideas", "max_steps": 4}, Journal(tmp_path / "run"))
        assert result["model_calls"] == 2
        assert result["status"] == "preview"
        assert [c[0] for c in portal.calls] == ["screenshot"]
        assert (tmp_path / "run" / "report.md").is_file()
    asyncio.run(scenario())


@pytest.mark.parametrize("uncertain", [False, True])
def test_report_survives_failed_input_or_observation_without_replay(tmp_path, uncertain):
    async def scenario():
        class Model(ResearchModel):
            async def assess(self, task, frame, history, memory):
                return assessment(frame, action=decision(frame, "type_query", query="Thailand"),
                                  findings=[finding()]), {"usage": {"totalTokenCount": 100}}

        class Portal(FakePortal):
            async def call(self, method, params=None, *, mutation=False):
                if mutation and uncertain:
                    self.calls.append((method, params, mutation))
                    raise UncertainAction("unknown outcome")
                return await super().call(method, params, mutation=mutation)

        portal = Portal(fail_read_after_input=True)
        directory = tmp_path / "run"
        if uncertain:
            result = await run_agent(portal, Model(), {"goal": "Research", "max_steps": 4}, Journal(directory), execute=True)
            assert result["status"] == "uncertain_input"
        else:
            with pytest.raises(PortalError):
                await run_agent(portal, Model(), {"goal": "Research", "max_steps": 4}, Journal(directory), execute=True)
        report = json.loads((directory / "report.json").read_text())
        assert len(report["findings"]) == 1
        assert report["result"]["acknowledged_inputs"] == (0 if uncertain else 1)
        assert len([c for c in portal.calls if c[2]]) == 1
    asyncio.run(scenario())


def test_invalid_paid_model_output_still_records_usage(tmp_path):
    async def scenario():
        class Model(ResearchModel):
            async def plan(self, task):
                raise ModelOutputError({"usage": {"totalTokenCount": 123}})
        directory = tmp_path / "run"
        with pytest.raises(ModelOutputError):
            await run_agent(FakePortal(), Model(), {"goal": "Research", "max_steps": 4}, Journal(directory))
        report = json.loads((directory / "report.json").read_text())
        assert report["result"]["reported_total_tokens"] == 123
        assert report["status"] == "failed"
    asyncio.run(scenario())


def test_rejected_assessment_recaptures_before_one_correction_and_saves_usage(tmp_path):
    async def scenario():
        class Model(ResearchModel):
            def __init__(self):
                self.frames = []

            async def assess(self, task, frame, history, memory):
                self.frames.append(frame.id)
                if len(self.frames) == 1:
                    raise ModelOutputError({"usage": {"totalTokenCount": 123}},
                                           reason="an answer requires recorded finding citations")
                assert len(self.frames) == 2
                assert self.frames[0] != frame.id
                assert history == []
                assert memory["finding_count"] == 0
                assert memory["decision_rejection"]["input_executed"] is False
                return assessment(frame, "done"), {"usage": {"totalTokenCount": 100}}

        portal = FakePortal()
        result = await run_agent(portal, Model(), {"goal": "Research", "max_steps": 4},
                                 Journal(tmp_path / "repair"), execute=True)
        assert [c[0] for c in portal.calls] == ["screenshot", "screenshot"]
        assert result["model_calls"] == 3  # plan, rejected assessment, correction
        assert result["reported_total_tokens"] == 273  # plan reports 50
        assert result["acknowledged_inputs"] == 0

    asyncio.run(scenario())


def test_two_invalid_assessments_stop_without_inputs_and_keep_both_usages(tmp_path):
    async def scenario():
        class Model(ResearchModel):
            async def assess(self, task, frame, history, memory):
                raise ModelOutputError({"usage": {"totalTokenCount": 123}})

        portal = FakePortal()
        directory = tmp_path / "two-errors"
        with pytest.raises(ModelOutputError):
            await run_agent(portal, Model(), {"goal": "Research", "max_steps": 10},
                            Journal(directory), execute=True)
        result = json.loads((directory / "report.json").read_text())["result"]
        assert result["model_calls"] == 3
        assert result["reported_total_tokens"] == 296
        assert [c[0] for c in portal.calls] == ["screenshot", "screenshot"]

    asyncio.run(scenario())


def test_gemini_plan_and_assessment_use_bounded_memory_and_current_pixels():
    async def scenario():
        frame = Frame.from_portal(png64())
        requests = []
        memory = {"plan": plan(), "findings": [], "summary": "At search box"}
        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            parts = body["contents"][0]["parts"]
            context = json.loads(parts[0]["text"])
            if len(requests) == 1:
                assert len(parts) == 1  # planning does not need an image
                assert context["exact_text_field_names"] == ["title"]
                assert "text_fields" not in context
                value = plan()
            else:
                assert context["memory"] == memory
                assert context["frame_id"] == frame.id
                assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
                value = assessment(frame, action=decision(frame, "type_query", query="Thailand"))
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(value)}]}}], "usageMetadata": {"totalTokenCount": 100}})
        model = GeminiVision("test", "test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            task = {"goal": "Research", "text_fields": {"title": "Exact title"}, "max_steps": 4}
            await model.plan(task)
            value, metadata = await model.assess(task, frame, [], memory)
            assert value["action"]["query"] == "Thailand"
            assert metadata["usage"]["totalTokenCount"] == 100
        finally:
            await model.close()
    asyncio.run(scenario())


def test_offline_report_command_needs_no_phone_or_model_credentials(tmp_path, monkeypatch, capsys):
    store, _ = notebook(tmp_path)
    store.close()
    monkeypatch.delenv("PHONE_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("PHONE_GEMINI_API_KEY", raising=False)
    args = build_parser().parse_args(["report", "--run-dir", str(tmp_path / "run")])
    asyncio.run(run(args))
    assert json.loads(capsys.readouterr().out)["markdown"].endswith("report.md")


def test_plan_rejects_duplicate_subgoals():
    value = copy.deepcopy(plan())
    value["subgoals"] *= 2
    with pytest.raises(ValueError, match="unique"):
        validate_plan(value)

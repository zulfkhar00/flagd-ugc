from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .protocol import PortalConnection, PortalError, UncertainAction
from .model import ModelOutputError, VisionRequestError, VisionTransportError
from .semantic import SemanticScreen
from .continuation import resolve_followup, validate_followup
from .reports import write_report
from .session import SessionStore
from .vision import Frame, validate_action, validate_identity_spec
from .authorization import AuthorizationState, validate_authorization


class Journal:
    """Persist intent before input so a crash cannot silently erase an action."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.directory = directory
        self.path = directory / "trace.jsonl"

    def record(self, event: str, **data: Any) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time.time(), "event": event, **data}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def frame(self, step: int, frame: Frame) -> None:
        (self.directory / f"{step:03d}.png").write_bytes(frame.png)
        (self.directory / f"{step:03d}-model.jpg").write_bytes(frame.jpeg)
        self.record("observation", step=step, frame_id=frame.id,
                    device_size=[frame.width, frame.height],
                    model_size=[frame.view_width, frame.view_height],
                    portal_screenshot_ms=round(frame.portal_screenshot_ms, 1),
                    host_prepare_ms=round(frame.host_prepare_ms, 1))


def load_task(path: Path) -> dict[str, Any]:
    return validate_task(json.loads(path.read_text(encoding="utf-8")))


def validate_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict) or set(task) - {
        "goal", "text_fields", "text_limits", "max_steps", "allow_robot_verification",
        "mode", "duration_seconds", "authorization", "publication",
        "resume_from_job_id", "deletion", "replacement",
    }:
        raise ValueError("invalid task fields")
    task = dict(task)
    mode = task.get("mode", "standard")
    if mode not in ("standard", "browse", "publish"):
        raise ValueError("invalid task mode")
    if "duration_seconds" in task and (type(task["duration_seconds"]) is not int
                                     or not 1 <= task["duration_seconds"] <= 3600):
        raise ValueError("duration_seconds must be 1–3600")
    if "duration_seconds" in task and mode != "browse":
        raise ValueError("duration_seconds is currently supported for browse mode")
    if "authorization" in task:
        validate_authorization(task["authorization"])
    if mode == "browse" and ("duration_seconds" not in task or "authorization" not in task):
        raise ValueError("browsing needs a duration and an account")
    if "publication" in task:
        value = task["publication"]
        if (not isinstance(value, dict) or not {"source_id", "slide_count"} <= set(value)
                or set(value) - {"source_id", "slide_count", "required_text_fields", "media_format", "require_music", "content_identity"}
                or not isinstance(value["source_id"], str) or not 1 <= len(value["source_id"]) <= 100
                or type(value["slide_count"]) is not int or not 6 <= value["slide_count"] <= 8
                or mode != "publish" or "authorization" not in task):
            raise ValueError("invalid publication source or slide count")
        required = value.get("required_text_fields", [])
        fields = task.get("text_fields", {})
        if (not isinstance(required, list) or len(required) > 20 or not isinstance(fields, dict)
                or any(not isinstance(key, str) or key not in fields for key in required)
                or len(required) != len(set(required))):
            raise ValueError("required posting fields must identify unique supplied text fields")
        if ("media_format" in value and (not isinstance(value["media_format"], str)
                or not value["media_format"].strip() or len(value["media_format"]) > 80)):
            raise ValueError("publication media_format must be 1–80 characters")
        if "require_music" in value and type(value["require_music"]) is not bool:
            raise ValueError("publication require_music must be a boolean")
        if "content_identity" in value:
            validate_identity_spec(value["content_identity"])
            if task["authorization"].get("verification", {}).get("account", True) is not True:
                raise ValueError("content identity requires account verification")
    if mode == "publish" and "publication" not in task:
        raise ValueError("publishing needs its source and slide count")
    if "deletion" in task:
        value = task["deletion"]
        if (not isinstance(value, dict) or set(value) != {"target_id", "identity"}
                or not isinstance(value["target_id"], str) or not 1 <= len(value["target_id"]) <= 100
                or task.get("authorization", {}).get("limits", {}).get("delete") != 1
                or task["authorization"].get("verification", {}).get("account", True) is not True):
            raise ValueError("deletion needs one authorized target and account verification")
        validate_identity_spec(value["identity"])
    elif task.get("authorization", {}).get("limits", {}).get("delete", 0):
        raise ValueError("deletion allowance needs an exact target")
    if "replacement" in task:
        import re
        value = task["replacement"]
        if (mode != "publish" or not isinstance(value, dict)
                or set(value) != {"previous_job_id", "deletion_job_id"}
                or any(not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                       for job_id in value.values())
                or not task["publication"].get("content_identity")):
            raise ValueError("replacement needs the original and completed deletion job IDs and content identity")
    if "resume_from_job_id" in task:
        import re
        if (mode != "publish" or not isinstance(task["resume_from_job_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", task["resume_from_job_id"])):
            raise ValueError("resume_from_job_id must identify a previous publication job")
    if type(task.get("allow_robot_verification", False)) is not bool:
        raise ValueError("allow_robot_verification must be a boolean")
    if (not isinstance(task.get("goal"), str) or not task["goal"].strip()
            or len(task["goal"]) > 8000):
        raise ValueError("task needs a goal of 1–8000 characters")
    fields = task.setdefault("text_fields", {})
    if (not isinstance(fields, dict) or len(fields) > 20
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or len(k) > 80 or len(v) > 8000 for k, v in fields.items())):
        raise ValueError("invalid exact text fields")
    limits = task.setdefault("text_limits", {})
    if (not isinstance(limits, dict) or len(limits) > 20
            or any(not isinstance(k, str) or k not in fields or type(v) is not int
                   or not 1 <= v <= 8000 for k, v in limits.items())):
        raise ValueError("text_limits must map supplied field names to limits of 1–8000 characters")
    for key, limit in limits.items():
        if len(fields[key]) > limit:
            raise ValueError(f"text field {key!r} must be at most {limit} characters")
    steps = task.setdefault("max_steps", 20)
    maximum = 500 if mode == "browse" else 100
    if type(steps) is not int or not 1 <= steps <= maximum:
        raise ValueError(f"max_steps must be between 1 and {maximum}")
    return task


async def screenshot(portal: PortalConnection, *, hide_overlay: bool | None = None) -> Frame:
    if hide_overlay is None:
        hide_overlay = os.environ.get("PHONE_SCREENSHOT_HIDE_OVERLAY", "1").lower() not in {
            "0", "false", "no", "off",
        }
    started = time.perf_counter()
    result = await portal.call("screenshot", {"hideOverlay": hide_overlay})
    received = time.perf_counter()
    frame = Frame.from_portal(result)
    prepared = time.perf_counter()
    return replace(frame, portal_screenshot_ms=(received - started) * 1000,
                   host_prepare_ms=(prepared - received) * 1000)


async def execute_action(
    portal: PortalConnection, action: dict[str, Any], frame: Frame, text_fields: dict[str, str]
) -> None:
    validate_action(action, frame, text_fields)
    if action["requires_review"]:
        raise ValueError("action requires operator review")
    if time.monotonic() - frame.captured_at > 90:
        raise ValueError("observation expired; take a fresh screenshot")
    kind = action["kind"]
    if kind == "tap":
        x, y = frame.to_device(action["x"], action["y"])
        await portal.call("tap", {"x": x, "y": y}, mutation=True)
        await asyncio.sleep(0.5)
    elif kind == "swipe":
        x, y = frame.to_device(action["x"], action["y"])
        end_x, end_y = frame.to_device(action["end_x"], action["end_y"])
        duration = action.get("duration_ms", 500)
        if "playback_ms" in action:
            await asyncio.sleep(max(0, action["playback_ms"] / 1000
                                    - (time.monotonic() - frame.captured_at)))
        for index in range(action.get("repeat", 1)):
            # The agent selects a double edge-back gesture from visible exit
            # UI. Keep the pair prompt, without an intervening model request.
            # An uncertain first input aborts here; the second is never sent.
            await portal.call("swipe", {"startX": x, "startY": y, "endX": end_x,
                                       "endY": end_y, "duration": duration}, mutation=True)
            await asyncio.sleep(0.1 if index == 0 and action.get("repeat") == 2
                                else duration / 1000 + 0.4)
    elif kind in ("type_text", "type_query", "type_comment"):
        text = (text_fields[action["text_key"]] if kind == "type_text"
                else action["query"] if kind == "type_query" else action["comment"])
        await portal.call("keyboard/input", {
            "base64_text": base64.b64encode(text.encode("utf-8")).decode(), "clear": True,
        }, mutation=True)
        await asyncio.sleep(0.5)
    elif kind == "submit_search":
        # Ordinary keyboard input, selected from the observed active search
        # editor. No app API, deep link or arbitrary Android key is exposed.
        await portal.call("keyboard/key", {"key_code": 66}, mutation=True)
        await asyncio.sleep(1.5)
    elif kind in ("wait", "watch"):
        duration = action.get("duration_ms", 500) / 1000
        if kind == "watch":
            duration = max(0, duration - (time.monotonic() - frame.captured_at))
        await asyncio.sleep(duration)
    elif kind in ("confirm_account", "confirm_preview", "confirm_media", "confirm_target", "confirm_content"):
        pass  # Journalled screen evidence, never an input to the phone.
    else:
        raise ValueError("terminal decisions are not inputs")


def unchanged_navigation(action: dict[str, Any], frame: Frame,
                         previous: tuple[Frame, dict[str, Any]]) -> bool:
    """Match ineffective navigation despite small coordinate/status changes.

    This is recovery feedback, not a UI classifier or permission to send input.
    A moving camera preview outside a tapped control should not hide a loop.
    """
    before, prior = previous
    if (action["kind"] not in ("tap", "swipe", "type_text", "type_query", "submit_search")
            or action["kind"] != prior["kind"] or action.get("effect", "none") != "none"
            or prior.get("effect", "none") != "none"):
        return False
    if any(action.get(key, 1 if key == "repeat" else None) != prior.get(key, 1 if key == "repeat" else None)
           for key in ("text_key", "query", "repeat")):
        return False
    for key, size in (("x", frame.view_width), ("end_x", frame.view_width),
                      ("y", frame.view_height), ("end_y", frame.view_height)):
        if key in action and (key not in prior or abs(action[key] - prior[key]) > size * 0.025):
            return False
    target = frame.to_device(action["x"], action["y"]) if action["kind"] == "tap" else None
    return not frame.materially_differs(before, at=target)


async def run_agent(
    portal: PortalConnection, model: Any, task: dict[str, Any], journal: Journal,
    *, execute: bool = False, min_confidence: float = 0.75,
) -> dict[str, Any]:
    task = validate_task(task)
    store = SessionStore(journal.directory)
    store.start(task)
    compact = task.get("mode") in ("browse", "publish")
    planned = (not compact and callable(getattr(model, "plan", None))
               and callable(getattr(model, "assess", None)))
    guarded_enabled = (task.get("mode") != "publish"
                       and os.environ.get("PHONE_GUARDED_CONTINUATIONS", "0").lower()
                       in {"1", "true", "yes", "on"})
    pending_followup: tuple[dict[str, str], str] | None = None
    scope = AuthorizationState(task)
    started = time.monotonic()
    budget = task.get("duration_seconds")
    deadline = started + budget if budget and task.get("mode") != "browse" else None
    navigation_deadline = started + 180 if task.get("mode") == "browse" else None
    watch_seconds = 0.0
    watched_current_video = False
    history: list[dict[str, Any]] = []
    calls = 0
    mutations = 0
    status = "failed"
    total_tokens = 0
    token_counts = {"promptTokenCount": 0, "candidatesTokenCount": 0,
                    "thoughtsTokenCount": 0, "cachedContentTokenCount": 0}
    model_usage: dict[str, dict[str, int]] = {}
    navigation_history: list[tuple[Frame, dict[str, Any]]] = []
    rejection_feedback: str | None = None
    authorization_rejections = 0
    review_rejections = 0
    confidence_rejections = 0
    publication_account = None
    def usage(metadata: dict[str, Any]) -> None:
        nonlocal total_tokens, calls
        calls += max(0, metadata.get("api_calls", 1) - 1)
        counts = metadata.get("usage", {})
        value = counts.get("totalTokenCount", 0) if isinstance(counts, dict) else 0
        if type(value) is int and value >= 0:
            total_tokens += value
        for key in token_counts:
            value = counts.get(key, 0)
            if type(value) is int and value >= 0:
                token_counts[key] += value
        for request in metadata.get("requests", [metadata]):
            model_name = request.get("model", "unknown")
            bucket = model_usage.setdefault(model_name, {"calls": 0, **dict.fromkeys(token_counts, 0)})
            bucket["calls"] += 1
            for key in token_counts:
                value = request.get("usage", {}).get(key, 0)
                if type(value) is int and value >= 0:
                    bucket[key] += value

    journal.record("run_started", goal=task["goal"], execute=execute, max_steps=task["max_steps"])
    try:
        if planned:
            calls += 1
            journal.record("model_requested", phase="plan")
            plan, metadata = await model.plan(task)
            usage(metadata)
            store.plan(plan)
            journal.record("plan", plan=plan, **metadata)
        frame = await screenshot(portal)
        journal.frame(0, frame)
        store.observation(0, frame)
        for step in range(task["max_steps"]):
            if frame.is_black:
                # Android acknowledged a screenshot but returned no visible
                # pixels. A short transition may recover, so permit two fresh
                # reads; never send a guessed tap/swipe on an invisible UI.
                for attempt in range(2):
                    journal.record("blank_capture", step=step, frame_id=frame.id, retry=attempt)
                    await asyncio.sleep(0.5)
                    frame = await screenshot(portal)
                    journal.frame(step, frame)
                    store.observation(step, frame)
                    if not frame.is_black:
                        break
                if frame.is_black:
                    status = "blank_capture"
                    journal.record("stopped", reason="repeated black screenshots; no blind UI input")
                    break
            end = deadline or navigation_deadline
            remaining = end - time.monotonic() if end else None
            if remaining is not None and remaining <= 0:
                status = "duration_limit" if deadline else "account_verification_timeout"
                break
            runtime_task = {**task, "execution_context": {
                **scope.context(), "seconds_remaining": remaining,
                "timer_started": deadline is not None, "publication": task.get("publication"),
                "playback_seconds": round(watch_seconds), "api_calls_so_far": calls,
                "guarded_continuations": guarded_enabled,
            }}
            if rejection_feedback is not None:
                runtime_task["execution_context"]["decision_rejection"] = rejection_feedback
            preplanned_action = None
            if pending_followup is not None and rejection_feedback is None:
                spec, expected_app = pending_followup
                pending_followup = None
                try:
                    current_screen = SemanticScreen.from_portal(
                        await asyncio.wait_for(portal.call("state", {"filter": True}),
                                               timeout=min(5, remaining) if remaining else 5), frame)
                    preplanned_action = resolve_followup(
                        spec, current_screen, frame, expected_app=expected_app,
                        text_fields=task.get("text_fields", {}))
                except (PortalError, ValueError, TypeError, TimeoutError):
                    pass
                journal.record("guarded_followup_check", step=step,
                               matched=preplanned_action is not None,
                               operation=spec["operation"])
            semantic_screen = None
            if (preplanned_action is None and not planned
                    and os.environ.get("PHONE_SEMANTIC_NAV", "0").lower()
                    in {"1", "true", "yes", "on"}
                    and callable(getattr(model, "decide_hybrid", None))):
                started = time.perf_counter()
                try:
                    semantic_screen = SemanticScreen.from_portal(
                        await asyncio.wait_for(portal.call("state", {"filter": True}),
                                               timeout=min(5, remaining) if remaining else 5), frame)
                    journal.record("semantic_observation", step=step,
                                   element_count=len(semantic_screen.elements),
                                   source_bytes=semantic_screen.source_bytes,
                                   prompt_chars=len(semantic_screen.compact()),
                                   useful=semantic_screen.useful,
                                   fingerprint=semantic_screen.fingerprint,
                                   portal_state_ms=round((time.perf_counter() - started) * 1000, 1))
                except (PortalError, ValueError, TypeError, TimeoutError) as exc:
                    journal.record("semantic_unavailable", step=step,
                                   error_type=type(exc).__name__,
                                   portal_state_ms=round((time.perf_counter() - started) * 1000, 1))
            if preplanned_action is not None:
                action, metadata = preplanned_action, {"decision_source": "guarded_followup"}
            else:
                calls += 1
                journal.record("model_requested", step=step,
                               perception="semantic_first" if semantic_screen and semantic_screen.useful else "vision")
            if preplanned_action is not None:
                rejection_feedback = None
            elif planned:
                context = store.context()
                if rejection_feedback is not None:
                    context["decision_rejection"] = {
                        "reason": rejection_feedback,
                        "input_executed": False,
                        "instruction": "Correct the rejected response using this fresh screen. Rejected findings were not stored.",
                    }
                try:
                    assessment, metadata = await model.assess(runtime_task, frame, history, context)
                except ModelOutputError as exc:
                    # Invalid model output has dispatched no input and changed
                    # no notebook data. Permit one correction, counted against
                    # the ordinary decision limit, after a fresh observation.
                    # Transport errors and uncertain inputs are never retried.
                    if rejection_feedback is not None or step + 1 >= task["max_steps"]:
                        raise
                    usage(exc.metadata)
                    journal.record("decision_rejected", step=step, **exc.metadata)
                    rejection_feedback = exc.metadata.get("validation_error", "unusable decision")
                    frame = await screenshot(portal)
                    journal.frame(step + 1, frame)
                    store.observation(step + 1, frame)
                    continue
                rejection_feedback = None
                usage(metadata)
                # Save findings before dispatching any input, including terminal decisions.
                store.assess(assessment, frame, task.get("text_fields", {}))
                action = assessment["action"]
                journal.record("assessment", step=step, assessment=assessment, **metadata)
            else:
                try:
                    decision_remaining = end - time.monotonic() if end else None
                    async with asyncio.timeout(decision_remaining):
                        if semantic_screen and semantic_screen.useful:
                            action, metadata = await model.decide_hybrid(
                                runtime_task, frame, history, semantic_screen)
                        else:
                            action, metadata = await model.decide(runtime_task, frame, history)
                except TimeoutError:
                    status = "duration_limit" if deadline else "account_verification_timeout"
                    break
                except ModelOutputError as exc:
                    if not compact or rejection_feedback is not None or step + 1 >= task["max_steps"]:
                        raise
                    usage(exc.metadata)
                    journal.record("decision_rejected", step=step, **exc.metadata)
                    rejection_feedback = exc.metadata.get("validation_error", "unusable decision")
                    frame = await screenshot(portal)
                    journal.frame(step + 1, frame)
                    store.observation(step + 1, frame)
                    continue
                rejection_feedback = None
                usage(metadata)
            validate_action(action, frame, task.get("text_fields", {}))
            journal.record("decision", step=step, action=action, **metadata)
            if execute and (action["kind"] == "handoff" or action["requires_review"]):
                # A model's review flag is a proposed stop, not an approval
                # requirement for work the operator already authorized. Ask
                # the agent to recover; never silently execute a flagged tap.
                review_rejections += 1
                if review_rejections > 2 or step + 1 >= task["max_steps"]:
                    status = "blocked"
                    journal.record("stopped", reason=action["reason"], blocker="unresolved_agent_handoff")
                    break
                rejection_feedback = (
                    "You proposed a handoff or review stop. This task is autonomous within its existing "
                    "authorization. Reconsider whether ordinary visible navigation, "
                    "editing, dismissing an informational notice or another route can complete "
                    "the task within its existing authorization. An unfinished caption or "
                    "music setting is work you can do, not automatically an operator blocker. "
                    "Move a blocking overlay or choose another route; do not repeatedly tap through it. "
                    "No input was sent. Do not relax requirements or handle an unauthorized "
                    "challenge. Choose your next authorized action with requires_review=false. "
                    "If essential credentials, an unauthorized commitment or another real blocker "
                    "makes progress impossible, state exactly what is missing in the handoff."
                )
                journal.record("handoff_reconsideration", step=step, reason=action["reason"])
                history.append({"handoff_reconsidered": True, "reason": action["reason"],
                                "input_acknowledged": False})
                frame = await screenshot(portal)
                journal.frame(step + 1, frame)
                store.observation(step + 1, frame)
                continue
            if action["requires_review"] or action["kind"] in ("done", "handoff"):
                # Model completion is a candidate outcome, never a verified success.
                status = ("completion_candidate" if action["kind"] == "done"
                          and not action["requires_review"] else "needs_review")
                if task.get("mode") == "publish" and scope.used["publish"] == 1:
                    publication_account = action.get("published_account") or None
                    if (publication_account and publication_account.casefold()
                            != task["authorization"]["account"].casefold()):
                        status = "misdirected_publication"
                        journal.record("publication_destination_mismatch", account=publication_account,
                                       requested_account=task["authorization"]["account"], evidence=action["reason"])
                break
            if action["confidence"] < min_confidence:
                confidence_rejections += 1
                if not execute or confidence_rejections > 2 or step + 1 >= task["max_steps"]:
                    status = "blocked" if execute else "low_confidence"
                    break
                rejection_feedback = (
                    "The proposed input was not sent because the target was uncertain. "
                    "Reconsider this fresh screenshot, wait for visible loading or choose a clearer "
                    "authorized route. Do not guess targets or request review for ordinary navigation."
                )
                journal.record("confidence_reconsideration", step=step, reason=action["reason"])
                frame = await screenshot(portal)
                journal.frame(step + 1, frame)
                store.observation(step + 1, frame)
                continue
            if not execute:
                status = "preview"
                break
            try:
                scope.check(action)
            except ValueError as error:
                # Missing a required checkpoint sent no input. Give the model
                # a bounded chance to complete that checkpoint on a fresh
                # screen, without weakening scope or replaying a public tap.
                authorization_rejections += 1
                rejection_feedback = str(error)  # AuthorizationState uses fixed, non-secret messages.
                journal.record("authorization_rejected", step=step, action=action,
                               reason=rejection_feedback, input_executed=False)
                if authorization_rejections > 2 or step + 1 >= task["max_steps"]:
                    status = "blocked"
                    break
                frame = await screenshot(portal)
                journal.frame(step + 1, frame)
                store.observation(step + 1, frame)
                history.append({"action_rejected": True, "reason": rejection_feedback,
                                "input_acknowledged": False})
                continue
            account_verifier = getattr(model, "verify_account", None)
            if action["kind"] == "confirm_account" and task.get("deletion") and not callable(account_verifier):
                raise ValueError("active account verifier is required for deletion")
            if action["kind"] == "confirm_account" and (task.get("mode") == "publish" or task.get("deletion")) and callable(account_verifier):
                calls += 1
                journal.record("model_requested", step=step, phase="active_account_verification")
                assessment, verification_metadata = await account_verifier(task, frame)
                usage(verification_metadata)
                journal.record("active_account_assessment", step=step, assessment=assessment,
                               **verification_metadata)
                if assessment.get("valid") is not True:
                    authorization_rejections += 1
                    rejection_feedback = (
                        "The active-account checkpoint was rejected; no input was sent. "
                        "A feed post's author, commenter, watermark or shared logo does not identify "
                        "the logged-in account. Use the active profile header or selected account "
                        "in the account switcher before confirm_account. Observed evidence: "
                        + assessment.get("evidence", "")[:500]
                    )
                    journal.record("account_verification_rejected", step=step, reason=rejection_feedback)
                    if authorization_rejections > 2 or step + 1 >= task["max_steps"]:
                        status = "blocked"
                        break
                    history.append({"action_rejected": True, "reason": rejection_feedback,
                                    "input_acknowledged": False})
                    frame = await screenshot(portal)
                    journal.frame(step + 1, frame)
                    store.observation(step + 1, frame)
                    continue
            if action["kind"] in ("confirm_target", "confirm_content"):
                verifier = getattr(model, "verify_visual_identity", None)
                if not callable(verifier):
                    raise ValueError("visual identity verifier is required for target/content checkpoints")
                calls += 1
                journal.record("model_requested", step=step, phase="visual_identity_verification")
                assessment, metadata = await verifier(task, frame, action["kind"])
                usage(metadata)
                journal.record("visual_identity_assessment", step=step, kind=action["kind"],
                               assessment=assessment, **metadata)
                if assessment.get("valid") is not True:
                    authorization_rejections += 1
                    rejection_feedback = (
                        "The exact visual identity was rejected; no input was sent. "
                        "Inspect the actual target/selected media, not a draft tile, source webpage or remembered values. "
                        "The required caption and media text must be readable. Observed evidence: "
                        + assessment.get("evidence", "")[:500]
                    )
                    if authorization_rejections > 2 or step + 1 >= task["max_steps"]:
                        status = "blocked"
                        break
                    history.append({"action_rejected": True, "reason": rejection_feedback,
                                    "input_acknowledged": False})
                    frame = await screenshot(portal)
                    journal.frame(step + 1, frame)
                    store.observation(step + 1, frame)
                    continue
            if action["kind"] == "watch" and task.get("mode") != "browse":
                raise ValueError("watch is only available in timed browsing")
            if "playback_ms" in action and task.get("mode") != "browse":
                raise ValueError("swipe playback is only available in timed browsing")
            if task.get("mode") == "browse" and action["kind"] == "watch" and watched_current_video:
                rejection_feedback = (
                    "This clip was already watched. No playback was started. Move to a visibly "
                    "different video with a swipe or search result before requesting another watch."
                )
                journal.record("repeated_watch_rejected", step=step, action=action,
                               reason=rejection_feedback, input_executed=False)
                history.append({"action_rejected": True, "action": action,
                                "reason": rejection_feedback, "input_acknowledged": False})
                frame = await screenshot(portal)
                journal.frame(step + 1, frame)
                store.observation(step + 1, frame)
                continue
            if (task.get("mode") == "publish" or action.get("effect") == "delete") and action["kind"] in (
                "tap", "type_text", "type_query", "type_comment", "submit_search"
            ):
                # TikTok can replace Next with Post at the same coordinates
                # while inference is in flight. Re-observe before dispatch.
                # Ordinary navigation swipes must not be rejected just because
                # video/carousel content continues playing on the screen.
                current = await screenshot(portal)
                target = frame.to_device(action["x"], action["y"]) if action["kind"] == "tap" else None
                if frame.materially_differs(current, at=target):
                    journal.record("stale_decision_discarded", step=step, action=action,
                                   frame_id=frame.id, new_frame_id=current.id)
                    scope.invalidate_preview()
                    scope.invalidate_media()
                    frame = current
                    journal.frame(step + 1, frame)
                    store.observation(step + 1, frame)
                    rejection_feedback = (
                        "The screen changed around the proposed control during inference, so that input "
                        "was NOT sent. Reconsider the controls on this fresh screen. If the same route "
                        "keeps being discarded, choose another visible route instead of assuming taps "
                        "reached the phone. Do not relax account, source or publishing requirements."
                    )
                    history.append({"action_discarded": True, "reason": rejection_feedback})
                    continue
            if sum(unchanged_navigation(action, frame, prior) for prior in navigation_history) >= 2:
                rejection_feedback = (
                    "Repeated inputs left the screen unchanged. This proposed repeat was NOT sent. "
                    "Choose a different visible route, move a blocking overlay, or reconsider where the "
                    "required content comes from. Do not keep tapping through an overlay or repeat the "
                    "same ineffective control with slightly different coordinates. Use task-supplied "
                    "source links if content is absent locally. For a visible double-back exit prompt, "
                    "choose an edge-back swipe with repeat=2 so inference latency does not expire it. "
                    "Do not guess targets or relax requirements."
                )
                journal.record("navigation_recovery", step=step, action=action,
                               reason=rejection_feedback, input_executed=False)
                history.append({"action_rejected": True, "action": action, "reason": rejection_feedback,
                                "input_acknowledged": False})
                frame = await screenshot(portal)
                journal.frame(step + 1, frame)
                store.observation(step + 1, frame)
                continue
            guarded_anchor = None
            guarded_spec = (validate_followup(metadata.get("guarded_followup"), action,
                                              task.get("text_fields", {}))
                            if guarded_enabled and isinstance(metadata, dict) else None)
            if guarded_spec is not None:
                try:
                    anchor_screen = SemanticScreen.from_portal(
                        await asyncio.wait_for(portal.call("state", {"filter": True}),
                                               timeout=min(5, remaining) if remaining else 5), frame)
                    guarded_anchor = anchor_screen.app or None
                except (PortalError, ValueError, TypeError, TimeoutError):
                    pass
                journal.record("guarded_followup_anchored", step=step,
                               available=guarded_anchor is not None,
                               operation=guarded_spec["operation"])
            journal.record("input_intent", step=step, action=action, frame_id=frame.id)
            scope.record_before_input(action)
            if action["kind"] == "confirm_account" and budget and deadline is None:
                deadline = time.monotonic() + budget
                navigation_deadline = None
                journal.record("browsing_timer_started", duration_seconds=budget)
            before = time.monotonic()
            playback_started = frame.captured_at if action["kind"] == "watch" or "playback_ms" in action else None
            remaining = deadline - before if deadline else (navigation_deadline - before if navigation_deadline else None)
            try:
                async with asyncio.timeout(remaining):
                    await execute_action(portal, action, frame, task.get("text_fields", {}))
            except TimeoutError:
                if playback_started is not None:
                    watch_seconds += time.monotonic() - playback_started
                status = "uncertain_input" if getattr(portal, "uncertain", False) else "duration_limit"
                break
            if playback_started is not None:
                watch_seconds += max(0, time.monotonic() - playback_started
                                     - (action.get("duration_ms", 500) / 1000 + 0.4
                                        if action["kind"] == "swipe" else 0))
            if action["kind"] not in ("wait", "watch", "confirm_account", "confirm_preview", "confirm_media", "confirm_target", "confirm_content"):
                mutations += 1
            journal.record("input_acknowledged", step=step)
            if task.get("mode") == "browse":
                if action["kind"] == "watch":
                    watched_current_video = True
                elif action["kind"] in ("swipe", "submit_search", "tap"):
                    watched_current_video = False
            scope.record_after_input(action)
            review_rejections = confidence_rejections = authorization_rejections = 0
            navigation_history.append((frame, action))
            navigation_history = navigation_history[-8:]
            history.append({"action": action, "input_acknowledged": True})
            if guarded_spec is not None and guarded_anchor is not None:
                pending_followup = (guarded_spec, guarded_anchor)
            # Do not ask the model for another input until fresh pixels arrive.
            frame = await screenshot(portal)
            journal.frame(step + 1, frame)
            store.observation(step + 1, frame)
        else:
            status = "step_limit"
    except UncertainAction:
        status = "uncertain_input"
        journal.record("stopped", reason="input outcome unknown; no automatic replay")
    except asyncio.CancelledError:
        status = "cancelled"
        journal.record("stopped", reason="cancelled; inspect before retrying")
        raise
    except ModelOutputError as exc:
        usage(exc.metadata)
        journal.record("error", error_type=type(exc).__name__, **exc.metadata)
        raise
    except (VisionRequestError, VisionTransportError) as exc:
        usage(exc.metadata)
        journal.record("error", error_type=type(exc).__name__, **exc.metadata)
        raise
    except Exception as exc:
        # Avoid exception strings that could contain keys, URLs or field values.
        journal.record("error", error_type=type(exc).__name__)
        raise
    finally:
        result = {"status": status, "model_calls": calls, "acknowledged_inputs": mutations,
                  "reported_total_tokens": total_tokens, "verified_success": False,
                  "token_counts": token_counts, "model_usage": model_usage,
                  "finding_count": store.context()["finding_count"],
                  "elapsed_seconds": round(time.monotonic() - started, 2),
                  "watch_seconds": round(watch_seconds, 2),
                  "account_confirmed": scope.confirmed,
                  "entered_text_fields": sorted(scope.entered_text_fields),
                  "preview_confirmed": scope.preview,
                  "media_confirmed": scope.media_confirmed,
                  "target_confirmed": scope.target_observation is not None,
                  "content_confirmed": scope.content_observation is not None,
                  "confirmed_media_format": (scope.media_observation or {}).get("media_format") if scope.media_confirmed else None,
                  "public_action_attempts": scope.used, "selected_sound": scope.selected_sound,
                  "deletion_target_id": task.get("deletion", {}).get("target_id") if scope.used["delete"] else None,
                  "publication_account": publication_account}
        journal.record("run_finished", **result)
        try:
            store.finish(result)
            result["reports"] = write_report(store)
        finally:
            store.close()
    return result

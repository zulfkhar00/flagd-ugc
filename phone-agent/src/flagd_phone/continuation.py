"""One guarded, non-public follow-up chosen with a visual phone decision.

The model may predict one routine next action. The controller only executes it
after a fresh screenshot and accessibility snapshot match the predicted target.
An uncertain or changed screen simply returns control to the visual agent.
"""
from __future__ import annotations

import re
from typing import Any

from .semantic import SemanticScreen
from .vision import Frame, validate_action


PUBLIC_LABEL = re.compile(
    r"\b(post|publish|share|send|delete|remove|like|follow|subscribe|"
    r"buy|purchase|pay|checkout|confirm|comment|submit)\b", re.I,
)
SEARCH_LABEL = re.compile(r"search|find|query", re.I)
OPERATIONS = {"none", "tap", "type_query", "submit_search"}
SAFE_NAV_LABELS = {"search", "explore", "discover", "home", "back", "profile", "for you"}


def validate_followup(spec: Any, first_action: dict[str, Any],
                      text_fields: dict[str, str] | None = None) -> dict[str, str] | None:
    """Reject plans that could become public, exact-copy, or blind inputs."""
    if not isinstance(spec, dict) or set(spec) != {"operation", "target_label", "query", "reason"}:
        return None
    operation, label, query, reason = (spec[key] for key in
                                       ("operation", "target_label", "query", "reason"))
    if not isinstance(operation, str) or operation not in OPERATIONS or operation == "none":
        return None
    if (first_action["kind"] not in ("tap", "type_query", "type_text")
            or first_action.get("effect", "none") != "none"
            or first_action["requires_review"] or first_action["confidence"] < 0.85):
        return None
    if (not isinstance(label, str) or not label.strip() or len(label) > 100
            or not isinstance(query, str) or len(query) > 200
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 200):
        return None
    if operation == "submit_search":
        if first_action["kind"] == "type_query":
            entered = first_action["query"]
        elif first_action["kind"] == "type_text":
            entered = (text_fields or {}).get(first_action.get("text_key"))
        else:
            return None
        if query != entered:
            return None
    elif operation == "type_query":
        if first_action["kind"] != "tap" or not query.strip() or any(ord(c) < 32 for c in query):
            return None
    elif operation == "tap":
        if label.strip().casefold() not in SAFE_NAV_LABELS:
            return None
        if first_action["kind"] == "type_text":
            if (label.strip().casefold() != "search"
                    or query != (text_fields or {}).get(first_action.get("text_key"))):
                return None
        elif first_action["kind"] != "tap" or query:
            return None
    return {"operation": operation, "target_label": label.strip(),
            "query": query, "reason": reason}


def resolve_followup(spec: dict[str, str], screen: SemanticScreen, frame: Frame,
                     *, expected_app: str, text_fields: dict[str, str]) -> dict[str, Any] | None:
    """Resolve a predicted action only against the current observed screen."""
    if not expected_app or screen.app != expected_app or frame.is_black:
        return None
    operation = spec["operation"]
    label = spec["target_label"].casefold()
    action: dict[str, Any] = {
        "kind": operation, "frame_id": frame.id, "reason": spec["reason"],
        "expected_change": "Continue only after observing this guarded action's result",
        "confidence": 0.9, "requires_review": False, "effect": "none",
    }
    if operation == "tap":
        if spec["query"]:
            editors = [e for e in screen.elements if e.editable and e.focused and not e.password
                       and spec["query"].casefold() in e.label.casefold()]
            if len(editors) != 1:
                return None
        matches = [e for e in screen.elements if e.clickable and not e.password
                   and e.label.casefold() == label and not PUBLIC_LABEL.search(e.label)]
        if len(matches) != 1:
            return None
        action["x"], action["y"] = screen.view_center(matches[0].id, frame)
    else:
        editors = [e for e in screen.elements if e.editable and e.focused and not e.password
                   and SEARCH_LABEL.search(f"{e.label} {e.resource_id}")]
        if len(editors) != 1:
            return None
        editor = editors[0]
        if operation == "type_query":
            if label not in f"{editor.label} {editor.resource_id}".casefold():
                return None
            if spec["query"] in text_fields.values():
                # Exact supplied copy must go through the normal type_text path.
                return None
            action["query"] = spec["query"]
        elif operation == "submit_search":
            if label != spec["query"].casefold() or spec["query"].casefold() not in editor.label.casefold():
                return None
        else:
            return None
    try:
        return validate_action(action, frame, text_fields)
    except ValueError:
        return None

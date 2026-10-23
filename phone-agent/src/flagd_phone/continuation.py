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
OPERATIONS = {"none", "tap", "type_query", "type_text", "submit_search"}
SAFE_NAV_LABELS = {"search", "explore", "discover", "home", "back", "profile", "for you"}
EXACT_FIELD_LABELS = {"title", "description", "caption", "url", "address"}


def _label_words(value: str) -> set[str]:
    # Accessibility resource IDs often use camelCase (for example titleInput).
    separated = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return set(re.findall(r"[a-z0-9]+", separated.casefold()))


def _exact_field_target(text_key: str, label: str) -> bool:
    if label not in EXACT_FIELD_LABELS:
        return False
    field = text_key.casefold().split("_")[-1]
    return field == label or (field == "url" and label == "address")


def validate_followup(spec: Any, first_action: dict[str, Any],
                      text_fields: dict[str, str] | None = None) -> dict[str, str] | None:
    """Reject plans that could become public or enter ungrounded copy."""
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
    elif operation == "type_text":
        # query is an exact supplied field KEY, never model-generated copy.
        if (first_action["kind"] != "tap" or query not in (text_fields or {})
                or not _exact_field_target(query, label.strip().casefold())):
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
                     *, expected_app: str, text_fields: dict[str, str],
                     diagnostics: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve a predicted action only against the current observed screen."""
    def reject(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["rejection"] = reason
        return None

    if not expected_app or screen.app != expected_app:
        return reject("app_changed")
    if frame.is_black:
        return reject("black_frame")
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
                return reject("search_editor_not_unique")
        matches = [e for e in screen.elements if e.clickable and not e.password
                   and e.label.casefold() == label and not PUBLIC_LABEL.search(e.label)]
        if len(matches) != 1:
            return reject("navigation_target_not_unique")
        action["x"], action["y"] = screen.view_center(matches[0].id, frame)
    else:
        focused = [e for e in screen.elements if e.editable and e.focused and not e.password]
        editors = [e for e in focused
                   if (SEARCH_LABEL.search(f"{e.label} {e.resource_id}")
                       if operation in ("type_query", "submit_search")
                       else label in _label_words(f"{e.hint} {e.resource_id}"))]
        if diagnostics is not None:
            diagnostics["focused_editor_count"] = len(focused)
            diagnostics["matching_editor_count"] = len(editors)
        if len(editors) != 1:
            return reject("editor_not_unique")
        editor = editors[0]
        if operation == "type_text":
            if not _exact_field_target(spec["query"], label):
                return reject("field_key_mismatch")
            placeholder = bool(editor.hint and editor.text.casefold() == editor.hint.casefold())
            if label in ("url", "address") and editor.text.casefold() in {
                    "search google or type url", "search or type url",
                    "search or type web address"}:
                placeholder = True
            if editor.text and not placeholder:
                return reject("editor_not_empty")
            action["text_key"] = spec["query"]
        elif operation == "type_query":
            if label not in f"{editor.label} {editor.resource_id}".casefold():
                return reject("search_label_mismatch")
            if spec["query"] in text_fields.values():
                # Exact supplied copy must go through the normal type_text path.
                return reject("query_is_exact_copy")
            action["query"] = spec["query"]
        elif operation == "submit_search":
            if label != spec["query"].casefold() or spec["query"].casefold() not in editor.label.casefold():
                return reject("search_query_mismatch")
        else:
            return reject("unknown_operation")
    try:
        return validate_action(action, frame, text_fields)
    except ValueError:
        return reject("invalid_action")

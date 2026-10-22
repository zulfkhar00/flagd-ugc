"""Compact, visible Android accessibility state for optional text-first decisions.

Portal's `state` response is untrusted screen data. Element IDs belong only to
one snapshot; callers must resolve them before executing an input.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .vision import Frame


def _clean(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _bounds(value: Any, width: int, height: int) -> tuple[int, int, int, int] | None:
    if isinstance(value, dict):
        raw = [value.get(key) for key in ("left", "top", "right", "bottom")]
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        raw = list(value)
    else:
        return None
    if any(type(part) not in (int, float) for part in raw):
        return None
    left, top, right, bottom = [round(part) for part in raw]
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    return (left, top, right, bottom) if left < right and top < bottom else None


def _flag(node: dict[str, Any], *keys: str) -> bool:
    return any(node.get(key) is True for key in keys)


def _descendant_label(node: dict[str, Any]) -> str:
    """Give a clickable wrapper the label of its nearby text child."""
    labels: list[str] = []
    queue = list(node.get("children", [])) if isinstance(node.get("children"), list) else []
    for child in queue[:12]:
        if not isinstance(child, dict) or _flag(child, "isPassword", "password"):
            continue
        label = _clean(child.get("text") or child.get("contentDescription") or child.get("hint"), 80)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 2:
            break
    return " ".join(labels)[:160]


@dataclass(frozen=True)
class Element:
    id: int
    bounds: tuple[int, int, int, int]
    label: str
    role: str
    resource_id: str
    clickable: bool
    editable: bool
    focused: bool
    scrollable: bool
    checked: bool
    selected: bool
    password: bool

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True)
class SemanticScreen:
    elements: tuple[Element, ...]
    width: int
    height: int
    captured_at: float
    fingerprint: str
    source_bytes: int
    app: str = ""

    @classmethod
    def from_portal(cls, result: Any, frame: Frame, *, max_elements: int = 100) -> SemanticScreen:
        if isinstance(result, str):
            if len(result) > 2_000_000:
                raise ValueError("Portal state is too large")
            result = json.loads(result)
        if not isinstance(result, (dict, list)):
            raise ValueError("Portal state must be a JSON tree")
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("Portal state is too large")
        # WebSocket state is normally an object. Support a nested JSON result
        # without assuming one Portal version's envelope names.
        if isinstance(result, dict) and isinstance(result.get("result"), str):
            try:
                nested = json.loads(result["result"])
            except ValueError:
                nested = None
            if isinstance(nested, (dict, list)):
                result = nested
        app = ""
        if isinstance(result, dict):
            phone = result.get("phone_state") or result.get("phoneState") or {}
            if isinstance(phone, dict):
                app = _clean(phone.get("packageName") or phone.get("foregroundPackage")
                             or phone.get("package_name"), 100)
        elements: list[Element] = []
        visited = 0

        def walk(value: Any) -> None:
            nonlocal visited
            if visited >= 4000 or len(elements) >= max_elements:
                return
            visited += 1
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            bounds = _bounds(value.get("boundsInScreen") or value.get("bounds"), frame.width, frame.height)
            visible = value.get("isVisibleToUser", value.get("visibleToUser", True)) is not False
            if bounds and visible:
                password = _flag(value, "isPassword", "password")
                label = "[password field]" if password else _clean(
                    value.get("text") or value.get("contentDescription") or value.get("hint"))
                clickable = _flag(value, "isClickable", "clickable")
                editable = _flag(value, "isEditable", "editable")
                scrollable = _flag(value, "isScrollable", "scrollable")
                if clickable and not label and not password:
                    area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
                    if area < frame.width * frame.height * 0.5:
                        label = _descendant_label(value)
                if label or clickable or editable or scrollable:
                    elements.append(Element(
                        id=len(elements) + 1, bounds=bounds, label=label,
                        role=_clean(value.get("className") or value.get("role"), 80).rsplit(".", 1)[-1],
                        resource_id=_clean(value.get("resourceId") or value.get("viewIdResourceName"), 100),
                        clickable=clickable, editable=editable,
                        focused=_flag(value, "isFocused", "focused"),
                        scrollable=scrollable, checked=_flag(value, "isChecked", "checked"),
                        selected=_flag(value, "isSelected", "selected"), password=password,
                    ))
            for key, child in value.items():
                if isinstance(child, (dict, list)) and key not in ("bounds", "boundsInScreen"):
                    walk(child)
                elif key in ("a11y_tree", "ui_tree", "tree", "result") and isinstance(child, str):
                    try:
                        parsed = json.loads(child)
                    except ValueError:
                        continue
                    if isinstance(parsed, (dict, list)):
                        walk(parsed)

        walk(result)
        fingerprint = hashlib.sha256(json.dumps(
            [(e.bounds, e.label, e.clickable, e.editable, e.focused, e.checked, e.selected)
             for e in elements], ensure_ascii=False, separators=(",", ":")
        ).encode()).hexdigest()
        return cls(tuple(elements), frame.width, frame.height, time.monotonic(),
                   fingerprint, len(encoded.encode("utf-8")), app)

    def compact(self) -> str:
        lines = [f"screen={self.width}x{self.height} app={self.app or 'unknown'}"]
        for element in self.elements:
            flags = ",".join(name for name, enabled in (
                ("click", element.clickable), ("edit", element.editable),
                ("focus", element.focused), ("scroll", element.scrollable),
                ("checked", element.checked), ("selected", element.selected),
            ) if enabled)
            label = json.dumps(element.label, ensure_ascii=False)
            lines.append(f"[{element.id}] {element.role or 'view'} {label} "
                         f"bounds={','.join(map(str, element.bounds))} {flags}")
        return "\n".join(lines)

    def target(self, element_id: Any) -> Element:
        if type(element_id) is not int or not 1 <= element_id <= len(self.elements):
            raise ValueError("unknown semantic element ID")
        return self.elements[element_id - 1]

    def view_center(self, element_id: int, frame: Frame) -> tuple[float, float]:
        element = self.target(element_id)
        x, y = element.center
        return (x * (frame.view_width - 1) / max(1, frame.width - 1),
                y * (frame.view_height - 1) / max(1, frame.height - 1))

    @property
    def useful(self) -> bool:
        return sum(bool(e.label) for e in self.elements) >= 3 and any(
            (e.clickable or e.editable) and bool(e.label) for e in self.elements)

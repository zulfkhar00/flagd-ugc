from __future__ import annotations

import base64
import io
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops


@dataclass(frozen=True)
class Frame:
    id: str
    png: bytes
    jpeg: bytes
    width: int
    height: int
    view_width: int
    view_height: int
    captured_at: float
    portal_screenshot_ms: float = 0.0
    host_prepare_ms: float = 0.0

    @property
    def is_black(self) -> bool:
        """Exact black capture, not a dim interface or low-contrast video."""
        with Image.open(io.BytesIO(self.png)) as image:
            return all(high == 0 for _, high in image.convert("RGB").getextrema())

    def materially_differs(self, other: Frame, *, at: tuple[int, int] | None = None) -> bool:
        """Detect a changed screen while a model decision was being computed.

        Ignore minor caret/status animation; this is a pixel-change heuristic,
        not a classifier of the target or proof that a screen is safe to tap.
        """
        if (self.width, self.height) != (other.width, other.height):
            return True
        with Image.open(io.BytesIO(self.png)) as before, Image.open(io.BytesIO(other.png)) as after:
            if at is not None:
                x, y = at
                # Recheck the tap's target rather than unrelated auto-playing
                # carousel content. Next changing to Post still changes here.
                # Keep the vertical window on the control itself. A taller
                # window includes live video above a stable navigation bar and
                # can reject every attempt to leave that feed.
                dx, dy = round(self.width * 0.13), round(self.height * 0.015)
                bounds = (max(0, x - dx), max(0, y - dy),
                          min(self.width, x + dx + 1), min(self.height, y + dy + 1))
                before, after = before.crop(bounds), after.crop(bounds)
            size = (96, max(1, round(96 * before.height / before.width)))
            a = before.convert("RGB").resize(size, Image.Resampling.BILINEAR)
            b = after.convert("RGB").resize(size, Image.Resampling.BILINEAR)
            r, g, b = ImageChops.difference(a, b).split()
            difference = ImageChops.lighter(ImageChops.lighter(r, g), b)
            changed = sum(difference.histogram()[26:])
            return changed / (size[0] * size[1]) > 0.02

    @classmethod
    def from_portal(cls, result: Any, *, max_side: int = 1024) -> Frame:
        if not isinstance(result, str) or len(result) > 24 * 1024 * 1024:
            raise ValueError("expected a base64 PNG screenshot of at most 18 MB")
        if max_side < 256 or max_side > 2048:
            raise ValueError("screenshot max_side must be between 256 and 2048")
        png = base64.b64decode(result, validate=True)
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or image.width * image.height > 20_000_000:
                raise ValueError("invalid or excessively large PNG screenshot")
            width, height = image.size
            view = image.convert("RGB")
            view.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            view.save(output, format="JPEG", quality=85)
            return cls(str(uuid.uuid4()), png, output.getvalue(), width, height,
                       view.width, view.height, time.monotonic())

    def to_device(self, x: Any, y: Any) -> tuple[int, int]:
        for value, size in ((x, self.view_width), (y, self.view_height)):
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= size - 1):
                raise ValueError("coordinates must be inside the current model screenshot")
        return (
            round(x * (self.width - 1) / max(1, self.view_width - 1)),
            round(y * (self.height - 1) / max(1, self.view_height - 1)),
        )


KINDS = ("tap", "swipe", "type_text", "type_query", "type_comment", "submit_search",
         "wait", "watch", "confirm_account", "confirm_preview", "confirm_media",
         "confirm_target", "confirm_content", "done", "handoff")
CAROUSEL_CHECKS = ("native_photo_carousel", "first_slide_matches_source",
                   "cover_matches_first_slide", "slide_order_matches_source",
                   "no_extra_images", "title_matches_source", "description_matches_source")
ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "frame_id": {"type": "string"},
        "reason": {"type": "string"},
        "expected_change": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "requires_review": {"type": "boolean"},
        "x": {"type": "number"}, "y": {"type": "number"},
        "end_x": {"type": "number"}, "end_y": {"type": "number"},
        "duration_ms": {"type": "integer", "minimum": 100, "maximum": 60000},
        "playback_ms": {"type": "integer", "minimum": 1000, "maximum": 15000},
        "repeat": {"type": "integer", "minimum": 1, "maximum": 2},
        "text_key": {"type": "string"},
        "query": {"type": "string"},
        "comment": {"type": "string"},
        "visible_context": {"type": "string"},
        "effect": {"type": "string", "enum": ["none", "like", "comment", "publish", "delete"]},
        "account": {"type": "string"}, "app": {"type": "string"},
        "evidence": {"type": "string"},
        "slide_count": {"type": "integer", "minimum": 6, "maximum": 8},
        "selected_sound": {"type": "string"},
        "media_format": {"type": "string"},
        "music_enabled": {"type": "boolean"},
        "published_account": {"type": "string", "maxLength": 30},
        **{key: {"type": "boolean"} for key in CAROUSEL_CHECKS},
    },
    "required": ["kind", "frame_id", "reason", "expected_change", "confidence", "requires_review"],
    "additionalProperties": False,
}


def validate_identity_spec(value: Any) -> None:
    if (not isinstance(value, dict) or set(value) != {"required_text", "any_text"}
            or not isinstance(value["required_text"], list) or not value["required_text"]
            or not isinstance(value["any_text"], list)
            or any(len(value[key]) > 10 or any(not isinstance(text, str) or not text.strip()
                                             or len(text) > 500 for text in value[key])
                   for key in ("required_text", "any_text"))):
        raise ValueError("visual identity needs bounded required and alternative visible text")


def identity_text_matches(spec: dict[str, Any], visible_text: str) -> bool:
    def words(value: str) -> str:
        return " ".join(re.findall(r"\w+", value.casefold()))
    observed = " " + words(visible_text) + " "
    present = lambda text: " " + words(text) + " " in observed
    return (all(present(text) for text in spec["required_text"])
            and (not spec["any_text"] or any(present(text) for text in spec["any_text"])))


def validate_action(action: Any, frame: Frame, text_fields: dict[str, str]) -> dict[str, Any]:
    if not isinstance(action, dict) or set(action) - ACTION_SCHEMA["properties"].keys():
        raise ValueError("invalid action fields")
    if not set(ACTION_SCHEMA["required"]) <= action.keys():
        raise ValueError("missing action fields")
    if action["kind"] not in KINDS or action["frame_id"] != frame.id:
        raise ValueError("unknown action or stale frame ID")
    confidence = action["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("invalid confidence")
    if type(action["requires_review"]) is not bool:
        raise ValueError("requires_review must be a boolean")
    for key in ("reason", "expected_change"):
        if not isinstance(action[key], str) or len(action[key]) > 1000:
            raise ValueError(f"invalid {key}")
    kind = action["kind"]
    allowed = set(ACTION_SCHEMA["required"])
    allowed.add("effect")
    effect = action.get("effect", "none")
    if effect not in ("none", "like", "comment", "publish", "delete") or (effect != "none" and kind != "tap"):
        raise ValueError("public effects must label a tap action")
    if kind in ("tap", "swipe"):
        allowed.update(("x", "y"))
        frame.to_device(action.get("x"), action.get("y"))
    if kind == "swipe":
        allowed.update(("end_x", "end_y", "duration_ms", "repeat", "playback_ms"))
        frame.to_device(action.get("end_x"), action.get("end_y"))
        if "playback_ms" in action and (type(action["playback_ms"]) is not int
                                     or not 1000 <= action["playback_ms"] <= 15000):
            raise ValueError("swipe playback must be 1–15 seconds")
        repeat = action.get("repeat", 1)
        if type(repeat) is not int or repeat not in (1, 2):
            raise ValueError("swipe repeat must be 1 or 2")
        if "playback_ms" in action and repeat != 1:
            raise ValueError("video playback cannot be combined with a repeated swipe")
        if repeat == 2:
            width, height = frame.view_width - 1, frame.view_height - 1
            left_back = action["x"] <= width * 0.025 and action["end_x"] >= width * 0.2
            right_back = action["x"] >= width * 0.975 and action["end_x"] <= width * 0.8
            if not (left_back or right_back) or abs(action["end_y"] - action["y"]) > height * 0.08:
                raise ValueError("only a horizontal edge-back gesture may repeat")
    if kind in ("wait", "watch"):
        allowed.add("duration_ms")
    if kind in ("swipe", "wait", "watch"):
        duration = action.get("duration_ms", 500)
        maximum = 15000 if kind == "watch" else 10000 if kind == "wait" else 2000
        if type(duration) is not int or not 100 <= duration <= maximum:
            raise ValueError("invalid duration")
    if kind == "type_text":
        allowed.add("text_key")
        if not isinstance(action.get("text_key"), str) or action["text_key"] not in text_fields:
            raise ValueError("typing must select an exact supplied text field")
    if kind == "type_query":
        allowed.add("query")
        query = action.get("query")
        if (not isinstance(query, str) or not query.strip() or len(query) > 200
                or any(ord(c) < 32 or ord(c) == 127 for c in query)):
            raise ValueError("search queries must be 1–200 characters without control characters")
    if kind == "type_comment":
        allowed.update(("comment", "visible_context"))
        for key, limit in (("comment", 300), ("visible_context", 500)):
            if (not isinstance(action.get(key), str) or not action[key].strip()
                    or len(action[key]) > limit or any(ord(c) < 32 for c in action[key])):
                raise ValueError("comments need short text and readable current screen context")
    if kind in ("confirm_account", "confirm_preview", "confirm_media", "confirm_target", "confirm_content"):
        allowed.add("evidence")
        if not isinstance(action.get("evidence"), str) or not action["evidence"].strip() or len(action["evidence"]) > 1000:
            raise ValueError("checkpoint needs visible evidence")
    if kind == "confirm_account":
        allowed.update(("account", "app"))
        if not isinstance(action.get("account"), str) or not isinstance(action.get("app"), str):
            raise ValueError("checkpoint needs the observed app and exact username")
    if kind == "confirm_preview":
        allowed.update(("slide_count", "selected_sound", *CAROUSEL_CHECKS))
        if type(action.get("slide_count")) is not int or not 6 <= action["slide_count"] <= 8:
            raise ValueError("invalid preview slide count")
        if not isinstance(action.get("selected_sound"), str) or not action["selected_sound"].strip() or len(action["selected_sound"]) > 300:
            raise ValueError("preview needs the visibly selected sound")
        if any(type(action.get(key)) is not bool for key in CAROUSEL_CHECKS):
            raise ValueError("preview needs explicit image, cover, title and description checks")
    if kind == "confirm_media":
        allowed.update(("media_format", "selected_sound", "music_enabled"))
        if (not isinstance(action.get("media_format"), str) or not action["media_format"].strip()
                or len(action["media_format"]) > 80):
            raise ValueError("media checkpoint needs the observed publication format")
        if (not isinstance(action.get("selected_sound"), str) or len(action["selected_sound"]) > 300
                or type(action.get("music_enabled")) is not bool):
            raise ValueError("media checkpoint needs the selected sound and enabled state")
        if action["music_enabled"] and not action["selected_sound"].strip():
            raise ValueError("enabled music needs its visibly selected track name")
    if kind == "done":
        allowed.add("published_account")
        account = action.get("published_account", "")
        if not isinstance(account, str) or (account and not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", account)):
            raise ValueError("published_account must be a visible username without @, or empty")
    if set(action) - allowed:
        raise ValueError("fields do not belong to the selected action")
    return action

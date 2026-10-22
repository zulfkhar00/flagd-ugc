from __future__ import annotations

from typing import Any

from .vision import CAROUSEL_CHECKS
from .workflows import account_name


EFFECTS = ("like", "comment", "publish", "delete")


def validate_authorization(value: Any) -> None:
    if (not isinstance(value, dict) or not {"app", "account", "limits"} <= set(value)
            or set(value) - {"app", "account", "limits", "verification"}):
        raise ValueError("authorization needs app, account and action limits")
    if value["app"] not in ("TikTok", "Instagram"):
        raise ValueError("invalid authorized app")
    if account_name(value["account"]) != value["account"]:
        raise ValueError("authorized account must omit @")
    limits = value["limits"]
    if (not isinstance(limits, dict) or set(limits) - set(EFFECTS)
            or any(type(v) is not int or not 0 <= v <= 20 for v in limits.values())
            or limits.get("publish", 0) > 1 or limits.get("delete", 0) > 1):
        raise ValueError("invalid authorized action limits")
    if "verification" in value:
        checks = value["verification"]
        if (not isinstance(checks, dict) or set(checks) != {"account", "preview"}
                or any(type(required) is not bool for required in checks.values())):
            raise ValueError("verification must explicitly declare account and preview booleans")


class AuthorizationState:
    """Task-scoped permissions, observed account checkpoint, and single Post tap.

    Screen interpretation remains a model assessment. This enforces declared
    action scope; it is not an independent classifier of every tap target.
    """
    def __init__(self, task: dict[str, Any]):
        self.scope = task.get("authorization")
        self.publication = task.get("publication")
        self.deletion = task.get("deletion")
        self.checks = (self.scope or {}).get("verification", {"account": True, "preview": True})
        self.confirmed = False
        self.preview = False
        self.media_confirmed = False
        self.media_observation: dict[str, Any] | None = None
        self.target_observation: dict[str, Any] | None = None
        self.content_observation: dict[str, Any] | None = None
        self.selected_sound: str | None = None
        self.entered_text_fields: set[str] = set()
        self.used = {kind: 0 for kind in EFFECTS}

    def context(self) -> dict[str, Any]:
        return {"scope": self.scope, "account_confirmed": self.confirmed,
                "account_check_required": self.checks["account"],
                "preview_check_required": self.checks["preview"],
                "preview_confirmed": self.preview,
                "media_confirmed": self.media_confirmed,
                "last_media_observation": self.media_observation,
                "deletion": self.deletion,
                "target_confirmed": self.target_observation is not None,
                "content_confirmed": self.content_observation is not None,
                "last_content_observation": self.content_observation,
                "entered_text_fields": sorted(self.entered_text_fields),
                "remaining": {kind: max(0, (self.scope or {}).get("limits", {}).get(kind, 0) - count)
                              for kind, count in self.used.items()}}

    def check(self, action: dict[str, Any]) -> None:
        kind, effect = action["kind"], action.get("effect", "none")
        if kind == "confirm_account":
            if (not self.scope or action["account"] != self.scope["account"]
                    or action["app"] != self.scope["app"]):
                raise ValueError("observed account does not match this job")
        if kind == "confirm_preview":
            if ((self.checks["account"] and not self.confirmed) or not self.publication
                    or action["slide_count"] != self.publication["slide_count"]):
                raise ValueError("preview needs the confirmed account and expected slide count")
            if any(action.get(key) is not True for key in CAROUSEL_CHECKS):
                raise ValueError("preview must match the source's first slide, cover and order, with no extra images")
            if not set(self.publication.get("required_text_fields", [])) <= self.entered_text_fields:
                raise ValueError("preview needs all supplied posting fields entered before publishing")
        if kind == "confirm_media":
            if not self.publication or (self.checks["account"] and not self.confirmed):
                raise ValueError("media confirmation needs the requested account and publication")
            if self.publication.get("media_format") and action["media_format"] != self.publication["media_format"]:
                raise ValueError("observed media format does not match this publication")
            if self.publication.get("require_music") and (
                    action["music_enabled"] is not True or not action["selected_sound"].strip()
                    or action["selected_sound"].strip().casefold() in ("none", "no music", "silent", "no audio")):
                raise ValueError("publication requires a selected, enabled music track")
        if kind == "confirm_target" and (not self.deletion or not self.confirmed):
            raise ValueError("deletion target needs the confirmed account and an exact target specification")
        if kind == "confirm_content" and (not self.publication or not self.confirmed
                                           or not self.publication.get("content_identity")):
            raise ValueError("content checkpoint needs the confirmed account and source identity")
        if kind in ("watch", "type_comment") and self.checks["account"] and not self.confirmed:
            raise ValueError("verify the requested account before browsing or composing comments")
        required = "comment" if kind == "type_comment" else effect
        if required != "none":
            if ((self.checks["account"] and not self.confirmed)
                    or self.context()["remaining"].get(required, 0) < 1):
                raise ValueError("public action is outside this job's authorization")
            if required == "publish" and self.checks["preview"] and not self.preview:
                raise ValueError("check the carousel preview before publishing")
            if required == "publish" and not set((self.publication or {}).get("required_text_fields", [])) <= self.entered_text_fields:
                raise ValueError("enter all supplied posting fields before publishing")
            if required == "publish" and ((self.publication or {}).get("media_format")
                    or (self.publication or {}).get("require_music")) and not self.media_confirmed:
                raise ValueError("confirm the requested media format and music before publishing")
            if required == "publish" and (self.publication or {}).get("content_identity") and not self.content_observation:
                raise ValueError("confirm the actual selected source content before publishing")
            if required == "delete" and (not self.deletion or not self.target_observation):
                raise ValueError("confirm the exact published deletion target before deleting")

    def record_before_input(self, action: dict[str, Any]) -> None:
        # Consume before dispatch: a lost acknowledgement is not permission
        # to send another public action. A task failure does not refund it.
        effect = action.get("effect", "none")
        if effect != "none":
            self.used[effect] += 1
        if action["kind"] == "confirm_account":
            self.confirmed = True
        if action["kind"] == "confirm_preview":
            self.preview = True
            self.selected_sound = action["selected_sound"]
        elif action["kind"] == "confirm_target":
            self.target_observation = {"frame_id": action["frame_id"], "evidence": action["evidence"]}
        elif action["kind"] == "confirm_content":
            self.content_observation = {"frame_id": action["frame_id"], "evidence": action["evidence"]}
        elif action["kind"] == "confirm_media":
            self.media_confirmed = True
            self.media_observation = {key: action[key] for key in ("media_format", "selected_sound", "music_enabled", "evidence")}
            self.selected_sound = action["selected_sound"] if action["music_enabled"] else None
        elif action["kind"] in ("tap", "swipe", "type_text", "type_query",
                                 "type_comment", "submit_search") and effect != "publish":
            # A verified composer must be checked again after any navigation or
            # edit. In particular, removing a collage invalidates the old check.
            self.invalidate_preview()
            self.invalidate_media()
        if action["kind"] in ("swipe", "type_text", "type_query", "submit_search"):
            # Target proof may survive its overflow menu and confirmation dialog,
            # but not navigation to another item/account.
            self.target_observation = None

    def invalidate_preview(self) -> None:
        self.preview = False
        self.selected_sound = None

    def invalidate_media(self) -> None:
        self.media_confirmed = False

    def record_after_input(self, action: dict[str, Any]) -> None:
        if action["kind"] == "type_text":
            self.entered_text_fields.add(action["text_key"])

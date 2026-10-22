"""Owned-account browsing and Volume-to-native-carousel task construction."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TITLE_LIMIT = 59
CALIFORNIA = ZoneInfo("America/Los_Angeles")


def account_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("account must be a username")
    value = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", value):
        raise ValueError("account must be a username")
    return value


def split_caption(caption: str) -> dict[str, str]:
    """Keep the first paragraph and remaining copy separate; never truncate."""
    if not isinstance(caption, str):
        raise ValueError("caption must be text")
    normalized = caption.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = re.split(r"\n[ \t]*\n", normalized, maxsplit=1)
    if len(paragraphs) != 2:
        raise ValueError("caption needs a title paragraph, a blank line, and a description")
    title, description = (part.strip() for part in paragraphs)
    if not title or "\n" in title or len(title) > TITLE_LIMIT:
        raise ValueError(f"first caption paragraph must be one line of 1–{TITLE_LIMIT} characters; got {len(title)}")
    if not description or len(description) > 4000:
        raise ValueError("description must be 1–4000 characters")
    return {"title": title, "description": description}


def browse_task(*, app: str, account: str, keywords: list[str], seconds: int = 900,
                max_likes: int = 0, max_comments: int = 0) -> dict[str, Any]:
    from .runner import validate_task
    if app not in ("TikTok", "Instagram"):
        raise ValueError("supported social workflow apps are TikTok and Instagram")
    account = account_name(account)
    if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 10
            or any(not isinstance(x, str) or not x.strip() or len(x) > 200
                   or any(ord(c) < 32 for c in x) for x in keywords)):
        raise ValueError("supply 1–10 search keywords of 1–200 characters")
    return validate_task({
        "mode": "browse", "duration_seconds": seconds, "max_steps": 300,
        "goal": (
            f"Browse {app} on already logged-in account @{account} for {seconds // 60} minutes. "
            "Confirm its visible username before browsing. Search the supplied exact keywords "
            "and watch relevant short videos briefly: usually 4–10 seconds, with occasional quicker "
            "skips or 12–15 second watches. On a visible full-screen video, prefer one swipe action "
            "with playback_ms as the total target viewing time, so the model does not need a "
            "separate call to decide the swipe. The time spent deciding already counts. "
            "Before another watch, move to a visibly different creator or video. If a swipe leaves "
            "the same video open, use a fresh screenshot to retry, then return to search results "
            "for another clip rather than watching it again. Change search keywords after a few videos. "
            "Occasionally visit comments, scroll discussions and expand replies before returning "
            "to more Reels. Optional relevant likes/comments stay within this job's allowances; "
            "skip unreadable context and never engage our own accounts. No follows/unfollows, "
            "messages, advertising or invented personal experiences. No research report is needed. "
            "Continue until the timer ends, using screenshots to handle navigation and interruptions."
        ),
        "text_fields": {f"keyword_{i + 1}": x for i, x in enumerate(keywords)},
        "authorization": {"app": app, "account": account,
                          "limits": {"like": max_likes, "comment": max_comments}},
    })


def slideshow_task(*, account: str, volume_id: str, share_url: str, caption: str,
                   slide_count: int, publish: bool = False) -> dict[str, Any]:
    from .runner import validate_task
    account = account_name(account)
    parts = urlsplit(share_url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or any(c.isspace() or ord(c) < 32 for c in share_url)):
        raise ValueError("share_url must be a valid HTTPS phone share link")
    if not isinstance(volume_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", volume_id):
        raise ValueError("volume_id must be a stable source ID")
    if type(slide_count) is not int or not 6 <= slide_count <= 8:
        raise ValueError("flagd slideshows need 6–8 ordered images")
    if type(publish) is not bool:
        raise ValueError("publish must be a boolean")
    fields = {**split_caption(caption), "volume_url": share_url}
    return validate_task({
        "mode": "publish", "max_steps": 100,
        "goal": (
            f"Prepare the Volume slideshow on TikTok account @{account}. First select this "
            "already logged-in account in TikTok and verify its visible username using "
            "confirm_account. Open Chrome through its visible UI, type volume_url into its "
            "address editor and open it with the keyboard. Use the Volume phone page's Share "
            f"button to send all {slide_count} photos to TikTok. Keep their original order. "
            "Inspect the source's first image before sharing. TikTok may open a collage/layout "
            "tool during sharing: cancel that tool instead of accepting its checkmark. Inspect "
            "the resulting photo strip. Remove any generated collage or extra image using "
            "visible controls; keep exactly the original photos. The first carousel image "
            "must be the source's first photo, and the thumbnail marked Cover must match it. "
            "Changing only the cover is insufficient if an extra collage remains. Count all "
            "photos in the strip, including the end; a collage layout labelled '6 photos' "
            "is not the carousel count. If these cannot be verified or corrected, stop. "
            "Verify it is a native swipeable photo carousel, not an image-to-video export. "
            "Read the caption on the Volume page if needed. Its first paragraph is supplied "
            "as title; all remaining paragraphs are supplied as description. Enter both "
            "verbatim in their separate TikTok fields, without truncating, adding or rewriting. "
            "Do not advance or post with either field empty. Visibly focus the title editor, "
            "enter title using type_text, then focus the description editor and enter the "
            "entire description using type_text. Inspect the entered title and both the "
            "beginning and end of the long description, scrolling when necessary. "
            "Open TikTok's suggested music list, choose an available suggested track, and "
            "verify and record its visible name. Do not post silently without selected music. "
            "Check slide count/order, correct images, title, full description, selected music "
            "and visible posting settings. Use confirm_preview only after these checks, "
            "including explicit checks that the first slide and cover match and no extra "
            "images remain. Recheck after any subsequent edit or navigation. "
            + ("Publish this authorized job once, then inspect the posted carousel on the "
               "intended profile. If posting is uncertain, stop; never retry the Post tap."
               if publish else "Stop at the final composer preview; do not publish.")
        ),
        "text_fields": fields, "text_limits": {"title": TITLE_LIMIT},
        "authorization": {"app": "TikTok", "account": account,
                          "limits": {"publish": 1 if publish else 0}},
        "publication": {"source_id": volume_id, "slide_count": slide_count,
                        "required_text_fields": ["title", "description"]},
    })


def next_california_slot(now: datetime, hhmm: str) -> datetime:
    """Next local daily slot, with DST derived from its calendar day."""
    if now.tzinfo is None or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hhmm):
        raise ValueError("use an aware datetime and HH:MM time")
    local = now.astimezone(CALIFORNIA)
    hour, minute = map(int, hhmm.split(":"))
    day = local.date()
    for _ in range(3):
        candidate = datetime.combine(day, time(hour, minute), CALIFORNIA)
        # Skip a nonexistent spring-forward slot instead of silently shifting it.
        roundtrip = candidate.astimezone(timezone.utc).astimezone(CALIFORNIA)
        if roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None) and candidate > local:
            return candidate
        day += timedelta(days=1)
    raise ValueError("cannot find next schedule slot")

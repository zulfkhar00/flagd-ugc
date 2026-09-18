from __future__ import annotations

import re
import unicodedata

from .errors import QualityError
from .hook_formats import validate_formatted_hook

WORD_RE = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)
FIRST_PERSON_RE = re.compile(r"\b(i|i'm|i’ve|i'd|me|my|mine)\b", re.IGNORECASE)
UNSAFE_PATTERNS = (
    r"\bguarantee(?:d|s)?\b",
    r"\balways works?\b",
    r"\bnever fails?\b",
    r"\b100\s*%",
    r"\bsaved my life\b",
    r"\bcure[sd]?\b",
    r"\bdiagnos(?:e|es|ed|is)\b",
)
TITLE_CTA_PATTERNS = (
    r"\bfollow\b",
    r"\blike\b",
    r"\bcomment\b",
    r"\bshare\b",
    r"\bsubscribe\b",
    r"\bclick\b",
    r"\btap\b",
    r"\bdownload\b",
    r"\blink\b",
)
URL_RE = re.compile(r"(?:https?://|www\.|\b\w+\.com\b)", re.IGNORECASE)
TITLE_NUMBER_RE = re.compile(r"\b\d+\b")


def word_count(hook: str) -> int:
    return len(WORD_RE.findall(hook))


def validate_hook(
    hook: str,
    *,
    hook_format: str | None = None,
    demo_description: str | None = None,
) -> str:
    normalized = " ".join(hook.strip().split())
    if not normalized:
        raise QualityError("hook is empty")
    count = word_count(normalized)
    if count >= 12:
        raise QualityError(f"hook must be under 12 words; got {count}")
    # Legacy callers use the original first-person rule. New format-aware callers use the
    # approved caption grammar instead: formats such as POV and `when ...` are still clearly
    # spoken from the on-camera woman's viewpoint without needing a literal I/me pronoun.
    if hook_format is None and not FIRST_PERSON_RE.search(normalized):
        raise QualityError("hook must use a first-person woman perspective")
    if "\n" in hook or any(character in normalized for character in "{}[]"):
        raise QualityError("hook contains unsafe formatting")
    if hook_format is not None and any(
        character in normalized for character in ('"', "“", "”")
    ):
        raise QualityError("formatted hook contains renderer-owned quotation marks")
    for pattern in UNSAFE_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise QualityError(f"hook contains an unsupported claim: {pattern}")
    if hook_format is not None:
        return validate_formatted_hook(
            normalized, hook_format, demo_description=demo_description
        )
    return normalized


def validate_title(title: str, *, demo_description: str | None = None) -> str:
    """Validate the short, factual label supplied with a manual-upload video.

    Semantic grounding is reviewed by Gemini using the supplied demo description.  This local
    gate handles the deterministic constraints that should never depend on model judgement.
    """
    normalized = " ".join(title.strip().split())
    if not normalized:
        raise QualityError("title is empty")
    count = word_count(normalized)
    if not 3 <= count <= 8:
        raise QualityError(f"title must contain three to eight words; got {count}")
    if normalized != normalized.lower():
        raise QualityError("title must use lower-case copy")
    if "\n" in title or any(character in normalized for character in "{}[]\"“”#"):
        raise QualityError("title contains unsafe formatting")
    if URL_RE.search(normalized):
        raise QualityError("title must not contain a URL")
    if any(
        unicodedata.category(character) in {"So", "Sk"} for character in normalized
    ):
        raise QualityError("title must not contain emoji")
    for pattern in UNSAFE_PATTERNS + TITLE_CTA_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise QualityError(f"title contains unsupported copy: {pattern}")
    supported_score = None
    if demo_description:
        score_match = re.search(
            r"\b(?:overall\s+(?:conversation\s+)?score|conversation\s+score|score)"
            r"\s*(?:of|:|is)?\s*(100|[1-9]\d?)"
            r"(?:\s*(?:/|out\s+of)\s*100)?\b",
            demo_description,
            re.IGNORECASE,
        )
        supported_score = score_match.group(1) if score_match else None
    for number in TITLE_NUMBER_RE.findall(normalized):
        if number != supported_score:
            raise QualityError("title number is not supported by its demo description")
    return normalized

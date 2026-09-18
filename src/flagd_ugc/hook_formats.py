from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import QualityError

if TYPE_CHECKING:
    from .assets import Demo


@dataclass(frozen=True)
class CaptionLine:
    """One line group in the opening's top-safe-area caption."""

    text: str
    font_scale: float = 1.0
    top_ratio: float = 0.086
    wrap_width: int = 23


@dataclass(frozen=True)
class CaptionPlan:
    """The display treatment for an already-validated hook."""

    lines: tuple[CaptionLine, ...]

    def summary(self) -> str:
        return " ".join(line.text for line in self.lines)


@dataclass(frozen=True)
class HookFormat:
    key: str
    name: str
    prompt_rule: str
    example: str
    caption_style: str = "classic"


_SCORE_DESCRIPTION_RE = re.compile(
    r"\b(?:overall\s+(?:conversation\s+)?score|conversation\s+score|score)"
    r"\s*(?:of|:|is)?\s*(?P<score>100|[1-9]\d?)"
    r"(?:\s*(?:/|out\s+of)\s*100)?\b",
    re.IGNORECASE,
)
_SCORECARD_RE = re.compile(
    r"^rating\s+(?:the\s+)?(?:chat|conversation):\s*(?P<score>100|[1-9]\d?)/100$",
    re.IGNORECASE,
)
_FRACTIONAL_SCORE_RE = re.compile(r"\b(?P<score>100|[1-9]\d?)/100\b")


HOOK_FORMATS: tuple[HookFormat, ...] = (
    HookFormat(
        key="pov",
        name="POV",
        prompt_rule=(
            "Start exactly with `pov:`. Follow it with a compact, specific first-person "
            "or observational payoff supported by the demo."
        ),
        example="pov: he thought he had game until this",
    ),
    HookFormat(
        key="quote-receipt",
        name="Quote / receipt",
        prompt_rule=(
            "Return only a short lower-case quote body of two to six words and at most 23 "
            "characters including spaces. Do not add quotation marks, an em dash, or an "
            "attribution; the renderer adds those."
        ),
        example="i'm good at texting",
        caption_style="quote-receipt",
    ),
    HookFormat(
        key="when",
        name="When reaction",
        prompt_rule=(
            "Start exactly with `when ` and describe the immediate situation that causes "
            "the on-camera reaction."
        ),
        example="when he says he's good at texting",
    ),
    HookFormat(
        key="direct-observation",
        name="Direct observation",
        prompt_rule=(
            "Write one plain, lower-case observation. Do not start with `pov:`, `when`, "
            "`my face when`, `rating`, or `i `."
        ),
        example="the 43/100 explained a lot",
    ),
    HookFormat(
        key="reaction-meme",
        name="Reaction meme",
        prompt_rule=(
            "Start exactly with `my face when ` and make the ending an event visibly "
            "supported by the matching demo."
        ),
        example="my face when i got the feedback",
    ),
    HookFormat(
        key="scorecard",
        name="Scorecard",
        prompt_rule=(
            "Use exactly `rating the chat: N/100`, replacing N only with the exact overall "
            "score stated in the demonstration description."
        ),
        example="rating the chat: 43/100",
        caption_style="scorecard",
    ),
    HookFormat(
        key="inner-thought",
        name="Inner thought",
        prompt_rule=(
            "Start exactly with `i ` and write one short, natural immediate thought about "
            "the visible feedback or result."
        ),
        example="i did not expect that feedback",
    ),
)

_BY_KEY = {item.key: item for item in HOOK_FORMATS}


def hook_format_keys() -> tuple[str, ...]:
    return tuple(item.key for item in HOOK_FORMATS)


def get_hook_format(key: str) -> HookFormat:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise QualityError(
            f"unknown hook format {key!r}; choose from {', '.join(hook_format_keys())}"
        ) from exc


def caption_plan(hook: str, hook_format: str) -> CaptionPlan:
    """Convert stored copy into the approved on-screen presentation."""
    format_definition = get_hook_format(hook_format)
    if format_definition.caption_style == "quote-receipt":
        return CaptionPlan(
            (
                CaptionLine(f"“{hook}”", top_ratio=0.078, wrap_width=23),
                CaptionLine(
                    "— him", font_scale=0.56, top_ratio=0.160, wrap_width=28
                ),
            )
        )
    if format_definition.caption_style == "scorecard":
        label, score = hook.split(":", maxsplit=1)
        return CaptionPlan(
            (
                CaptionLine(
                    f"{label.strip()}:",
                    font_scale=0.62,
                    top_ratio=0.080,
                    wrap_width=28,
                ),
                CaptionLine(
                    score.strip(), font_scale=1.05, top_ratio=0.125, wrap_width=14
                ),
            )
        )
    return CaptionPlan((CaptionLine(hook),))


def _description_score(description: str) -> str | None:
    match = _SCORE_DESCRIPTION_RE.search(description)
    return match.group("score") if match else None


def format_supports_demo(format_key: str, description: str) -> bool:
    """Whether a demo description has what a format needs to stay truthful."""
    if format_key == "scorecard":
        return _description_score(description) is not None
    return True


def validate_formatted_hook(
    hook: str,
    hook_format: str,
    *,
    demo_description: str | None = None,
) -> str:
    """Validate the format grammar and any presentation-specific factual claim."""
    normalized = " ".join(hook.strip().split())
    get_hook_format(hook_format)
    if any(character.isupper() for character in normalized):
        raise QualityError("formatted hooks must use lower-case copy")

    if hook_format == "pov":
        if not re.match(r"^pov:\s+\S", normalized):
            raise QualityError("POV hook must start with 'pov:'")
    elif hook_format == "quote-receipt":
        if any(character in normalized for character in "\"“”—"):
            raise QualityError("quote-receipt hook must be the quote body only")
        words = re.findall(r"\b[\w]+(?:['’][\w]+)?\b", normalized, re.UNICODE)
        if not 2 <= len(words) <= 6:
            raise QualityError("quote-receipt hook must contain two to six words")
        if len(normalized) > 23:
            raise QualityError("quote-receipt hook must fit on one short line")
    elif hook_format == "when":
        if not re.match(r"^when\s+\S", normalized):
            raise QualityError("when hook must start with 'when '")
    elif hook_format == "direct-observation":
        prohibited = ("pov:", "when ", "my face when ", "rating ", "i ")
        if normalized.lower().startswith(prohibited):
            raise QualityError("direct-observation hook uses another format's grammar")
    elif hook_format == "reaction-meme":
        if not re.match(r"^my\s+face\s+when\s+\S", normalized, re.IGNORECASE):
            raise QualityError("reaction-meme hook must start with 'my face when '")
    elif hook_format == "scorecard":
        match = _SCORECARD_RE.match(normalized)
        if not match:
            raise QualityError("scorecard hook must look like 'rating the chat: N/100'")
        supported_score = _description_score(demo_description or "")
        if supported_score is None or match.group("score") != supported_score:
            raise QualityError(
                "scorecard number must exactly match the overall score in its demo description"
            )
    elif hook_format == "inner-thought":
        if not re.match(r"^i\s+\S", normalized, re.IGNORECASE):
            raise QualityError("inner-thought hook must start with 'i '")
    score_match = _FRACTIONAL_SCORE_RE.search(normalized)
    if score_match and hook_format != "scorecard":
        supported_score = _description_score(demo_description or "")
        if supported_score is None or score_match.group("score") != supported_score:
            raise QualityError(
                "numeric score must exactly match the overall score in its demo description"
            )
    return normalized


def hook_format_cycle(run_date: date) -> tuple[HookFormat, ...]:
    """A stable daily rotation; resume never changes the intended format order."""
    start = run_date.toordinal() % len(HOOK_FORMATS)
    return HOOK_FORMATS[start:] + HOOK_FORMATS[:start]


def assign_hook_formats(
    demos: list[Demo], count: int, run_date: date
) -> list[tuple[Demo, HookFormat]]:
    """Give each selected demo a different approved caption format.

    The only constrained format is scorecard: it is selected only alongside a demo whose
    reviewed description states the precise score. The ordering is date-stable and the demo
    pick is deterministic, so a text-generation retry has the same grounding inputs.
    """
    if count < 1:
        raise ValueError("hook format count must be positive")
    if count > len(HOOK_FORMATS):
        raise QualityError(
            f"need {count} distinct hook formats but only {len(HOOK_FORMATS)} are approved"
        )
    remaining = sorted(demos, key=lambda demo: str(demo.path))
    if len(remaining) < count:
        raise QualityError(f"need at least {count} approved demos, found {len(remaining)}")

    assignments: list[tuple[Demo, HookFormat]] = []
    for ordinal, format_definition in enumerate(hook_format_cycle(run_date)):
        compatible = [
            demo
            for demo in remaining
            if format_supports_demo(format_definition.key, demo.description)
        ]
        if not compatible:
            continue
        # A date-derived pick varies the demonstration bank without creating an untracked
        # source of randomness between a quality retry and its recovery.
        index = (run_date.toordinal() + ordinal * 3) % len(compatible)
        demo = compatible[index]
        assignments.append((demo, format_definition))
        remaining.remove(demo)
        if len(assignments) == count:
            return assignments
    raise QualityError("not enough compatible demos to assign distinct hook formats")


def visible_hook_text(hook: str, hook_format: str) -> str:
    """Human-readable caption text for reviews and the Telegram summary."""
    return caption_plan(hook, hook_format).summary()

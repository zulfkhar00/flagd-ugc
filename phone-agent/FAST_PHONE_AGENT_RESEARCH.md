# Faster, cheaper phone decisions — research (2026-10-21)

## Finding

The existing Mobilerun Portal companion already exposes `state` (Android
accessibility hierarchy and phone state) over the same reverse WebSocket used by
the controller. This is the first representation to test in place of a screenshot
on every step. It does not require ADB or another Android app.

The current controller explicitly ignores the hierarchy and asks Gemini to choose
one normalized-coordinate action from each screenshot. Its repeated policy/task
context and one-decision-per-action loop are larger costs than image transfer alone.

## Measured baseline

The recent local trace
`runs/task-622ea3e63e7047b989594fc30c57a977/trace.jsonl` records a posting
run that ended as a *completion candidate*, without independent success
verification. It is used here only as a latency/cost sample:

| Measure | Value |
| --- | ---: |
| Elapsed | 199.25 s |
| Model requests | 39 |
| Acknowledged input actions | 32 |
| Screenshot observations | 38 |
| Time waiting on model responses | ~133.8 s |
| Portal screenshot time | ~18.7 s |
| Host image preparation | ~2.3 s |
| Prompt tokens | 126,711 |
| Image prompt tokens (decision calls) | 41,800 |
| Text prompt tokens (decision calls) | 83,695 |
| Candidate output tokens | 4,468 |

Gemini 3.8 Flash standard introductory pricing is $0.75 per million input tokens
and $3.75 per million output tokens through 2026-12-31. At those rates, the
logged usage is about **$0.11 for this run**. The decision images alone account
for about **$0.031**. This calculation excludes any unlogged charges or future
rate changes. Cutting screenshots while retaining the same 39 decisions therefore
cannot by itself eliminate most of the cost or latency.

## Screen representation

Use a compact, indexed list built from Portal `state`, for example:

```text
app=com.example.app  screen=720x1612  keyboard=hidden
[1] button  "Search"             bounds=598,92,688,170  clickable
[2] tab     "Profile"            bounds=615,1490,704,1595 clickable
[3] field   "Search accounts"    bounds=48,210,672,280 editable focused
```

Keep the full hierarchy in controller memory. Send the model only visible,
relevant nodes with their label/text, content description, role/class, resource
ID when helpful, bounds, and state flags (clickable, editable, focused,
selected, checked, scrollable). The model returns an element ID and operation;
the controller resolves the tap coordinate from the latest bounds. IDs are
snapshot-local and must be checked against a fresh observation before execution.
Do not let a stale ID target a different screen.

This representation is grounded in Android `AccessibilityNodeInfo` and in
Portal's documented `state` endpoint. It is smaller and gives exact element
bounds, but it can miss unlabeled icons, image/video content, custom-drawn
controls and sometimes entire app windows. Use a screenshot fallback whenever
the tree lacks the needed control, visual content matters, a layout is ambiguous,
or a high-impact confirmation cannot be established from accessible text.

## Jev

The open-source Mobile Jev agent follows an indexed-control loop: observe,
offer visible controls, choose operation/target, validate, execute, observe. Its
published Uber demo reaches payment selection in about 21 seconds / 9 actions,
which is a demonstration rather than a benchmark of our TikTok/Instagram flows.
The upstream project uses the Mobilerun cloud API and TypeSafe Jev API keys;
running it unchanged would add those services. Its decision and validation
pattern can instead be adapted to our direct Portal connection.

TypeSafe's official model page lists Jev 1.14/1.13 as **text-input** models,
priced at **$0.042 per million input tokens**, with free output tokens. Jev
returns typed choices and confidence rather than arbitrary prose. At 39 calls
with 1,000–2,000 input tokens each, its direct model bill would be roughly
$0.0016–$0.0033, *if* its choices work reliably on our screens. That excludes
any separate device/cloud fees, Gemini fallback calls, and account costs.
The vendor quotes 70–500 ms model response times; this needs measurement from
our host and with our actual choice sets. Jev cannot write a novel caption or
comment; exact supplied text can be copied by code, while genuinely new text
needs a generative model.

## Experiment order

As of 2026-10-22, steps 1 and the opt-in controller portion of step 3 are
implemented in `semantic.py`, `GeminiVision.decide_hybrid`, and `run_agent`.
The pilot leaves screenshot capture and visual verification in place and is
disabled by default. Unit tests exercise redaction, coordinate mapping,
visual fallback and feature gating. It has **not yet been benchmarked on live
TikTok/Instagram screens**. Run `scripts/compare_perception.py` on a baseline
and a pilot trace after the pending queue is clear. Jev and decision-count
reduction remain research options, not implemented capabilities.

On October 22 a paid synthetic-screen smoke test used the same
`gemini-3.1-flash-lite` model and the same three-button task through both
paths. Text-first returned a tap in **1.43 s**, using **359 prompt tokens**
and **0 image tokens**. Screenshot-based returned a tap in **3.50 s**, using
**2,161 prompt tokens**, including **1,100 image tokens**. A separate text-first
call mapped its selected `Search` element to the expected device coordinates.
These are single-call smoke tests on a drawn interface, not a claim about
TikTok/Instagram accuracy or month-long savings. The phone was disconnected
from the running local service during the live-app test attempt.

1. **Instrument the current path more precisely.** Record state-fetch time,
   serialized state bytes, compact-state token count, screenshot timing, model
   timing, number of fallback calls, executed actions and end-to-end outcome.
2. **Read Portal `state` in parallel with a screenshot on representative screens**
   (TikTok feed/search/post composer, Instagram feed/Reel composer, Settings and
   an unfamiliar app). Inspect the hierarchy locally without sending it to a
   model. Determine which target controls and verification evidence are present.
3. **Pilot text-first Gemini.** Replace the long vision prompt on ordinary
   navigation steps with a short indexed-control prompt and `tap_element(id)`;
   retain screenshot-Gemini fallback. The current 3.8 Flash model is a useful
   control because this isolates representation from model choice.
4. **Test Jev as a decision engine** for the same compact states. Compare its
   decision quality, confidence calibration, latency and cost with text-only
   Gemini. Keep exact-text typing, public-action limits and post-action checks
   in the controller, independent of either model.
5. **Reduce decision count.** Reuse validated short action sequences only while
   fresh state checks match expected conditions; stop and replan on divergence.
   Use bounded state polling after taps instead of an unconditional screenshot.
   One model call should be able to choose among current controls without
   repeating the entire task policy and history each time.

Gemini implicit caching is enabled automatically, but Google's current documented
minimum for 3.8 Flash is 4,096 input tokens. Ordinary decision calls in the
sample run are around 3,000–3,400 tokens, so implicit caching is unlikely to
remove much of this repeated context. Explicit caching can be investigated if
a large static prefix remains after prompt compaction; its storage charge and
request shape need to be included in the comparison.

Success criteria: lower wall-clock time and model spend **at equal or better
task completion and posting correctness**, not merely a lower image token count.
Unknown screens, accessible-tree gaps and public side effects must still route
through fresh evidence and the existing authorization checks.

## Primary sources

- [Portal local API / reverse transport](https://github.com/droidrun/mobilerun-portal/blob/main/docs/local-api.md)
- [Android AccessibilityNodeInfo](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
- [Mobile Jev implementation and demo](https://github.com/droidrun/mobile-jev)
- [TypeSafe Jev models and pricing](https://docs.typesafe.ai/models)
- [TypeSafe Jev system-one design and latency claims](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [DailyDroid screen-text versus screenshot study](https://arxiv.org/abs/2604.17817)

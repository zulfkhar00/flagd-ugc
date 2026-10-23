# General visual phone agent prototype

The product is a [general visual phone assistant](GENERAL_PHONE_AGENT.md)
controlled by natural-language instructions across apps. This controller is its
first component, with planning, generated searches and a persistent research
notebook. App exploration and long sessions still need real-device work.
The [flagd workflow requirements](FLAGD_WORKFLOWS.md) cover topic research,
native slideshows, music selection, exact copy and publication verification.

This is a separate one-phone prototype. It is not wired into the UGC daemon or
deployed to the VPS. Its first milestone is seeing an owned Android phone and
navigating it from screenshots. No model training is required.

```text
Android + Mobilerun Portal
    → outbound WebSocket to our controller
    → short task plan and initial search ideas
    → screenshot, resized for vision
    → Gemini chooses one visible tap/swipe/text/wait
    → validate and convert coordinates to the original screen
    → Portal dispatches the input
    → fresh screenshot before the next decision
    → saved findings and a local report with screenshot references
```

The agent does not request UI trees, installed-app inventories, shell commands,
deep links or private TikTok/Instagram APIs. Portal uses Android Accessibility to
execute the physical UI inputs. No ADB or Mobilerun Cloud account is needed at
runtime. This prototype uses the companion app without modifying or copying its
AGPL implementation; our controller speaks its documented protocol.

## What is implemented

- Authenticated reverse connection, optionally pinned to one device ID.
- Screenshot capture with original pixels retained and a 1024-pixel model view.
- Direct plain-language instructions through `run --goal`, with task JSON also
  available for structured workflows and exact text fields.
- One structured vision decision per step; Gemini uses explicit 0–1000 normalized
  coordinates, converted to screenshot pixels and then original device pixels.
  Invalid coordinates are rejected rather than guessed or clamped. Short history rather than an entire
  screenshot conversation replayed every time.
- An initial plan with subgoals and observable success criteria. Each screen
  assessment updates progress and a compact session summary.
- Visible tap, swipe, keyboard Enter for an active search editor, exact supplied text replacement, generated search queries
  and short waits. Generated typing is reserved for visibly focused search fields.
- Preview mode makes one planning request and one screen assessment, with no input.
- An explicit `--execute` mode runs bounded navigation.
- Fresh observation after each input, confidence/step limits and repeated-input
  detection on an unchanged screenshot.
- Local intent/acknowledgement journal, screenshots and reported token usage.
- SQLite task memory containing visited sources, deduplicated findings and their
  captured observations. The model sees at most the last 20 findings and 30 sources.
- Automatically written Markdown and JSON reports, including partial findings
  when an input, connection or model response fails. Answers cite recorded finding IDs.
- An offline `report` command rebuilds reports without AI requests or phone inputs.
- Uncertain inputs stop the task; no automatic task resumption or input replay.
- An invalid assessment may receive one correction after a fresh screenshot,
  within the same decision limit. Rejected inputs/findings are not executed or
  stored, and all returned token usage is counted. Transport/uncertain-input
  errors remain terminal.
- Missing authorization/media checkpoints are sent back as feedback to the
  vision agent, with a fresh screenshot and at most two recovery decisions.
  The agent chooses the correction; the controller never scripts app navigation
  or relaxes publication requirements. Rejected public taps are not dispatched.
- Unfamiliar layouts and ordinary popups do not automatically trigger a handoff;
  the model can inspect visible controls and choose a different route. Whether
  it successfully resolves an unfamiliar screen still needs live evaluation.
- A repeatedly unchanged screen returns navigation feedback to the vision
  agent before stopping. It may choose another visible route or obtain missing
  source content. Repeating the same stalled screen/input after that feedback
  still stops, and the ordinary task decision limit remains in place.
- An unflagged handoff during execution gets one reconsideration from the same
  agent on a fresh screen, so an informational notice or unfinished editor step
  can be resolved without manual intervention. Explicit review requests are
  preserved, and repeated handoff still stops without guessing a phone input.
- In `--keep-connected` mode, task failures leave the listener alive and the
  paired phone can reconnect. Reconnection never resumes or replays task inputs.
- Model-declared completion is recorded as a **completion candidate**, not
  independently verified success.
- Tasks can declare a required publication format and music. A `confirm_media`
  observation must match that format and report an applied, enabled track before
  publishing, even when extra carousel checks are waived. Instagram slide-based
  posts now request `media_format: "reel"` and `require_music: true`. This is a
  model reading of visible controls; it is not independent audio verification.

## Initial real-device checks — October 12, 2026

An owned Android phone completed authenticated reverse connection and captured
720 × 1612 screenshots. Portal reported its screen-awake watchdog active, the
display interactive and the device unlocked. The phone keyboard was selected.
Persistent connection mode stayed available after task failure and completion.

Visual navigation reached Android Settings → Display. The initial attempt hit
its step limit after some missed navigation targets; a continuation reached the
brightness screen but exhausted the model's response budget. After correcting
pending-progress validation and lowering Gemini 3.8 Flash reasoning, a read-only
run recorded Brightness level **58%** and completed with three model requests
and no gesture/text inputs. The captured screenshot was also visually inspected
by the operator assistant. This validates this small task, not arbitrary app
navigation or reliability across devices. The generic result remains a
completion candidate until task-specific verification is added.

Private local artifacts are in `phone-agent/runs/20261013T005419Z-7dd7acb3/`;
earlier failed attempts remain alongside them for debugging. The controller's
51 offline checks include reconnection after model failure without task replay,
wake-status verification, and requiring evidence for completed progress.

A subsequent browser test exposed Gemini's normalized coordinate convention,
an unfocused typing attempt and failed suggestion-based search submission. The
adapter now explicitly converts 0–1000 coordinates, and an active search can
submit with keyboard Enter. Google then presented a robot challenge; after the
operator explicitly authorized visible challenge completion, the agent selected
the image tiles and reached search results. A final continuation opened a travel
article and saved three destinations with cited screenshot evidence. It took
38.42 seconds and seven model requests (24,456 reported tokens). A separate
operator-assistant visual review confirmed the small task; automatic verification
remains unimplemented. The 64 offline checks passed. Detailed local results,
including failed attempts and token-cost estimates, are recorded at
`phone-agent/runs/20261013T011406Z-19302707/verification.md`.

The reusable local controller was then tested on the same physical phone.
Four instructions used one persistent service connection, including cancellation
and a successful subsequent read. Repeating an earlier request ID returned its
existing result without more inference. One scrolling task reached its limit
while looking for numbered headings in unnumbered sections; partial findings
were retained and the connection stayed open. All 83 offline checks passed.
Private live results are recorded at
`phone-agent/runs/controller-verification-2026-10-12.md`.

After the operator installed TikTok and signed into the existing promotional
accounts, the agent switched to try.flagd, searched a social-confidence topic,
and inspected five posts' visible text and comment panels. The operator assistant
reviewed the captured comments. A follow-up profile check confirmed @try.flagd.
The research job used 40 model requests in about five minutes. This validates
basic TikTok account switching, searching, opening posts and comment reading on
this phone; it does not validate full video/audio understanding, publishing,
Instagram or automated account/citation verification. Some source labels were
display names rather than independently checked handles. Local reviewed results
are at `phone-agent/runs/task-8bdc3b15af2842198f6f1d08395cf84f/verification.md`.

## Set up the prototype

From the parent repository:

```bash
uv sync --project phone-agent --extra dev --python 3.12
cp phone-agent/.env.example phone-agent/.env
```

Generate a random token, copy it into `PHONE_DEVICE_TOKEN` in that file, and use
the same token in Portal. The following command only prints a new local token:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Supply `PHONE_GEMINI_API_KEY` and a currently available image-input Gemini model
as `PHONE_VISION_MODEL`. Optionally set `PHONE_BROWSE_MODEL` to a tested cheaper
image-input model (for example, `gemini-3.1-flash-lite`). Timed browsing uses that
model; publishing and general tasks retain the main model by default. An invalid,
low-confidence, review-requesting, public-input or handoff decision gets one main-model
fallback on the same pixels before any input is sent. Both requests are counted.
Set `PHONE_NAV_MODEL` to a tested fast image-input model to use the same
fallback for general tasks, such as switching between already logged-in
accounts. Publishing continues to use `PHONE_VISION_MODEL` for every
decision. On the October 15 TikTok trace, the main model took a median 8.1
seconds per account-switch decision, versus 2.4 seconds for browsing decisions
on `gemini-3.1-flash-lite`; actual latency and correctness must be checked on
the next live navigation task.
These settings do not affect the UGC models. `inspect`
does not require a Gemini key and makes no AI request. `run` makes paid Gemini
requests, including in preview mode; the default ceiling is 21 requests per run
(one plan plus up to 20 screen assessments). `--max-steps` changes the screen
assessment limit. No separate model request is made to render the report.
Offline checks make no inference requests. Initial live inference and phone
results are summarized above; token usage is retained in each local run trace.

Browsing and publishing use separate short prompts and three recent actions.
Long exact fields are held by the controller and summarized in requests when
extra preview checks are waived; `type_text` still inserts their complete values.

### Experimental text-first navigation

`PHONE_SEMANTIC_NAV=1` enables a pilot that reads Portal's visible Android
accessibility state over the existing reverse WebSocket connection. On an
ordinary navigation step, Gemini receives a compact list of labeled screen
elements and can choose a low-risk element or scroll container. It does not
receive an image for a successful text-only choice. If the tree is missing,
ambiguous, low-confidence or points at a public action such as Post, the
controller uses its existing screenshot decision. Account, media, music,
publication and completion checks remain visual. No ADB connection is needed.

`PHONE_SEMANTIC_MODEL` can select a tested cheaper Gemini text model. If unset,
the pilot uses `PHONE_BROWSE_MODEL`, then `PHONE_NAV_MODEL`, then the main
`PHONE_VISION_MODEL`. The feature is **off by default** and applies to compact
browse/publish decision loops; standard planned research tasks still use their
existing visual assessments. Each step still captures a screenshot for the
evidence journal, stale-screen check and fallback, so this pilot targets model
image tokens and decision latency, not screenshot-capture time.

### Experimental guarded continuations

`PHONE_GUARDED_CONTINUATIONS=1` lets a visual decision propose one extra,
routine navigation step. After the first input, the controller captures a new
screenshot and accessibility state. It executes the predicted step only when a
unique safe navigation label or focused search editor matches in the same app;
otherwise it asks the visual agent again. It can also enter an exact supplied
title, description, caption or URL after tapping an empty editor, but only if a
unique focused field is identified by its accessibility hint or resource ID.
This never follows up with a public action. Publication tasks require an
explicit `"guarded_continuations": true` in that task; the environment flag
alone does not opt them in. This feature is independent of the text-first pilot
above. It aims to remove a model round trip, not to remove screen observation.
Compare elapsed time, model calls and result quality on the same bounded task
before enabling it for regular work.
For one benchmark without restarting the phone service, add
`"guarded_continuations": true` to a structured task JSON; the service default
can remain off.

Compare an existing run and a pilot run with:

```bash
python3 phone-agent/scripts/compare_perception.py phone-agent/runs/BASELINE phone-agent/runs/PILOT
```

The comparison reports semantic decisions, visual fallbacks, model requests,
image tokens and state/screenshot timings. Compare completion and posting
correctness separately; lower token use is not a successful pilot by itself.
Tasks requiring full caption comparison retain the full text. Publication limits,
account/media checkpoints and uncertain-input protection remain enforced.
Loading waits can last up to ten seconds without another model request; playback
waits last up to sixty seconds. Browsing prefers playback and only occasional
discussion visits. Per-model calls, input/output/thinking and cached-token counts
are retained in the report so fallback costs remain visible.

Executed tasks reconsider review flags, handoffs and uncertain targets using a
fresh screenshot. Successful inputs reset that recovery allowance, so separate
popups do not exhaust a single lifetime retry. A persistent missing prerequisite
is reported as `blocked`; denied inputs are never sent. Navigation recovery
recognizes small coordinate changes and animated pixels outside the tapped
control, then asks the agent to choose a different route. The agent can select
`repeat: 2` for a horizontal edge-back swipe when the screen requires a prompt
double-back gesture. Only that navigation gesture can repeat; public taps cannot.

For executed publishing jobs, `confirm_account` receives an independent visual
assessment of the same screenshot. Feed authors, commenters, watermarks and shared
avatars cannot establish the logged-in account. A named different destination in
the publication result is recorded as `misdirected_publication`.
The owner-only control operation `reconcile_publication` can assess the saved
success screenshot of a terminal job with an acknowledged Post input. It records
an explicit named recipient and retains the original task and Post receipt. This
allows duplicate protection to follow a proven actual recipient; it never resets
an action allowance or repeats a phone input. Unnamed or uncertain outcomes leave
the existing duplicate protection in place.
Temporary HTTP 429/5xx inference failures get at most two read-only retries with
short backoff. HTTP status and every provider attempt are retained in usage data;
response bodies and credentials are not logged.

Load the local configuration into the shell (the CLI does not implicitly read
the UGC service's `.env`):

```bash
set -a
source phone-agent/.env
set +a
```

On the Android phone:

1. Install a release of [Mobilerun Portal](https://github.com/droidrun/mobilerun-portal/releases).
2. Enable its Accessibility service and the permissions it requests for capture.
3. For text input, select Mobilerun Keyboard using Android Settings.
4. In Portal Settings → Reverse Connection, set our host URL and token, then
   enable Connect to Host. Do not use the Connect to Mobilerun Cloud button.
5. For a first connection, keep the phone unlocked. Set
   `PHONE_KEEP_SCREEN_AWAKE=1` to enable Portal's screen-awake watchdog on
   connection. The controller checks live awake status before screenshots or
   AI requests. This setting persists on the phone until disabled in Portal
   Settings → Keep Screen Awake. Secure locks still need operator unlocking.
   Background operation and restart recovery need real-device testing.

For a same-Wi-Fi test, start the listener on the Mac:

```bash
uv run --project phone-agent flagd-phone inspect --bind 0.0.0.0 --keep-connected
```

Set Portal's host to `ws://<MAC_LAN_IP>:8787/portal`. Use this plaintext connection
only on a trusted local network. The command prints the phone's device ID and
stores a screenshot. Set `PHONE_DEVICE_ID` to that ID for future runs.
Screenshot replies currently contain full-resolution PNGs. The controller records
`portal_screenshot_ms` (phone capture, PNG encoding and transfer) and
`host_prepare_ms` (local decode and model-sized JPEG conversion) on each
observation; `capture` also returns both timings. Portal normally hides its
overlay before capturing, which adds about 100 ms. If the overlay is switched
off and test captures contain no floating controls, set
`PHONE_SCREENSHOT_HIDE_OVERLAY=0` to skip that delay. Keep the default when the
overlay is in use. This option does not change image quality or resolution.
With `--keep-connected`, the listener then holds the connection open without AI
requests or phone inputs. The same flag works on `run` after task completion.
Task errors and phone disconnects leave this listener available for the paired
phone to reconnect; they do not trigger new task execution. Ctrl+C ends it.
Without that flag, inspection exits immediately after capture, which causes
Portal to start reconnecting. Stop the inspection before starting an agent run.
The `--keep-screen-awake` flag enables the watchdog for an individual command;
`--no-keep-screen-awake` skips enabling it but does not disable an already active
phone watchdog. Keeping the display awake requires Portal to remain running and
reachable; it cannot revive a phone that is powered off or an app Android has stopped.

The separate [Dokploy deployment guide](DEPLOY_DOKPLOY.md) packages this
listener for a public `wss://<DOMAIN>/portal` endpoint with TLS at Dokploy's
proxy. The private Unix control socket and saved runs stay inside its
persistent volume. A public endpoint is not provisioned merely by adding the
Compose file; the Dokploy domain, secrets and phone URL must also be set.

## Submit multiple instructions to one phone connection

Load the configuration once in the service terminal, then start:

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli serve --bind 0.0.0.0
```

Portal keeps its existing `/portal` URL and device token. The service remains
listening across task completion, failure and phone reconnection. It makes no
AI requests while idle. Screen-awake setup is checked before each task when
`PHONE_KEEP_SCREEN_AWAKE=1` is configured.

Set `PHONE_PARK_ON_FINISH=1` (or `serve --park-on-finish`) to return Home,
disable Portal's wake watchdog and lock the display after a successful executed
task or a completed timed browsing session. Home is sent before disabling the
watchdog, since releasing its wake lock can immediately put the display to sleep.
The next task re-enables the wake
watchdog when `PHONE_KEEP_SCREEN_AWAKE=1`. Cleanup uses the documented Portal
Home/lock controls without AI requests, ADB, app packages or UI trees. It leaves
the app in the background rather than force-stopping its process. Failed tasks,
review handoffs and uncertain inputs keep their screen for recovery; preview
tasks do not run cleanup. The task receipt records cleanup separately so a
display problem cannot cause a published slideshow to be retried. One phone
owner is retained until cleanup finishes. Run
`phone-agent/.venv/bin/python -m flagd_phone.cli park` to return
an idle phone Home and lock it immediately.

From another terminal in the parent project, send a natural-language instruction.
The client needs no Gemini key or device token:

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli submit \
  --goal "Read the title of the article currently open in Chrome. Leave the page unchanged." \
  --execute --max-steps 8 --wait
```

Each accepted task has a task ID, submission request ID, its own private run
directory and a saved result/report. `--wait` polls the service without AI
requests; a wait timeout leaves the task running. Omit `--execute` for a
single-assessment preview. Existing `--task`, `--text` and explicitly authorized
`--allow-robot-verification` options work on `submit` as well.

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli status
phone-agent/.venv/bin/python -m flagd_phone.cli status --job TASK_ID
phone-agent/.venv/bin/python -m flagd_phone.cli cancel TASK_ID
phone-agent/.venv/bin/python -m flagd_phone.cli capture
```

Only one task or manual capture owns the phone at a time. A busy/offline phone
rejects new tasks instead of queueing them for later execution. Cancelling a task
keeps the phone connection open. `capture` saves a screenshot with no AI request.
The original one-shot `run`/`inspect` commands remain available but cannot share
the listener port with `serve`.

The control channel is an owner-only Unix socket at `phone-agent/.control.sock`;
it is not exposed on the LAN. Both service and clients support `--control-socket`
or `PHONE_CONTROL_SOCKET` for a different local path. Saved submission receipts
are in `phone-agent/runs/controller-jobs.json` with private file permissions.
Reusing `submit --request-id UUID` with the same instruction returns its existing
task, including after a service restart; it never replays the task. A different
instruction with that ID is rejected. The client does not automatically retry a
lost submission reply. Interrupted tasks become `interrupted` on restart.

For work that should wait through a busy/offline phone or a temporary Gemini
outage, use the durable serial queue instead of `submit`:

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli enqueue \
  --task prepared-post.json --execute --request-id YOUR-STABLE-UUID
phone-agent/.venv/bin/python -m flagd_phone.cli queue-status
```

Queue entries are saved before execution and processed in order by the phone
service after its process restarts. Read-only Gemini requests make up to
three attempts with a 75-second per-attempt cap. A terminal provider/network error
is retried from a fresh screenshot with increasing delay (up to 30 minutes)
only when the saved receipt proves zero public actions and no uncertain input.
Phone disconnection leaves pending work queued until reconnection. The queue
blocks at an attempted publication, uncertain input, interrupted in-flight job,
or other result needing inspection; it never repeats a Share/Post tap. Inspect
the saved job ID and use `queue-status ITEM_ID` to see the reason. `queue-cancel
ITEM_ID` cancels pending work. A service process must be running for queued work
to execute; the durable file is `runs/controller-jobs.json`.

After inspecting an uncertain blocked publication, `queue-skip-blocked ITEM_ID
--reason "..."` preserves that item and its original job receipt as `skipped` while
allowing later, distinct queue items to proceed. It does not claim that the
uncertain post succeeded or retry its Share/Post tap.

Automatic verification and queue continuation after a post succeeds but the
phone disconnects before confirmation is tracked in [TODO.md](TODO.md).

On this Mac, the service is installed as the user LaunchAgent
`~/Library/LaunchAgents/com.flagd.phoneagent.plist` from
`phone-agent/launchd/com.flagd.phoneagent.plist`. `RunAtLoad` and `KeepAlive`
restart the listener after login or a crash. The phone's Portal connection is
separate: if Android does not reconnect after a listener restart, queued work
waits without spending model calls or attempting a post.

After uncertain input or an interrupted task, inspect the screenshot returned by
`capture`, then acknowledge that fresh frame's ID before submitting a new task:

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli acknowledge FRAME_ID
```

Acknowledgement clears the recovery hold; it does not resume the old task.
These controls are a local CLI. An authenticated instruction API and optional
California browsing scheduler are described below; there is no chat/web dashboard.

## Instruction service and owned-account workflows

The main input stays a text instruction. A separate scheduling/content service
decides when to send it, which account it concerns, the allowed public actions,
and the exact prepared copy. The visual executor sees the screen and performs
one action at a time; Portal carries screenshots and ordinary inputs wirelessly.

Start the already paired phone listener with `serve`. Start the optional API in
another process with the same ignored `.env` loaded:

```bash
phone-agent/.venv/bin/python -m flagd_phone.cli gateway
```

`PHONE_CONTROL_TOKEN` must be a separate secret of at least 32 characters. The
API listens on `127.0.0.1:8788`, requires `Authorization: Bearer TOKEN`, and exposes:

- `POST /tasks`: `instruction`, optional `options`, `execute`, and a stable
  UUID `request_id` generated by the caller before delivery.
- `GET /status` and `GET /tasks/TASK_ID`: connectivity and saved job results.
- `POST /tasks/TASK_ID/cancel`: stop that job while keeping the phone connected.

The external service can retry delivery using the same request ID to retrieve
the receipt, not execute the instruction twice. Busy/offline responses create no
job: the caller chooses whether to try later. Interrupted phone actions are not
replayed. Put the loopback endpoint behind a trusted HTTPS tunnel if the calling
service runs on the VPS; this prototype has not been deployed there.

Example submission body:

```json
{
  "request_id": "e8d8d05e-3ea8-4e38-932e-73c6b96a419c",
  "instruction": "On TikTok @try.flagd, verify the account, search conversation starters, watch posts and browse their comment threads for 15 minutes. No research report needed.",
  "execute": true,
  "options": {
    "mode": "browse",
    "duration_seconds": 900,
    "max_steps": 300,
    "authorization": {
      "app": "TikTok",
      "account": "try.flagd",
      "limits": {"like": 0, "comment": 0}
    }
  }
}
```

These options give the service an enforceable timer and permissions; they do
not replace the instruction. Default plain instructions still use the general
agent. API delivery defaults to preview unless `execute` is explicitly true.
The existing CLI can use the API with `--api-url http://127.0.0.1:8788` on
`submit`, `status` or `cancel` after loading `PHONE_CONTROL_TOKEN`.

`make-browse-task` emits a task without running it. During timed browsing, the
model can choose a single swipe with a 1–15 second playback target; time already
spent deciding from that screenshot counts toward the target. This usually keeps
short videos to about 4–10 seconds without a separate model request to swipe.
The browsing deadline starts after a visible account checkpoint.
Comment panels and reply threads use the normal screenshot decisions. Optional
likes/comments need per-job limits; comments need readable current context and
must not invent personal experiences or endorsements. The model labels public
taps, and the runtime enforces those declared limits. This is not an independent
deterministic classifier of every screen control.

`make-slideshow-task --source SOURCE.json` emits a Volume phone-share workflow.
The source supplies `account`, `volume_id`, `share_url`, `caption`, `slide_count`.
Its first caption paragraph becomes the title (59 characters maximum); remaining
paragraphs become the description unchanged. Overlength titles fail validation
instead of being silently truncated. The agent uses the phone share sheet,
checks the native carousel/order, selects suggested music and enters both fields.
`--publish` authorizes one Post tap for that source/account. Preview and selected
account checkpoints precede it. A second publication job for the same source and
account is rejected even with a different delivery ID. Inspect an uncertain or
failed receipt before deciding on recovery; the prototype never retries Post.

After inspecting a stopped publication, an explicit continuation can declare
`resume_from_job_id` for the latest job of that source/account. It is accepted
only when all earlier jobs are terminal and their saved receipts report zero
Post attempts. Any attempted Post, missing receipt, uncertain input or interrupted
job keeps the duplicate block in place. This continues preparation; it never
automatically repeats a publication gesture.

For black-capture diagnostics, `capture --show-overlay` includes the Portal
overlay and returns live screen-awake status. It does not read a UI tree or
change the account. Awake/unlocked status alone does not establish usable pixels.

An optional local schedule is available with `serve --schedule CONFIG.json`.
Check it using `schedule-preview --config CONFIG.json`. The example
`schedule.example.json` is **disabled** and assumes 15 minutes morning plus
15 minutes evening per account, on TikTok. Times use California daylight saving.
Tasks share one phone serially. Slots can wait up to an hour for earlier jobs;
expired slots are skipped. Restarting the service does not catch up missed slots.
An external scheduler can use the instruction API instead of this local helper.
Both approaches require the host and phone connection to remain available.

Caption parsing, scheduling, authorization and API behavior have offline tests.
Native TikTok publishing, suggested music and live public engagement still need
real-device validation; a model completion alone is not proof of publication.

Preview a general phone-navigation instruction:

```bash
uv run --project phone-agent flagd-phone run \
  --goal "Open Settings and find the display brightness control. Leave its value unchanged." \
  --bind 0.0.0.0
```

Run the same bounded navigation with inputs enabled:

```bash
uv run --project phone-agent flagd-phone run \
  --goal "Open Settings and find the display brightness control. Leave its value unchanged." \
  --bind 0.0.0.0 --execute
```

Supply exact text for a search or form without constructing a task file:

```bash
uv run --project phone-agent flagd-phone run \
  --goal "Open the browser and search using the supplied query." \
  --text "query=places to visit in Thailand" --max-steps 20 \
  --bind 0.0.0.0
```

For research, the agent can formulate queries itself:

```bash
uv run --project phone-agent flagd-phone run \
  --goal "Use the browser to find three places to visit in Thailand. Read relevant pages and report the observed recommendations with sources. Search and read only." \
  --max-steps 30 --bind 0.0.0.0 --execute
```

These commands illustrate the one-shot entry point. Small Settings and browser
research tasks have been exercised on a physical phone, with the dated limits
above. Structured jobs can use `--task` instead of `--goal`.

Each run writes `memory.sqlite3`, `trace.jsonl`, screenshots, `report.md` and
`report.json` in the printed artifact directory. Reports identify the observed
page/account and captured screenshot; a source URL is included only when the
model transcribes that URL from visible screen text. The transcription and
conclusions are model assessments, not independently verified facts.

Rebuild a report from saved memory after a run or interruption:

```bash
uv run --project phone-agent flagd-phone report \
  --run-dir phone-agent/runs/<RUN_DIRECTORY>
```

Opening memory does not resume the task or replay gestures. A process killed
before report creation can still leave committed findings available for this
command; its saved status may remain `running`.

Each one-shot command is one run and exits afterward, except that `--keep-connected`
keeps the listener available until stopped, including after task failure or a
phone disconnect. Portal can reconnect without restarting task inputs.
One-shot connection setup times out after 120 seconds by default.
Screenshots and the journal are private local files under `phone-agent/runs/`.
They may contain personal screen data; they are excluded from Git. Task memory
includes supplied exact text. Traces include generated queries and model
descriptions, which may reproduce visible personal text. API/device tokens are
not deliberately logged.

## Task format

```json
{
  "goal": "Navigate to the TikTok carousel review screen using the six images already available on the phone. Stop before publishing.",
  "text_fields": {
    "title": "The meeting ended before you found a gap to speak",
    "description": "The exact prepared long description goes here."
  },
  "text_limits": {"title": 59},
  "max_steps": 30
}
```

This illustrates the task interface, not a completed carousel integration.
This TikTok workflow explicitly declares the user's 59-character title limit.
The generic controller does not impose TikTok limits on fields in other apps.
Form typing selects a supplied field verbatim; the model cannot invent its value.
Search typing uses a separate `type_query` action, bounded to 200 characters and
validated separately from exact fields. Whether a focused control is a search
field is assessed by the vision model, not a deterministic UI classifier.

Without scoped job authorization the policy requests review before public posting,
messaging, engagement or account changes. This is a model policy, not a guarantee that a vision model
can never misidentify a button. Authentication and identity verification are
handed back to the operator. Ordinary robot checks also stop by default; when
the operator explicitly requests completion through the visible interface,
`--allow-robot-verification` records that authorization for this task only.
It allows the usual checkbox/image-puzzle interaction, not a non-UI bypass.
This flag does not grant permission for account changes, posting or engagement.
Start with inspection and preview.

## Next milestones

1. On a real phone, verify capture/input alignment and ordinary navigation;
   keep failed attempts and measure successful-task tokens and latency.
2. Exercise the implemented planning, search, memory and reporting loop in
   Settings, a browser and Maps. Check the actual screenshots against reported findings.
3. Test an unfamiliar app's installation and onboarding through its visible UI.
4. Add app-specific workflows, including native TikTok carousels and Instagram.
   TikTok image transfer and picker indexing still require real-device tests;
   Portal's file APIs require Android 11+.
5. Add durable scheduled jobs, per-phone ownership, task-specific success
   verification, duplicate prevention and manual recovery.

Publishing, native carousel handling, email-code retrieval, analytics,
multi-account scheduling and multi-phone recovery have not been validated.
Human navigation is an interaction style, not an assurance about account reach
or immunity to platform enforcement.

## Offline checks

```bash
uv run --project phone-agent --extra dev pytest phone-agent/tests
```

Tests cover real WebSocket framing against a simulated Portal, authentication,
coordinate conversion, uncertain-input handling, vision request construction,
preview execution, fresh-observation ordering, generated-query typing, persistent
findings, citation validation and report recovery after failure. Research tests
use scripted model responses, not live visual comprehension. They require no
API key or phone and do not prove live TikTok performance.

## Research sources

- [Portal reverse connection](https://github.com/droidrun/mobilerun-portal/blob/main/docs/reverse-connection.md)
- [Portal control methods](https://github.com/droidrun/mobilerun-portal/blob/main/docs/local-api.md)
- Portal protocol source inspected at commit `cc3fa91b15847ca9f7cc9a5afdd6b8e1db7e4ec2`:
  `ReverseConnectionService.kt`, `ActionDispatcher.kt`, `ApiResponse.kt`, `ApiHandler.kt`.
- [Mobilerun framework](https://github.com/droidrun/mobilerun): heavier existing
  agent framework; inspected as a reference rather than a runtime dependency.
- [Mobile Jev](https://github.com/droidrun/mobile-jev): useful recovery and
  measurement patterns; its current agent chooses indexed UI controls rather
  than interpreting screenshot images. A later cost experiment.
- [Gemini generateContent contract](https://ai.google.dev/api/generate-content)
- [WebSocket server contract](https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html)

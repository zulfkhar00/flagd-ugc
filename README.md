# flagd UGC pipeline

A private, resumable Python 3.12 service that creates four 15-second vertical flagd videos
every day and delivers them to one Telegram chat. It does not post to social platforms and
does not collect analytics.

The daemon starts work at 08:00 `America/Los_Angeles`. Four videos are generated sequentially to
avoid bursts through managed HTTPS proxies and can be reviewed in Telegram later that morning.

## Video format

Each result contains:

- 0–3 seconds: an approved synthetic character in a natural, handheld front-camera opening,
  giving one strong, instantly readable silent facial reaction beneath a POV, "when", "my face when",
  inner-thought or plain reaction text overlay. The caption and matching emotion are planned
  together; the reaction, setting and wardrobe are persisted before image/video generation;
- 3–15 seconds: the matching approved flagd demonstration;
- no audio, so the uploader can select platform-native sound manually.

FFmpeg loops short visual inputs as needed. Every result must pass an `ffprobe` check for
1080×1920, H.264, 30 FPS, no audio stream, and 15 seconds (±0.25 seconds) before delivery.

## Persistent data

Mount a persistent volume at `/data` and provision this structure:

```text
/data/
  characters/          approved PNG/JPEG/WebP character references
  demos/               8–12 approved vertical demonstrations + manifest.json
  db/pipeline.sqlite3  created automatically
  renders/YYYY-MM-DD/
    intermediate/      retained for 7 days
    final/             retained for 30 days
  character-samples/   generated verification pack and manual approval
```

All non-hidden supported files in `characters` are treated as approved references. A demo
manifest should describe exactly what each clip truthfully demonstrates:

```json
{
  "demos": [
    {
      "file": "share-availability.mp4",
      "description": "A user selects availability and shares it with friends.",
      "approved": true
    }
  ]
}
```

An omitted manifest is supported for local testing, but filenames then become the only
grounding descriptions. A reviewed manifest is strongly recommended for production hooks.

### Preparing iPhone demo recordings

The repository includes a reviewed preparation plan for the initial flagd demo bank. It
imports the untouched iPhone recordings into `data/demos/raw`, selects the useful moments,
holds short result screens long enough to read, and writes eight approved silent clips plus
`manifest.json`:

```bash
flagd-ugc prepare-demos \
  --plan demo-bank/recipes.json \
  --source-dir ~/Downloads \
  --output-dir ./data/demos
```

Prepared clips are exactly 12 seconds, 1080×1920, H.264 and 30 FPS. The full tall iPhone
screen is preserved with dark side padding so the app header and bottom navigation are not
cropped. The raw recordings are copied without modification and are not treated as selectable
demos by the daily pipeline.

### Opening-hook text treatment

The first three seconds use a centred top-safe-area TikTok Classic caption: two short
lowercase lines in white bold text with a black outline and subtle shadow. The text has no
banner, background card or branding, and disappears when the app demonstration begins.

For a no-cost, silent end-to-end review using an existing opening and approved demo:

```bash
flagd-ugc compose-lab \
  --opening ./opening.mp4 \
  --demo ./data/demos/03-negative-date-feedback.mp4 \
  --hook "i'm good at texting" \
  --hook-format quote-receipt \
  --output ./compose-lab-output/prototype.mp4 \
  --open
```

The approved rotation, copy grammars, score guardrail and caption layouts are documented in
[HOOK_FORMATS.md](HOOK_FORMATS.md). `compose-lab --hook-format` renders any one of those layouts
without paid providers or delivery.

The daily pipeline uses the expressive reaction recipes and date-rotated settings in
[OPENING_DIRECTIONS.md](OPENING_DIRECTIONS.md). The opening has one silent facial beat that
matches the overlay and subsequent demo, with ordinary blinking, body settling and handheld
wrist drift. The animation review checks six frames for the intended reaction and defects.

### Local four-video batch preview

After approving individual openings and caption styles, render a complete silent daily-pack
preview without Gemini, Kling, SQLite, Telegram, or deployment:

```bash
flagd-ugc batch-preview-lab \
  --plan ./batch-preview/approved-v1.json \
  --output-dir ./batch-preview-output/approved-v1 \
  --open
```

The plan contains four distinct approved formats and demos. Its media paths are relative to
`asset_root`, which is set to the repository root in the provided plan. The command validates
every source, hook grammar, demo pairing, output codec, dimensions, frame rate, duration and
silent-audio requirement before producing `manifest.json` and `report.html`.

### Gemini hook-copy experiment

When the local preview pack is approved, test the actual copy model before generating any new
media. This makes two Gemini text calls: one to generate four pre-assigned formats and one to
review their grounding and safety. Each option includes its separate manual-upload title. It
does not generate an image or video, write to the daily database, deliver to Telegram, or deploy
anything.

```bash
flagd-ugc hook-copy-lab \
  --demos-dir ./data/demos \
  --output-dir ./hook-copy-output/first-live-test \
  --confirm-paid \
  --open
```

Use `--dry-run` instead of `--confirm-paid` to inspect that day's selected format/demo pairings
without contacting Gemini.

The final MP4s intentionally contain no music or other audio. Select a platform-native sound
only when manually uploading each video.

## Configuration

Copy `env.example` to `.env` and set the secrets relevant to the command you run:

```dotenv
GEMINI_API_KEY=...
FAL_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Local commands automatically load `.env` from the current working directory without
overriding variables already exported by the shell. `FAL_KEY` is used by both the local
animation lab and the daily pipeline's Kling 3 Standard queue.

The fal client uses the operating system certificate store through `truststore`. This
supports managed Macs whose HTTPS proxy certificate is trusted by macOS. TLS verification is never
disabled. Before a paid experiment, verify several fresh connections without inference:

```bash
flagd-ugc fal-check --attempts 5
```

The defaults use `gemini-3.6-flash` for text/review, Nano Banana Pro through
`gemini-3-pro-image`, and fal.ai's `fal-ai/kling-video/v3/standard/image-to-video` queue.
Model names can be changed without code using `GEMINI_TEXT_MODEL`, `GEMINI_IMAGE_MODEL`, and
`FAL_KLING_MODEL`. The fal adapter uses the configured key securely as a `Key` authorization
header and keeps TLS verification enabled.

## Local first-scene experiments

For video-first experiments, use one command. It generates the required starting frame
internally, selects a technically valid result, animates it, and opens only the final video
review:

```bash
flagd-ugc hook-video-lab \
  --master ./path/to/master.jpeg \
  --scenario bed-facepalm \
  --duration 3 \
  --confirm-paid \
  --output-dir ./hook-video-output \
  --open
```

The internal image is an implementation input for image-to-video, not a separate user
deliverable. Every named scenario is her own arm's-length front-camera selfie: she holds the
phone herself, the phone stays outside its own image, and motion is coupled to her camera arm.
Prompts forbid another camera operator, tripod, gimbal, zoom and cinematic movement.
Before Kling submission, an automated visual gate rejects identity drift, non-selfie framing,
extra or branched limbs, malformed hands and other generation defects. A failed image can be
regenerated up to two times; only a passing frame may reach paid animation. `--confirm-paid`
authorizes these gated image attempts and one fal animation request. Before any generation,
the command probes a fake
fal queue request and stops without spending if that path is blocked by the network.

If local polling is interrupted after submission, resume the whole hook-video run without
`--confirm-paid`; this retrieves the already-paid request and updates the final report:

```bash
flagd-ugc hook-video-lab --resume ./hook-video-output/RUN --open
```

Use `flagd-ugc scene-lab` only when specifically tuning or comparing first-frame identity,
realism, and three-second motion readiness. It creates versioned, self-contained HTML review
bundles and requires an explicit `--confirm-paid` flag before any image-generation call.
Named scenarios include seated bedroom, bed-reclined, desk-chair, city-walk, doorway retreat,
confused chair rise, bed facepalm and kitchen hand-over-mouth compositions;
each includes a pose-specific animation prompt. Wardrobe is independently selectable so the
character does not appear in the same hoodie in every video.
See [SCENE_LAB.md](SCENE_LAB.md) for the baseline command and tuning protocol.

## Local animation experiments

After manually accepting a scene-lab still, animate exactly that candidate with fal.ai
Kling 3 Standard. The paired `MOTION_PROMPT.txt` beside the candidate is selected
automatically:

```bash
flagd-ugc animation-lab \
  --image ./scene-lab-output/RUN/generated-01.png \
  --duration 3 \
  --confirm-paid \
  --output-dir ./animation-lab-output \
  --open
```

Scene-aware behavioral hooks can be selected with `--visual-hook`. They begin with
human action rather than a neutral pose: use `bed-facepalm` with a bed-reclined
candidate and `chair-confused-rise` with a desk-chair candidate.

The paid flag is mandatory for the initial submission. At the current audio-off price, one
three-second test is approximately `$0.252`. The experiment saves the request ID immediately,
downloads the MP4, checks vertical format, H.264, duration and absence of audio, extracts one
review frame per second, and creates an HTML review page.

If polling or downloading is interrupted after submission, resume the same request without
creating or charging for another generation:

```bash
flagd-ugc animation-lab --resume ./animation-lab-output/RUN --open
```

## Verification and first run

Install locally with Python 3.12 and FFmpeg:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Then use the paid-call gates deliberately:

```bash
# Validates configuration and supplied assets, without provider calls.
flagd-ugc preflight

# Creates an isolated one-video batch under /data/smoke. No Telegram delivery.
flagd-ugc smoke --confirm-paid

# Generates the eight-image consistency pack. Inspect every image afterward.
flagd-ugc character-samples --confirm-paid

# Regenerates only one failed sample while preserving the rest of the pack.
flagd-ugc character-samples --replace 2 --confirm-paid

# Run only after a person has inspected and accepted all eight samples.
flagd-ugc approve-samples --reviewer "Reviewer name"

# Re-run preflight, then create and deliver one complete four-video batch.
flagd-ugc preflight
flagd-ugc run
```

`preflight` intentionally fails until the eight samples have automated pass results and a
manual approval record. Paid smoke and character generation are never triggered by tests,
image builds, health checks, or startup.

## Resume and delivery behavior

SQLite records hooks, upload titles, opening directions, scenes, visual reviews, Kling job IDs,
animation reviews, composition, codec validation, and every Telegram message. A unique date
batch plus a renewable lease prevents two workers from processing the same London date. Restarts
reuse valid files and resume polling the same Kling task rather than creating another paid task.

Telegram Bot API has no idempotency key or outgoing-message lookup. To prevent duplicate
messages, this service commits an `inflight` delivery reservation before each request. If the
connection fails after that point and acceptance cannot be determined, the item is marked
`uncertain`, is not resent automatically, and a separate delivery alert is attempted. This is
at-most-once behavior; inspect the chat and SQLite ledger before manually resolving an
uncertain send.

Successful batches send four MP4 documents, each captioned with its short upload title, followed
by the date and numbered titles plus opening texts. Partial batches send every valid MP4, the
same summary with failed entries marked, then an alert containing the failures.

## Dokploy deployment

1. Create a private Dokploy Compose application from this directory. The checked-in Compose
   command starts idle so the first deployment cannot generate or deliver automatically.
2. Add the four secrets directly in Dokploy; never upload `.env`. Keep
   `TIMEZONE=America/Los_Angeles` and `RUN_TIME=08:00`. The schedule follows California daylight
   saving time even when the VPS is in the UK.
3. Attach persistent storage at `/data`. If Dokploy manages an external volume, replace the
   named volume in `docker-compose.yml` with that volume or bind mount.
4. Upload the approved references and demo manifest/clips into the mounted paths.
5. Run the verification sequence above in the service console.
6. Inspect the complete delivered four-video batch, then replace the Compose `sleep infinity`
   command with `["flagd-ugc", "daemon"]` to enable the daily schedule.

The container health check verifies SQLite, FFmpeg, and FFprobe only. It performs no network
calls. Retention cleanup runs after every scheduled batch and is also available through
`flagd-ugc cleanup`.

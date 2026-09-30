# Scene lab

The scene lab tests one question before any video animation is purchased: can one master
identity reference reliably produce a realistic, motion-ready first frame?

## Baseline experiment

Start with a dry run. This writes a versioned experiment bundle and imports an existing
candidate without contacting Gemini:

```bash
flagd-ugc scene-lab \
  --master "/Users/zmaukey/Downloads/Sofia Laurent/master_photo.jpeg" \
  --import-image "/Users/zmaukey/Downloads/Sofia Laurent/scene1.jpeg" \
  --samples 4 \
  --dry-run \
  --output-dir ./scene-lab-output \
  --open
```

Each bundle contains the master copy, exact scene and motion prompts, manifest, candidates,
and an HTML review sheet. Checkbox progress is saved in the browser. Use **Download review
JSON** when the review is complete.

When `GEMINI_API_KEY` is configured, one four-sample paid experiment is:

```bash
flagd-ugc scene-lab \
  --master "/Users/zmaukey/Downloads/Sofia Laurent/master_photo.jpeg" \
  --samples 4 \
  --confirm-paid \
  --output-dir ./scene-lab-output \
  --open
```

Before retrying after a connection or API failure, run `flagd-ugc gemini-check`. It performs
only a read-only model metadata request—no image generation and no generation charge. It
checks that Python can reach Gemini and that the configured key can access the selected model.

`--confirm-paid` is mandatory for generation. Tests, dry runs, and opening old reports cannot
make paid calls. The default is the stable `gemini-3-pro-image` model, 2K, 9:16, and the v5
scene prompt. Earlier versions remain in the prompt package for controlled comparisons.

## Scenario variation

The seated bedroom prompt is only the baseline. Select a different pose and setting with
`--scenario`:

```text
bedroom-seated  casually seated in a lived-in bedroom
bed-reclined    reclining imperfectly against rumpled bedding
desk-chair      sitting sideways in an ordinary desk chair
city-walk       walking casually on an ordinary city street
doorway-backout enters a room, notices something and backs out
chair-confused-rise looks confused and abruptly begins to stand
bed-facepalm    lies on an unmade bed and facepalms
kitchen-cover-mouth freezes while crossing a kitchen and covers her mouth
```

For example, generate one bed-reclined candidate with:

```bash
flagd-ugc scene-lab \
  --master "/Users/zmaukey/Downloads/Sofia Laurent/master_photo.jpeg" \
  --scenario bed-reclined \
  --samples 1 \
  --confirm-paid \
  --output-dir ./scene-lab-output \
  --open
```

Each scenario has its own frame-zero pose and paired three-second motion prompt. Identity,
natural skin texture, casual imperfection, caption clearance and restrained reaction timing
remain common guardrails. Test repeated samples of one scenario at a time; generating one of
each tests variety, not consistency.

Wardrobe is independent from identity. Scenarios default to varied ordinary outfits rather
than repeating the same hoodie. Override the default with `--wardrobe`:

```bash
flagd-ugc scene-lab \
  --master "/Users/zmaukey/Downloads/Sofia Laurent/master_photo.jpeg" \
  --scenario bedroom-seated \
  --wardrobe cream-knit \
  --samples 1 \
  --confirm-paid \
  --output-dir ./scene-lab-output \
  --open
```

Available wardrobe keys are `charcoal-hoodie`, `washed-navy-tee`, `forest-sweatshirt`,
`olive-street-layers`, `cream-knit`, `burgundy-long-sleeve`, `heather-lilac-tee`,
`black-zip-hoodie`, and `rust-striped-tee`. Clothing must remain ordinary, unbranded,
imperfect and appropriate to the setting; facial identity must remain unchanged.

## Tuning method

Generate four images with the same prompt version. Accept a prompt only when at least three
of four candidates pass every hard gate:

- unmistakably the same identity as the master;
- ordinary phone-video realism, without polished AI or influencer styling;
- the mundane task is already visibly underway;
- gaze has a clear reason to remain on the task;
- the body, hands and nearby object can continue that task naturally over three seconds;
- coherent face, body, hands, clothing, and background;
- neutral expression with no added reaction beat;
- face remains clear and the composition leaves room for caption text.

Change only one prompt variable between experiments. Record what changed in a new prompt
file and pass it with `--prompt-file`; use `--motion-prompt-file` for the paired animation
instruction. Do not animate a candidate until its still prompt meets the three-of-four rule.

Useful early variables to test independently are action clarity, task-directed gaze, camera
distance, and background detail. For everyday-task UGC, use the task-driven direction below
and change only one scene variable at a time.

## What the still should communicate

For the current everyday-task direction, the image is a casual frame already in the middle of
a mundane action, not a completed reaction or polished portrait. The person has a clear reason
to move; her gaze belongs on the task. Her expression stays neutral. The body angle, free hand,
and nearby object should make the action legible from the starting frame, with enough room for
a small continuation over three seconds. See [TASK_DRIVEN_UGC_RULE.md](experiments/TASK_DRIVEN_UGC_RULE.md)
for the reusable gaze, movement and benchmark guidance.

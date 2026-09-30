# Imperfect handheld selfie experiment

Goal: compare a closer, understated phone-selfie opening against the existing generated
openings. This changes starting-frame composition and motion direction together; it is a
creative pilot, not a test that isolates one causal variable.

Use `data/characters/master.jpeg` as the identity reference. It is ignored by Git.
Use Gemini's configured image model at 1K and Kling 3 Standard with audio disabled.
Retain original outputs for assessment; do not add shake, grain or beauty processing.

## Scope and cost

- Generate one starting frame; allow at most one replacement if visually unsuitable.
- Inspect identity, natural framing, and anatomy before animation.
- Generate three independent three-second animations from the same accepted frame and
  identical `motion.txt`. Run sequentially. Preserve every output, including rejects.
- Image output: $0.134 per 1K image. Video: $0.084 per second, $0.252 per attempt.
- Planned media generation: $0.890; with a second image: $1.024.
- Additional input/text/review tokens are not included above. Overall authorized-test
  ceiling, once approved: $3. Stop if actual rates or request scope would exceed it.
- No automatic paid resubmission after ambiguous submission failures; resume saved IDs.
- No production configuration changes, daily database writes, or Telegram delivery.

These are planning limits, not a billing cap enforced by the existing CLI. Execution must
track submitted calls. Paid execution remains pending the user's confirmation of this brief.

## Execution using existing lab commands

Run in a prepared environment with the project installed, FFmpeg/FFprobe, and the existing
provider credentials. Resolve paths from the project root.

```sh
flagd-ugc scene-lab \
  --master ./data/characters/master.jpeg \
  --samples 1 \
  --image-size 1K \
  --prompt-file ./experiments/imperfect-selfie-v1/scene.txt \
  --motion-prompt-file ./experiments/imperfect-selfie-v1/motion.txt \
  --output-dir ./scene-lab-output/imperfect-selfie-v1 \
  --dry-run
```

After paid approval, replace `--dry-run` with `--confirm-paid`. Inspect the resulting image.
Then run this command three times sequentially, setting `ACCEPTED_FRAME` to the actual
accepted generated image path:

```sh
flagd-ugc animation-lab \
  --image "$ACCEPTED_FRAME" \
  --motion-prompt-file ./experiments/imperfect-selfie-v1/motion.txt \
  --duration 3 \
  --kling-tier standard \
  --output-dir ./animation-lab-output/imperfect-selfie-v1 \
  --confirm-paid
```

## Review

Compare at normal playback size and speed, without captions initially:

- Does it feel like a casual phone recording in the first second?
- Does the camera move with a plausible holding arm, with consistent background perspective?
- Is the expression small and naturally evolving, without a fixed stare or exaggerated grin?
- Is character identity stable through blinks and changes of angle?
- Are softness and noise unobtrusive, with no facial smearing or anatomy errors?

Record accepted/rejected and the reason for all three outputs. Compare with generated-1,
generated-2 and generated-3. Only after choosing a usable result, try a caption whose meaning
fits restrained amusement and append an approved demo.

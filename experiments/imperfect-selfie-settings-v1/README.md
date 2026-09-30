# Everyday-setting variations

Follow-up to the three imperfect-selfie-v1 animations approved by the user. The user
requested the same casual, imperfect phone-video style across different settings and
outfits. This pilot tests kitchen, sofa, and outdoors with one generation per setting.

The scene recipes retain the approved identity and capture instructions. Clothing,
background and lighting context vary. All clips use the same small glance and restrained
smile, so this does not validate other reactions or establish a production success rate.

## Approved scope

- Three Nano Banana Pro starting images at 1K, one per setting.
- Visual inspection of the starting frames before animation.
- Three Kling 3 Standard animations, three seconds each, audio disabled.
- No paid retries. Submission markers prevent ambiguous failures from buying replacements
  on rerun; saved animation requests can resume.
- Estimated image output and video cost: $1.158, plus image input/text tokens.
- Including the first round's $0.890, estimated media cost is $2.048 against the existing
  $3 ceiling. This is a planning estimate, not a measured provider invoice.

`recipes.json` contains the exact prompts. `run.py` uses the existing project's providers
inside the deployed container, with its existing credentials; it does not read or write
the daily pipeline database. Outputs live under `/data/experiments/imperfect-selfie-settings-v1`.

Local copies and the comparison page are under the Git-ignored directory
`data/experiments/imperfect-selfie-settings-v1/`. Original videos are retained without
added grain, sharpening, captions or artificial shake. User playback review determines
whether these variations match the desired style.

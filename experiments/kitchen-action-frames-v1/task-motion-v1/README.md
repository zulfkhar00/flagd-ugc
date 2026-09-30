# Selected task-motion animation test

Animates the four selected frames from the physically occupied kitchen batch:
`lean-into-drawer`, `adjust-coffee-maker`, `reach-for-mug`, and `sit-at-island`.

The prompts preserve the approved kitchen no-reaction v5 approach (neutral face,
no deliberate gaze or head action, tiny independent handheld drift) and ask only
for a few centimeters of continuation of the task already visible in each still.
Each three-second Kling standard clip is estimated at $0.252, or $1.008 total.

Run `run.py` in the app container after placing this folder under
`/data/experiments/kitchen-action-frames-v1/task-motion-v1`. Its selected images
are read from the parent directory. Results are written under `outputs/`.

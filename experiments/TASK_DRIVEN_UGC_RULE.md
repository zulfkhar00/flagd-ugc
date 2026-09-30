# Task-driven UGC generation rule

Use this direction for upcoming everyday UGC scene generations. The mundane physical task
should carry the clip; do not add a separate reaction for the viewer.

Her attention stays primarily on the task she is performing. Do not make her repeatedly look
toward the camera. A brief accidental pass near the lens is okay if her attention naturally
returns to the task. Keep the face emotionally neutral.

The starting frame should show the task already in progress. Animate only a small continuation
of that task. Let her body follow it with subtle arm, shoulder and torso movement; hair and
clothing respond naturally. Keep camera drift tiny and independent of the task. End during the
ordinary moment, before the task or expression resolves into a pose.

## Posture coupling

Prefer mundane tasks that naturally change the subject's whole-body posture. Good examples
include leaning over a bed, tying a shoe, reaching into a drawer, wiping a counter, folding
something over furniture, or reaching into a bag. Weaker choices leave her upright in a selfie
pose while only one hand moves an object.

The task should physically explain her gaze, head position, shoulders, torso angle and arm
movement. If only the hand performs the task while her head, torso and shoulders remain posed
for the camera, reject the clip. Do not add movement just to satisfy this rule; choose a task
whose natural mechanics create it.

Keep scenes ordinary and geographically grounded where relevant. For groceries, use a plain
reusable tote with no visible branding or lettering. Keep the front-camera point of view
physically plausible, with the recording phone outside the frame.

Current reference clips:

- `data/experiments/task-context-generalization-v1/animation-v1/clips/bedroom-make-bed.mp4`
- `data/experiments/everyday-task-frames-v1/animation-v1/clips/wipe-counter.mp4`
- `data/experiments/kitchen-action-frames-v1/task-motion-v1/clips/lean-into-drawer.mp4`

These three are the primary style references. The bedroom clip is the strongest overall
result from the generalization batch. Use the three clips together when creating new everyday
situations. Do not spend more iterations tuning Kling motion unless a new scene exposes a
specific failure.

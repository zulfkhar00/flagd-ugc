HANDHELD_SCENE_INSTRUCTION = """

SELF-SHOT FRONT CAMERA — REQUIRED
This image is the live front-facing camera view from a phone the woman herself holds in
one extended arm. She is filming herself at arm's length; there is no photographer, friend,
camera operator, tripod, stand or fixed mount. The phone itself cannot appear because it is
the camera producing this image. The camera-bearing shoulder and arm position must be
physically plausible, while the other hand remains free for the described gesture. Use mild
natural arm-length perspective and casual imperfect framing without an enlarged foreground
hand or extreme ultrawide distortion. Show exactly two arms total. The camera arm is mostly
outside the crop; at most one continuous part of its sleeve or forearm may enter along a
single frame edge, never crossing the image centre. Never add a camera hand, branching limb,
detached hand, third arm or third hand to explain the selfie viewpoint.
""".strip()


HANDHELD_MOTION_INSTRUCTION = """

SELF-SHOT FRONT CAMERA — REQUIRED
She continues holding the front-facing phone herself in the same arm. Camera motion stays physically coupled
to her body; only her free hand gestures. Keep framing. No operator, tripod,
gimbal, zoom, cut, jerk or visible phone.
""".strip()


def with_handheld_scene(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{HANDHELD_SCENE_INSTRUCTION}\n"


def with_handheld_motion(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{HANDHELD_MOTION_INSTRUCTION}\n"

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


US_KITCHEN_SETTING_INSTRUCTION = """

KITCHEN SETTING — CONTEMPORARY UNITED STATES, NIGHT
She is in a normal contemporary American apartment kitchen at night. Show only
partial, incidental glimpses of familiar American kitchen details: simple modern or
shaker-style upper cabinets, a laminate or quartz-look countertop, part of a stainless
or black refrigerator, and one or two ordinary appliances or items such as a microwave,
toaster, coffee maker, or paper towel roll. Near the sink there may be dish soap and a
sponge. Include one or two used mugs or dishes and a grocery bag or unopened household
item casually left on the counter. The kitchen is lived-in but not dirty.

Use ordinary contemporary U.S. apartment finishes. Nothing is luxurious or professionally
styled. Avoid old dark rental cabinetry dominating the image, bare ceiling bulbs, antique
fixtures, cramped European kitchenette or post-Soviet apartment cues, and excessive yellow
lighting. Light comes from normal U.S. ceiling fixtures or recessed lights; the phone may
expose the scene imperfectly.
""".strip()


US_ENTRYWAY_SETTING_INSTRUCTION = """

ENTRYWAY SETTING — CONTEMPORARY UNITED STATES
She has just walked into the entry area of a normal contemporary American apartment.
Behind her, casually cropped, show white or off-white painted drywall, a standard
American interior door, a coat hanging from a simple hook, a pair of sneakers near the
wall, and perhaps the edge of a console table or a reusable shopping bag. The entryway
is modest and ordinary but not cramped, shabby or old.

Use a normal flush-mount ceiling fixture or light spilling in from the living room. The
phone exposure can be slightly uneven and dim, but the architecture itself is not gloomy
or deteriorated.
""".strip()


US_STREET_SETTING_INSTRUCTION = """

STREET SETTING — CONTEMPORARY UNITED STATES
Ground the ordinary street scene in the United States through subtle everyday
infrastructure: a broad American road, concrete sidewalk and painted curb, utility poles,
low-rise retail or apartment buildings in the distance, and ordinary U.S.-style parked
cars. Keep these as incidental background cues, not symbols or tourist landmarks. Use a
mundane suburban or neighborhood street rather than a picturesque destination.
""".strip()


HANDHELD_MOTION_INSTRUCTION = """

SELF-SHOT FRONT CAMERA — REQUIRED
She continues holding the front-facing phone herself in the same arm. Camera motion stays physically coupled
to her body; only her free hand gestures. Keep framing. No operator, tripod,
gimbal, zoom, cut, jerk or visible phone.
""".strip()


US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION = """

RESIDENTIAL SETTING — CONTEMPORARY UNITED STATES
The character lives in the United States. Any home interior must read as a normal,
currently occupied middle-class U.S. apartment or suburban house, plausibly furnished
with ordinary contemporary U.S. household items. Add a few incidental signs of daily life
such as laundry, bedding, charging cords, mugs, or practical items when they fit the scene.
Casual imperfection comes from lived-in belongings, relaxed clothing, imperfect framing,
and ordinary phone exposure. It does not mean deprivation, a bare room, institutional or
run-down surroundings, or European/post-Soviet architecture. Avoid bare hanging bulbs,
freestanding European wardrobes, cramped rental corridors, sparse white rooms, deteriorated
frames, austere furniture, unusually dark brown interiors, coordinated influencer decor, or
polished studio lighting. Use ordinary practical fixtures and believable room proportions.
""".strip()


US_BEDROOM_SETTING_INSTRUCTION = """

BEDROOM SETTING — CONTEMPORARY UNITED STATES
She is sitting casually on the edge of her bed in a normal contemporary American
apartment bedroom. The room belongs to a woman in her 20s and looks genuinely lived in
rather than professionally decorated.

Behind her are fragments of:
- an unmade bed with a soft comforter and several pillows
- a small nightstand with an ordinary bedside lamp
- white horizontal window blinds
- a simple dresser
- a laundry basket or a few clothes casually left nearby
- a charging cable plugged into a normal wall outlet

The furniture looks affordable and contemporary, similar to ordinary Target/IKEA/Wayfair
furniture found in U.S. apartments. Walls are warm off-white or light beige.

There is a normal flush-mount ceiling fixture or bedside lamp. Absolutely no bare hanging
bulb.

The room should feel like a regular American bedroom, not a European rental, hostel,
minimalist studio, post-Soviet apartment, hotel room or staged influencer set.

Imperfection comes from the woman's belongings and casual mess, not from old architecture
or shabby fixtures.
""".strip()


def with_handheld_scene(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{HANDHELD_SCENE_INSTRUCTION}\n"


def with_handheld_motion(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{HANDHELD_MOTION_INSTRUCTION}\n"

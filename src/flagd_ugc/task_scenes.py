"""Everyday actions that make the UGC subject's posture and gaze feel motivated."""

from __future__ import annotations

from dataclasses import dataclass

from .camera_style import (
    with_handheld_scene,
)
from .wardrobes import get_wardrobe


US_TASK_BEDROOM_SETTING = """
BEDROOM SETTING — CONTEMPORARY UNITED STATES
Use a normal contemporary American apartment bedroom with warm off-white walls, a soft
comforter, a small nightstand and ordinary lamp, horizontal window blinds, and a laundry
basket or a few clothes nearby. Furniture and fixtures are affordable and current. No bare
hanging bulb, hotel styling or run-down architecture.
""".strip()

US_TASK_ENTRYWAY_SETTING = """
ENTRYWAY SETTING — CONTEMPORARY UNITED STATES
Use an ordinary U.S. apartment entry with painted drywall, a standard interior door, a simple
bench or hook, and everyday sneakers. Keep normal room proportions and practical lighting.
""".strip()

US_TASK_HOME_SETTING = """
HOME SETTING — CONTEMPORARY UNITED STATES
Use an ordinary currently occupied middle-class U.S. apartment or suburban home with
contemporary everyday furniture and practical household fixtures. Let a few normal
belongings appear incidentally where they belong. Keep the room's architecture and lighting
plausible; it is lived-in, not a staged influencer set or a run-down space.
""".strip()

US_TASK_KITCHEN_SETTING = """
KITCHEN SETTING — CONTEMPORARY UNITED STATES
Use an ordinary contemporary American apartment kitchen with simple modern or shaker-style
cabinets, a practical counter and a partial view of a stainless or black refrigerator.
Include one or two ordinary appliances or used items only if they fit the frame. The kitchen
is lived-in but not dirty; lighting comes from normal ceiling fixtures or recessed lights.
""".strip()


SCENE_RULE = """
TASK-FIRST STARTING FRAME
This is a candid frame from the middle of an ordinary task, not a posed creator shot.
Make the physical task legible through visible hand-to-object contact. Her attention and
neutral expression stay with the task. Let the task explain her head, shoulder and torso
orientation, with a naturally asymmetric posture. Keep the phone's top safe area clear for
the short caption added later. Show normal everyday belongings only where they naturally
belong. No smile or reaction, no gaze to camera, no visible phone, mirror, reflection, logo,
text, extra person or extra limb.
""".strip()

MOTION_RULE = """
This is a silent, arbitrary three-second excerpt from a longer casual front-camera recording.
Continue only the task already in progress by a small amount; do not finish it. Her gaze
stays on the task and her face remains neutral, with relaxed lips. No deliberate lens glance,
head turn, smile, frown, surprise or other reaction. Her arm, shoulder, head and torso make
small natural adjustments caused by the same task. Do not leave her body posed while only
the hand moves. Hair and clothing respond subtly. The phone hand has tiny irregular
handheld drift that remains physically plausible. The movements are modest and unperformed;
the clip cuts in the middle of the ordinary action. No speech, lip-sync, cut, zoom or pose.
""".strip()

TASK_HANDHELD_MOTION = """
SELF-SHOT FRONT CAMERA — REQUIRED
She continues holding the front-facing phone herself in the same arm. The phone has only
tiny natural wrist drift; its movement remains physically plausible alongside the task-driven
body movement. Her free hand continues the task already underway. No operator, tripod, gimbal,
zoom, cut, jerk or visible phone.
""".strip()


@dataclass(frozen=True)
class TaskScene:
    key: str
    label: str
    wardrobe_key: str
    scene: str
    continuation: str
    setting: str = "residential"

    def scene_prompt(self) -> str:
        setting = {
            "bedroom": US_TASK_BEDROOM_SETTING,
            "entryway": US_TASK_ENTRYWAY_SETTING,
            "kitchen": US_TASK_KITCHEN_SETTING,
            "residential": US_TASK_HOME_SETTING,
            "car": """PARKED CAR SETTING — UNITED STATES
The car is safely parked with the engine off. Use a plausible ordinary U.S.-market car
interior and incidental view of a driveway or parking lot. No driving, road motion, crash,
or visible phone.""",
        }[self.setting]
        return with_handheld_scene(
            "\n\n".join(
                (
                    self.scene.format(wardrobe=get_wardrobe(self.wardrobe_key).prompt),
                    SCENE_RULE,
                    setting,
                )
            )
        )

    def motion_prompt(self) -> str:
        return (
            f"{MOTION_RULE}\n\nTask continuation: {self.continuation}"
            f"\n\n{TASK_HANDHELD_MOTION}"
        )


TASK_SCENES: tuple[TaskScene, ...] = (
    TaskScene(
        key="bedroom-make-bed",
        label="straighten the duvet",
        wardrobe_key="charcoal-hoodie",
        setting="bedroom",
        scene="""She is already leaning over a bed and drawing one corner of the duvet into place
with her free hand. Her eyes stay on the bedding. The mattress edge and part of the duvet
fill the lower foreground; her torso bends naturally into the reach. She wears {wardrobe}.
Include only incidental glimpses of a nightstand and ordinary bedroom belongings.""",
        continuation=(
            "She draws the same duvet corner a few centimeters farther and shifts "
            "her weight with the reach."
        ),
    ),
    TaskScene(
        key="entryway-tie-shoe",
        label="tie a sneaker",
        wardrobe_key="heather-lilac-tee",
        setting="entryway",
        scene="""She is seated on a simple entry bench with one knee raised, already adjusting the
laces of her sneaker with her free hand. She looks down at the laces. Her shoulders and
torso angle toward the raised knee rather than toward the viewer. She wears an ordinary
{wardrobe} and jeans. Show a standard interior door and one or two pairs of shoes nearby.""",
        continuation=(
            "She makes one small lace adjustment, inclining and settling her "
            "shoulders and torso toward the shoe."
        ),
    ),
    TaskScene(
        key="living-room-fold-throw",
        label="fold a throw over a sofa",
        wardrobe_key="forest-sweatshirt",
        setting="residential",
        scene="""In a normal U.S. apartment living room, she is already folding a throw over the
back of an ordinary sofa with her free hand. The fabric and her working arm enter the near
foreground. Her torso leans over the sofa and her eyes follow the edge of the throw. She
wears {wardrobe}. Show partial sofa and coffee table with an ordinary item, not a styled set.""",
        continuation=(
            "She draws the same section of throw a few centimeters over the sofa back "
            "as her upper body leans with it."
        ),
    ),
    TaskScene(
        key="kitchen-wipe-counter",
        label="wipe the counter",
        wardrobe_key="rust-striped-tee",
        setting="kitchen",
        scene="""In a normal contemporary American apartment kitchen, she is already wiping one
small area of the counter with a dishcloth. Her gaze is on the counter; her torso leans
slightly into the wipe and one shoulder follows the reaching arm. She wears {wardrobe}.
Show an incidental section of counter and ordinary cabinets, with a mug or paper towel roll.""",
        continuation=(
            "She wipes the same patch once more with a short natural stroke; her "
            "torso and shoulder follow the reach."
        ),
    ),
    TaskScene(
        key="kitchen-lean-into-drawer",
        label="look through a kitchen drawer",
        wardrobe_key="washed-navy-tee",
        setting="kitchen",
        scene="""In a normal contemporary U.S. apartment kitchen, she is already leaning toward an
open utensil drawer and moving one or two utensils with her free hand. Her eyes stay inside
the drawer. Her upper body bends forward and slightly to one side; the drawer edge and her
arm create natural foreground depth. She wears {wardrobe}.""",
        continuation=(
            "She shifts one utensil a little inside the same open drawer and leans a "
            "few centimeters farther, then settles."
        ),
    ),
    TaskScene(
        key="laundry-fold-towel",
        label="fold a towel",
        wardrobe_key="cream-knit",
        setting="residential",
        scene="""At an ordinary U.S. apartment laundry area, she is already folding a towel on top
of a washer or dryer. Her free hand holds the towel edge while her camera-holding arm stays
outside the crop. Place the camera slightly off-axis and frame her asymmetrically; her
gaze and a small torso lean follow the towel. She wears {wardrobe}. A washer and laundry
basket appear incidentally in the background.""",
        continuation=(
            "She brings the same towel edge a few centimeters over and shifts her "
            "weight gently toward the machine."
        ),
    ),
    TaskScene(
        key="kitchen-grocery-tote",
        label="put groceries away",
        wardrobe_key="burgundy-long-sleeve",
        setting="kitchen",
        scene="""In a normal contemporary American apartment kitchen, she is already reaching into
a plain reusable grocery tote on the counter to put away one ordinary item. Her eyes look
into the bag, and her torso bends slightly toward it. She wears {wardrobe}. Keep the tote
unbranded and show only incidental familiar kitchen details.""",
        continuation=(
            "She adjusts the same ordinary item inside the unbranded tote and shifts "
            "her torso slightly toward the counter."
        ),
    ),
    TaskScene(
        key="parked-car-seatbelt",
        label="settle the seatbelt",
        wardrobe_key="olive-street-layers",
        setting="car",
        scene="""Inside a safely parked car, she is already smoothing the seatbelt flat against her
shoulder with her free hand. Her eyes look at the belt, her shoulder lifts slightly, and her
torso turns toward that shoulder. She wears {wardrobe}. Use a casual close front-camera crop;
the phone itself stays outside the image.""",
        continuation=(
            "She smooths the belt a few centimeters and makes a small matching "
            "shoulder and torso adjustment."
        ),
    ),
)

_BY_KEY = {item.key: item for item in TASK_SCENES}
if len(_BY_KEY) != len(TASK_SCENES):  # pragma: no cover - module invariant
    raise RuntimeError("task scene keys must be unique")


def get_task_scene(key: str) -> TaskScene:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"unknown task scene {key!r}") from exc


def task_scene_keys() -> tuple[str, ...]:
    return tuple(item.key for item in TASK_SCENES)

"""Expressive, silent reactions and American settings for daily UGC hooks.

The copy planner chooses the emotion alongside the hook. Settings rotate independently;
neither a task nor a second facial beat is added to the selected reaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from .camera_style import (
    US_BEDROOM_SETTING_INSTRUCTION,
    US_ENTRYWAY_SETTING_INSTRUCTION,
    US_KITCHEN_SETTING_INSTRUCTION,
    US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION,
    with_handheld_scene,
)
from .errors import QualityError
from .wardrobes import get_wardrobe


@dataclass(frozen=True)
class HookReaction:
    key: str
    label: str
    copy_context: str
    movement: str
    expressive: bool = False

    def motion_prompt(self) -> str:
        if self.expressive:
            return f"""A single continuous three-second silent front-camera reaction hook.
The reaction starts immediately and is clearly readable within the first second. She feels
one strong emotion as a sudden realization registers, with attention near the recording lens:
{self.movement}

Make this visibly expressive and attention-grabbing, like a real person reacting strongly.
Do not reduce it to a neutral face, tiny eyebrow twitch, faint smirk or casual glance.
Her face keeps normal human proportions. Her head, shoulders and upper torso naturally
respond to the SAME emotion; hair and clothing follow that movement. No unrelated emotional
beats, cartoon distortion, repeated action cycle or theatrical pose changes.

An open mouth, teeth or a broad smile are allowed when the selected reaction requires them.
No speech, lip-sync, silent mouthing or phoneme sequences. A jaw-drop is one continuous
reaction shape, not opening and closing the mouth as if saying words. Natural blinking is fine.
This image is the view from the phone she herself holds, not a view of her holding a phone.
She has no second phone, screen or object in her free hand. She continues holding the camera
with the same arm, mostly outside its own image. Tiny irregular handheld wrist drift
responds to her movement without becoming a shake effect or deliberate camera move.
Preserve identity, makeup, room, outfit and anatomy. No household task, new prop, hand-to-face
gesture, visible phone, mirror, camera cut, zoom or generated text. Cut during the strong
reaction, before she resets to a neutral face or a posed smile.
""".strip()
        return f"""A single continuous three-second silent front-camera reaction hook.
Start from the relaxed expression in the input frame. She gives only this small reaction:
{self.movement}

Her attention stays near the phone, as though reading something just below its lens. One
ordinary blink may happen independently. No deliberate look-away or large head turn, no
sequence of separate emotions, no exaggerated TikTok acting. Keep her lips closed or gently
at rest: no speech, lip-sync, silent mouthing, phoneme shapes or repeated mouth opening.

One shoulder or her torso settles slightly at an unrelated moment; hair and clothing respond
subtly. She keeps filming with the same camera-bearing arm. The phone has tiny irregular
wrist drift, with small changes in framing and background perspective independent of the facial
reaction. No deliberate camera move or perfect recentering.
Preserve the starting identity, makeup, room, outfit, pose and anatomy. No household task,
new prop, hand-to-face gesture, camera cut, zoom, generated text or visible phone. The shot
cuts while the small reaction is still alive, without a final beauty pose or held ending smile.
""".strip()


LEGACY_HOOK_REACTIONS: tuple[HookReaction, ...] = (
    HookReaction(
        key="skeptical",
        label="restrained skeptical eyebrow raise",
        copy_context="Doubt or mild disbelief about the situation described in the overlay.",
        movement=(
            "One eyebrow lifts a little more than the other and her lips press together "
            "slightly. Her head stays almost still; no smirk or theatrical eye roll."
        ),
    ),
    HookReaction(
        key="surprised",
        label="small surprised realization",
        copy_context="Unexpected information or feedback, without asserting good or bad news.",
        movement=(
            "Her eyebrows rise slightly and her eyes widen just a little, as though "
            "something registered. Her mouth remains relaxed and closed; no gasp."
        ),
    ),
    HookReaction(
        key="amused",
        label="small suppressed smile",
        copy_context="An amusing or lightly ironic situation supported by the demo and overlay.",
        movement=(
            "One corner of her mouth lifts into a restrained closed-mouth almost-laugh; "
            "her cheeks respond a little. No teeth display or full laughing performance."
        ),
    ),
    HookReaction(
        key="embarrassed",
        label="small sheepish reaction",
        copy_context="A relatable mistake, awkward attempt or disappointing visible feedback.",
        movement=(
            "Her lips press together softly and her chin lowers only a few degrees in "
            "a small sheepish acknowledgement. No head drop, facepalm or smile."
        ),
    ),
    HookReaction(
        key="pleased",
        label="restrained pleased relief",
        copy_context="A small win or positive feedback actually supported by the demo.",
        movement=(
            "Her eyes soften and a small closed-mouth smile begins, with one subtle "
            "release of tension in her face. No broad grin, nod or celebration."
        ),
    ),
)

HOOK_REACTIONS: tuple[HookReaction, ...] = (
    HookReaction(
        key="shocked",
        label="wide-eyed silent jaw-drop",
        copy_context="Strong surprise at unexpected information or feedback; no implied positive or negative result.",
        movement=(
            "Her eyebrows lift clearly, her eyes widen and her jaw drops into a silent "
            "gasp. Her head recoils a few centimeters and her shoulders briefly lift. "
            "She stays wide-eyed with the jaw-drop still alive at the cut."
        ),
        expressive=True,
    ),
    HookReaction(
        key="disbelief",
        label="strong incredulous no-way reaction",
        copy_context="Incredulity, contradiction or an unexpectedly confronting practice situation.",
        movement=(
            "Her brows lift unevenly and her eyes open wide in obvious disbelief. "
            "Her lips part into one small silent jaw-drop, then one brow stays raised "
            "as her chin draws back slightly. Her face reads clearly as no way, "
            "without mouthing those words or repeatedly shaking her head."
        ),
        expressive=True,
    ),
    HookReaction(
        key="laughing",
        label="burst of silent amused laughter",
        copy_context="A strongly amusing or self-deprecating situation supported by the overlay and demo.",
        movement=(
            "She breaks into a broad genuine amused grin, cheeks lifting and eyes "
            "creasing naturally. A brief silent laugh moves her shoulders once and "
            "tips her head back slightly. Her teeth can show; she is still amused "
            "at the cut, without rhythmic speech-like mouth movement."
        ),
        expressive=True,
    ),
    HookReaction(
        key="cringing",
        label="obvious embarrassed cringe",
        copy_context="An awkward mistake, embarrassing practice attempt or disappointing visible feedback.",
        movement=(
            "She visibly winces: eyes narrow, nose wrinkles a little, lips pull into "
            "an awkward tight grimace, and her chin and shoulders draw back together. "
            "The whole upper-body recoil expresses embarrassment. Keep that one "
            "cringe readable at the cut; no smile, head turn or face-covering hand."
        ),
        expressive=True,
    ),
    HookReaction(
        key="flustered",
        label="visibly flustered under pressure",
        copy_context=(
            "Time pressure, struggling to find words, or overthinking a practice situation. "
            "Mention a timer or deadline only when the demo actually supports it."
        ),
        movement=(
            "Her brows draw together, her eyes open wider with urgent concentration, "
            "and her lips tighten into an obvious tense grimace. Her shoulders tense "
            "and her head leans toward the recording lens a few centimeters, as "
            "though scrambling to think. The pressure stays readable at the cut. "
            "No typing gesture, phone handling, smile or speech-like mouth movement."
        ),
        expressive=True,
    ),
    HookReaction(
        key="excited",
        label="bright excited surprise",
        copy_context="An exciting small win or positive feedback actually supported by the demonstration.",
        movement=(
            "Her eyes brighten and open wide, her eyebrows rise, and her face breaks "
            "into a big delighted smile with teeth visible. Her shoulders lift and "
            "she leans toward the phone a few centimeters in one spontaneous burst "
            "of excitement. Keep the delight alive at the cut; no speech or repeated bouncing."
        ),
        expressive=True,
    ),
)

_REACTIONS_BY_KEY = {
    reaction.key: reaction for reaction in (*LEGACY_HOOK_REACTIONS, *HOOK_REACTIONS)
}
_ACTIVE_REACTIONS_BY_KEY = {reaction.key: reaction for reaction in HOOK_REACTIONS}


def get_hook_reaction(key: str) -> HookReaction:
    try:
        return _REACTIONS_BY_KEY[key]
    except KeyError as exc:
        raise QualityError(f"unknown daily hook reaction {key!r}") from exc


def get_active_hook_reaction(key: str) -> HookReaction:
    reaction = get_hook_reaction(key)
    if key not in _ACTIVE_REACTIONS_BY_KEY:
        raise QualityError(f"casual hook reaction {key!r} is archived; choose an expressive reaction")
    return reaction


@dataclass(frozen=True)
class ReactionScene:
    key: str
    label: str
    wardrobe_key: str
    framing: str
    setting: str = US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION

    def scene_prompt(self, *, expressive: bool = False) -> str:
        expression = (
            "Her expression is attentive, already at the onset of a strong reaction. "
            "Frame her head and upper chest, keeping her face large enough for the "
            "emotion to read immediately. Her free hand and elbows stay outside the "
            "crop, with room for natural head and shoulder movement. Do not generate a neutral "
            "beauty pose or the finished peak expression."
            if expressive
            else "Her lips are gently closed and her expression is relaxed, ready for one restrained reaction during animation."
        )
        return with_handheld_scene(f"""SILENT REACTION HOOK — STARTING FRAME
{self.framing}
She wears {get_wardrobe(self.wardrobe_key).prompt}.

Her face and upper shoulders are readable, with room above her head for the caption. Keep
the top safe area clear; do not render any text. {expression} Her attention rests near the
recording lens rather than on a household task. The camera-bearing hand and handset stay
beyond the image boundary, and her empty free hand stays outside the crop. Do not bake the
completed reaction into this first frame.
Preserve the approved character's facial features, skin tone, everyday makeup and hair identity.
Use normal skin texture and casual asymmetric posture, not a beauty portrait. Lighting comes
from the actual room or window, with ordinary phone exposure; no studio fill or portrait blur.
Clothing and belongings are ordinary contemporary items, without adding wear to everything.
No visible phone, mirror, reflection, text, logo, other person, new prop or hand-to-face gesture.

{self.setting}
""")


REACTION_SCENES: tuple[ReactionScene, ...] = (
    ReactionScene(
        key="bedroom-bed-edge",
        label="seated on the bed edge",
        wardrobe_key="charcoal-hoodie",
        framing=(
            "She is casually seated on the edge of her bed. The front-camera viewpoint is slightly below "
            "eye level with an incidental crop into one shoulder; she is not making the bed."
        ),
        setting=US_BEDROOM_SETTING_INSTRUCTION,
    ),
    ReactionScene(
        key="kitchen-counter",
        label="standing beside the kitchen counter",
        wardrobe_key="washed-navy-tee",
        framing=(
            "She stands casually beside the kitchen counter, with the front-camera viewpoint a little "
            "closer than a conventional selfie. One shoulder is slightly raised by the "
            "camera arm. She is not handling anything on the counter."
        ),
        setting=US_KITCHEN_SETTING_INSTRUCTION,
    ),
    ReactionScene(
        key="living-room-sofa",
        label="leaning back on the sofa",
        wardrobe_key="forest-sweatshirt",
        framing=(
            "She leans casually back into an ordinary sofa. The front-camera viewpoint is slightly above "
            "her eye line and her head sits a little off-centre. Incidental background "
            "fragments include a throw and a coffee table with a mug."
        ),
    ),
    ReactionScene(
        key="apartment-entry",
        label="paused in the apartment entry",
        wardrobe_key="burgundy-long-sleeve",
        framing=(
            "She has paused in the apartment entry, with an easy asymmetric stance. "
            "Use a head-and-shoulders phone crop with a tiny casual tilt. No door action."
        ),
        setting=US_ENTRYWAY_SETTING_INSTRUCTION,
    ),
    ReactionScene(
        key="bedroom-chair",
        label="sitting sideways in a bedroom chair",
        wardrobe_key="heather-lilac-tee",
        framing=(
            "She sits slightly sideways in an ordinary bedroom chair, with her face still "
            "readable near the recording lens. Frame close enough to crop part of one shoulder. "
            "Include incidental horizontal blinds and the edge of a dresser."
        ),
    ),
    ReactionScene(
        key="parked-car",
        label="sitting in a safely parked car",
        wardrobe_key="olive-street-layers",
        framing=(
            "She sits in a safely parked car, with the front-camera viewpoint slightly off-axis. Her face and one "
            "shoulder fill most of the frame with incidental seat and window edges. "
            "She is not adjusting the seatbelt or driving."
        ),
        setting=(
            "Use an ordinary contemporary U.S.-market car, parked with engine off. "
            "Daylight comes through the windows; a mundane U.S. parking lot or driveway "
            "is only incidentally visible. No road movement or driving."
        ),
    ),
    ReactionScene(
        key="laundry-area",
        label="paused beside the laundry closet",
        wardrobe_key="cream-knit",
        framing=(
            "She has paused beside an ordinary American apartment laundry closet. "
            "The front-camera viewpoint is at a casual height with one shoulder partly cropped. "
            "A washer/dryer edge, detergent and laundry basket appear incidentally. "
            "She is not folding or carrying laundry."
        ),
    ),
)

_SCENES_BY_KEY = {scene.key: scene for scene in REACTION_SCENES}


def get_reaction_scene(key: str) -> ReactionScene:
    try:
        return _SCENES_BY_KEY[key]
    except KeyError as exc:
        raise QualityError(f"unknown reaction hook scene {key!r}") from exc

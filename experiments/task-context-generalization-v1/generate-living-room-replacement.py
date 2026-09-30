"""Replace the living-room start frame that incorrectly showed a phone."""
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


ROOT = Path(__file__).resolve().parent
PROMPT = """
Create one candid vertical 9:16 frame captured from a live front-facing phone camera.
The same woman from the supplied identity reference is holding and looking through
the front camera at arm's length while folding a throw blanket over the back of an
ordinary sofa with her free hand. Her gaze stays on the fabric and her torso leans
slightly into the small task. Neutral, unperformed expression. Simple dark-green
T-shirt. Normal contemporary American apartment living room, partial sofa and coffee
table with a mug and remote in the background. Casual, slightly off-center handheld
crop and ordinary room light.

Camera is the viewer's point of view. Absolutely no phone, camera, device, hand holding
a device, mirror, reflection, photographer, tripod or second person anywhere in frame.
Only the task hand should be visible in the foreground; the camera-holding arm stays
outside the image. Do not create a mirror selfie or an image of someone filming herself.
""".strip()
CAMERA_LOCK = """
DIRECT FRONT-CAMERA VIEW — REQUIRED
Viewer sees through the live front-facing camera the woman herself holds at arm's
length. No phone/device may appear in its own image; no mirrors/reflections, photographer,
second person, tripod or stand. Exactly two arms: camera-holding arm is outside the crop;
free hand performs the task.
""".strip()


def main() -> None:
    settings = Settings.from_env()
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model="gemini-3.1-flash-image",
        timeout=180,
    )
    provider.generate_image(
        PROMPT + "\n\n" + CAMERA_LOCK,
        [Path("/data/characters/master.jpeg")],
        ROOT / "living-room-fold-throw-replacement.png",
        aspect_ratio="9:16",
        image_size="1K",
    )


if __name__ == "__main__":
    main()

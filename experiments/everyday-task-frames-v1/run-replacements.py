"""Replace four mirror/visible-recording-phone stills with direct front-camera frames."""
import json
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


ROOT = Path(__file__).resolve().parent
CAMERA_LOCK = """
SELF-SHOT FRONT CAMERA — REQUIRED
This image is the live front-facing camera view from a phone the woman herself holds
in one extended arm. The phone itself cannot appear because it is the camera producing
the image. There is no photographer, friend, camera operator, tripod, stand or fixed
mount. Show exactly two arms total. The camera arm is mostly outside the crop; at most
one continuous part of its sleeve or forearm may enter along one frame edge. Her other
free hand alone performs the described task. There is no mirror or reflective-surface
selfie, no screen showing the recording, and no second phone or camera except for the
separate task phone explicitly described in the charger scene.
""".strip()


def main() -> None:
    recipes = json.loads((ROOT / "replacements.json").read_text())
    expected = {"pull-from-cabinet", "plug-in-charger", "fold-hoodie-sleeve", "pick-up-keys"}
    if len(recipes) != 4 or {recipe["key"] for recipe in recipes} != expected:
        raise ValueError("Replacement scope must remain the four rejected frames")

    settings = Settings.from_env()
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model="gemini-3.1-flash-image",
        timeout=180,
    )
    master = Path("/data/characters/master.jpeg")

    for recipe in recipes:
        key = recipe["key"]
        output = ROOT / f"{key}-replacement.png"
        if output.is_file():
            print("Already generated:", key, flush=True)
            continue
        marker = ROOT / f"{key}-replacement.submission.json"
        with marker.open("x") as handle:
            json.dump({
                "model": "gemini-3.1-flash-image",
                "image_size": "1K",
                "estimated_image_output_cost_usd": 0.067,
                "replaces": key,
            }, handle)
        print("Generating replacement still:", key, flush=True)
        provider.generate_image(
            recipe["prompt"] + "\n\n" + CAMERA_LOCK,
            [master],
            output,
            aspect_ratio="9:16",
            image_size="1K",
        )
        print("Saved:", output, flush=True)


if __name__ == "__main__":
    main()

"""Generate one task-in-progress still for each of six ordinary contexts."""
import json
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


ROOT = Path(__file__).resolve().parent
CAMERA_LOCK = """
DIRECT FRONT-CAMERA VIEW — REQUIRED
The viewer sees through the live front-facing camera that the woman herself holds at
arm's length. This is not a photograph of her taking a selfie. The recording phone cannot
appear in its own image. No mirror, reflection, photographer, second person, tripod or
camera stand. Exactly two arms: the camera-holding arm is mostly outside the crop; her
other free hand performs the described task. Do not add another phone unless the task
explicitly requires one.
""".strip()


def main() -> None:
    recipes = json.loads((ROOT / "recipes.json").read_text())
    expected = {
        "bedroom-make-bed", "bathroom-hang-towel", "living-room-fold-throw",
        "entryway-tie-shoe", "parked-car-seatbelt", "laundry-fold-towel",
    }
    if len(recipes) != 6 or {recipe["key"] for recipe in recipes} != expected:
        raise ValueError("Generalization batch must contain exactly six contexts")

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
        output = ROOT / f"{key}.png"
        if output.is_file():
            print("Already generated:", key, flush=True)
            continue
        marker = ROOT / f"{key}.submission.json"
        with marker.open("x") as handle:
            json.dump({
                "model": "gemini-3.1-flash-image",
                "image_size": "1K",
                "estimated_image_output_cost_usd": 0.067,
            }, handle)
        print("Generating still:", key, flush=True)
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

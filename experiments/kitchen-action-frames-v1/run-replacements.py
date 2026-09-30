"""Generate replacement stills with an explicit front-camera relationship lock."""
import json
from pathlib import Path

from flagd_ugc.camera_style import HANDHELD_SCENE_INSTRUCTION
from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


def main():
    root = Path(__file__).resolve().parent
    recipes = json.loads((root / "replacements.json").read_text())
    if len(recipes) != 5:
        raise ValueError("Replacement scope must remain exactly five stills")
    settings = Settings.from_env()
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model="gemini-3.1-flash-image",
        timeout=180,
    )
    master = Path("/data/characters/master.jpeg")
    self_shot_lock = (
        "\n\nThis is the direct live view from her own front camera, not a mirror selfie. "
        "The phone is the camera and cannot be visible. There are exactly two arms: "
        "one holds the phone toward the camera and is mostly outside the image; her "
        "other free hand performs the task. No reflective surface, visible phone, "
        "second camera, photographer or other person."
    )
    for recipe in recipes:
        output = root / f"{recipe['key']}.png"
        if output.is_file():
            print("Already generated:", recipe["key"], flush=True)
            continue
        marker = root / f"{recipe['key']}.submission.json"
        with marker.open("x") as handle:
            json.dump({"model": "gemini-3.1-flash-image", "image_size": "1K",
                       "estimated_image_output_cost_usd": 0.067}, handle)
        print("Generating replacement still:", recipe["key"], flush=True)
        provider.generate_image(
            recipe["prompt"] + "\n\n" + HANDHELD_SCENE_INSTRUCTION + self_shot_lock,
            [master], output, aspect_ratio="9:16", image_size="1K",
        )
        print("Saved:", output, flush=True)


if __name__ == "__main__":
    main()

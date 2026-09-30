"""Generate eight task-in-progress stills; this script never submits animations."""
import json
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


def main() -> None:
    root = Path(__file__).resolve().parent
    recipes = json.loads((root / "recipes.json").read_text())
    if len(recipes) != 8:
        raise ValueError("This still-only pass must contain exactly eight candidates")

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
        "The phone filming is outside the image. One arm holds it toward the camera "
        "and is mostly cropped; her other free hand performs the described task. "
        "No mirror, photographer or other person."
    )

    for recipe in recipes:
        key = recipe["key"]
        output = root / f"{key}.png"
        if output.is_file():
            print("Already generated:", key, flush=True)
            continue
        marker = root / f"{key}.submission.json"
        with marker.open("x") as handle:
            json.dump({
                "model": "gemini-3.1-flash-image",
                "image_size": "1K",
                "estimated_image_output_cost_usd": 0.067,
            }, handle)
        print("Generating still:", key, flush=True)
        provider.generate_image(
            recipe["prompt"] + self_shot_lock,
            [master],
            output,
            aspect_ratio="9:16",
            image_size="1K",
        )
        print("Saved:", output, flush=True)


if __name__ == "__main__":
    main()

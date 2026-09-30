"""Generate six task-in-progress kitchen stills; this script never calls animation."""
import json
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


def main():
    root = Path(__file__).resolve().parent
    recipes = json.loads((root / "recipes.json").read_text())
    if len(recipes) != 6:
        raise ValueError("This still-only pass must contain exactly six candidates")
    settings = Settings.from_env()
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model="gemini-3.1-flash-image",
        timeout=180,
    )
    master = Path("/data/characters/master.jpeg")
    for recipe in recipes:
        output = root / f"{recipe['key']}.png"
        if output.is_file():
            print("Already generated:", recipe["key"], flush=True)
            continue
        marker = root / f"{recipe['key']}.submission.json"
        with marker.open("x") as handle:
            json.dump({"model": "gemini-3.1-flash-image", "image_size": "1K",
                       "estimated_image_output_cost_usd": 0.067}, handle)
        print("Generating still:", recipe["key"], flush=True)
        provider.generate_image(
            recipe["prompt"], [master], output,
            aspect_ratio="9:16", image_size="1K",
        )
        print("Saved:", output, flush=True)


if __name__ == "__main__":
    main()

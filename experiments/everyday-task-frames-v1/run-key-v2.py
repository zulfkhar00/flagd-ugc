"""Try a stronger direct-camera POV for the rejected keys frame."""
import json
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


ROOT = Path(__file__).resolve().parent


def main() -> None:
    prompt = json.loads((ROOT / "key-v2.json").read_text())["prompt"]
    settings = Settings.from_env()
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model="gemini-3.1-flash-image",
        timeout=180,
    )
    output = ROOT / "pick-up-keys-replacement-v2.png"
    marker = ROOT / "pick-up-keys-replacement-v2.submission.json"
    if output.is_file():
        print("Already generated:", output, flush=True)
        return
    with marker.open("x") as handle:
        json.dump({
            "model": "gemini-3.1-flash-image",
            "image_size": "1K",
            "estimated_image_output_cost_usd": 0.067,
            "replaces": "pick-up-keys",
        }, handle)
    provider.generate_image(
        prompt,
        [Path("/data/characters/master.jpeg")],
        output,
        aspect_ratio="9:16",
        image_size="1K",
    )
    print("Saved:", output, flush=True)


if __name__ == "__main__":
    main()

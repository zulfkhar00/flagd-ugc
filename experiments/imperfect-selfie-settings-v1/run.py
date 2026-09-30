"""Run the approved three-setting experiment inside the existing container.

Usage: python run.py scenes | animations
Each image and animation submission is reserved on disk before the paid call.
An ambiguous failure stops the run; rerunning cannot silently buy a replacement.
"""
import json
import sys
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset
from flagd_ugc.providers import GeminiProvider


def main():
    phase = sys.argv[1]
    if phase not in {"scenes", "animations"}:
        raise ValueError("choose scenes or animations")
    root = Path(__file__).resolve().parent
    recipes = json.loads((root / "recipes.json").read_text())
    if len(recipes) != 3:
        raise ValueError("approved scope is exactly three settings")
    settings = Settings.from_env()
    require_media_tools()
    provider = FalKlingProvider(
        settings.fal_api_key,
        model="fal-ai/kling-video/v3/standard/image-to-video",
        request_timeout=120,
        poll_seconds=20,
        initial_poll_delay=60,
        job_timeout=1800,
        transient_retries=0,
    )
    provider.check_queue_access()
    if phase == "scenes":
        gemini = GeminiProvider(
            settings.gemini_api_key,
            text_model=settings.gemini_text_model,
            image_model="gemini-3-pro-image",
            timeout=180,
        )
        for recipe in recipes:
            folder = root / recipe["key"]
            folder.mkdir(exist_ok=True)
            target = folder / "scene.png"
            if target.exists():
                validate_vertical_asset(target)
                continue
            with (folder / "image-submission.json").open("x") as handle:
                json.dump({"model": "gemini-3-pro-image", "image_size": "1K",
                           "estimated_image_output_cost_usd": 0.134}, handle)
            print("Generating scene:", recipe["key"], flush=True)
            gemini.generate_image(
                recipe["scene_prompt"], [Path("/data/characters/master.jpeg")],
                target, aspect_ratio="9:16", image_size="1K",
            )
            validate_vertical_asset(target)
            print("Scene complete:", recipe["key"], flush=True)
        return
    if not (root / "scenes-reviewed.txt").is_file():
        raise RuntimeError("Inspect all three starting frames before animation")
    lab = AnimationLab(provider)
    for recipe in recipes:
        folder = root / recipe["key"]
        existing = sorted((folder / "animations").glob("*/manifest.json"))
        if existing:
            if len(existing) != 1:
                raise RuntimeError("Unexpected extra animation runs; inspect manually")
            print(lab.resume(existing[0].parent), flush=True)
            continue
        with (folder / "animation-submission.json").open("x") as handle:
            json.dump({"duration": 3, "estimated_video_cost_usd": 0.252}, handle)
        print("Animating:", recipe["key"], flush=True)
        result = lab.run(
            image=folder / "scene.png", motion_prompt=recipe["motion_prompt"],
            output_root=folder / "animations", duration=3,
        )
        print(result, flush=True)


if __name__ == "__main__":
    main()

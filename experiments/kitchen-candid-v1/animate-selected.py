"""Animate the three user-selected kitchen stills with the unchanged v5 prompt."""
import json
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


APPROVED = {"mid-action-reach", "low-phone-shoulder", "loose-tie-back"}


def main():
    root = Path(__file__).resolve().parent
    recipes = json.loads((root / "recipes.json").read_text())
    selected = [item for item in recipes if item["key"] in APPROVED]
    if {item["key"] for item in selected} != APPROVED or len(selected) != 3:
        raise ValueError("Animation scope must remain the three approved stills")
    prompt = (root.parent / "us-home-scenes-v1" / "motion-prompt-v5-kitchen.txt").read_text().strip()
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
    lab = AnimationLab(provider)
    for item in selected:
        key = item["key"]
        image = root / f"{key}.png"
        if not image.is_file():
            raise FileNotFoundError(f"Selected starting frame missing: {image}")
        validate_vertical_asset(image)
        output_root = root / "selected-animations-v1" / key
        manifests = sorted(output_root.glob("*/manifest.json"))
        if manifests:
            if len(manifests) != 1:
                raise RuntimeError(f"Unexpected duplicate run for {key}")
            print(lab.resume(manifests[0].parent), flush=True)
            continue
        output_root.mkdir(parents=True, exist_ok=True)
        marker = output_root / "animation-submission.json"
        with marker.open("x") as handle:
            json.dump({"duration_seconds": 3, "estimated_cost_usd": 0.252,
                       "model": "fal-ai/kling-video/v3/standard/image-to-video",
                       "motion_prompt_source": "motion-prompt-v5-kitchen.txt"}, handle)
        print("Animating selected still with v5 prompt:", key, flush=True)
        result = lab.run(
            image=image,
            motion_prompt=prompt,
            output_root=output_root,
            duration=3,
        )
        print(result, flush=True)


if __name__ == "__main__":
    main()

"""Test three ultra-minimal motion prompts on the approved kitchen and bedroom stills."""
import json
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


APPROVED = {
    "bedroom-posture": "bedroom-bed-edge",
    "bedroom-brief-lens-glance": "bedroom-bed-edge",
    "kitchen-neutral": "kitchen-counter",
}


def main():
    root = Path(__file__).resolve().parent
    prompts = json.loads((root / "motion-prompts-v3.json").read_text())
    if {item["variant"]: item["scene"] for item in prompts} != APPROVED:
        raise ValueError("Animation scope must remain the three approved variants")
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
    for item in prompts:
        variant, scene = item["variant"], item["scene"]
        image = root / f"{scene}.png"
        if not image.is_file():
            raise FileNotFoundError(f"Approved starting frame missing: {image}")
        validate_vertical_asset(image)
        variant_root = root / "variants-v3" / variant
        manifests = sorted(variant_root.glob("*/manifest.json"))
        if manifests:
            if len(manifests) != 1:
                raise RuntimeError(f"Unexpected multiple v3 runs for {variant}")
            print(lab.resume(manifests[0].parent), flush=True)
            continue
        variant_root.mkdir(parents=True, exist_ok=True)
        marker = variant_root / "animation-submission.json"
        with marker.open("x") as handle:
            json.dump({"duration_seconds": 3, "estimated_cost_usd": 0.252,
                       "model": "fal-ai/kling-video/v3/standard/image-to-video"}, handle)
        print("Animating existing still:", variant, flush=True)
        result = lab.run(
            image=image,
            motion_prompt=item["prompt"],
            output_root=variant_root,
            duration=3,
        )
        print(result, flush=True)


if __name__ == "__main__":
    main()

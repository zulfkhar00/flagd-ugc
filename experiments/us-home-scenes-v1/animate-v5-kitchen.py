"""Final three-second no-reaction test on the existing kitchen starting still."""
import json
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


def main():
    root = Path(__file__).resolve().parent
    image = root / "kitchen-counter.png"
    if not image.is_file():
        raise FileNotFoundError(image)
    validate_vertical_asset(image)
    prompt = (root / "motion-prompt-v5-kitchen.txt").read_text().strip()
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
    output_root = root / "variants-v5" / "kitchen-no-reaction"
    manifests = sorted(output_root.glob("*/manifest.json"))
    lab = AnimationLab(provider)
    if manifests:
        if len(manifests) != 1:
            raise RuntimeError("Unexpected duplicate v5 jobs; inspect before continuing")
        print(lab.resume(manifests[0].parent), flush=True)
        return
    output_root.mkdir(parents=True, exist_ok=True)
    marker = output_root / "animation-submission.json"
    with marker.open("x") as handle:
        json.dump({"duration_seconds": 3, "estimated_cost_usd": 0.252,
                   "model": "fal-ai/kling-video/v3/standard/image-to-video"}, handle)
    print("Animating existing kitchen still", flush=True)
    result = lab.run(
        image=image,
        motion_prompt=prompt,
        output_root=output_root,
        duration=3,
    )
    print(result, flush=True)


if __name__ == "__main__":
    main()

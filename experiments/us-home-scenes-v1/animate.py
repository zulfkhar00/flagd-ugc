"""Animate only the four user-approved U.S. home starting frames."""
import json
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


APPROVED = {
    "laundry-area",
    "kitchen-counter",
    "bedroom-bed-edge",
    "living-room-sofa",
}


def main():
    root = Path(__file__).resolve().parent
    prompts = json.loads((root / "motion-prompts.json").read_text())
    if {item["key"] for item in prompts} != APPROVED or len(prompts) != 4:
        raise ValueError("Animation scope must remain the four approved scenes")
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
        key = item["key"]
        image = root / f"{key}.png"
        if not image.is_file():
            raise FileNotFoundError(f"Approved starting frame missing: {image}")
        validate_vertical_asset(image)
        folder = root / key
        output_root = folder / "animations"
        manifests = sorted(output_root.glob("*/manifest.json"))
        if manifests:
            if len(manifests) != 1:
                raise RuntimeError(f"Unexpected multiple runs for {key}; inspect first")
            print(lab.resume(manifests[0].parent), flush=True)
            continue
        marker = folder / "animation-submission.json"
        folder.mkdir(exist_ok=True)
        with marker.open("x") as handle:
            json.dump({"duration_seconds": 4,
                       "estimated_cost_usd": 0.336,
                       "model": "fal-ai/kling-video/v3/standard/image-to-video"}, handle)
        print("Animating approved still:", key, flush=True)
        result = lab.run(
            image=image,
            motion_prompt=item["prompt"],
            output_root=output_root,
            duration=4,
        )
        print(result, flush=True)


if __name__ == "__main__":
    main()

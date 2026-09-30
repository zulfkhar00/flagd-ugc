"""Animate four selected everyday-task frames with restrained task continuation."""
import json
from pathlib import Path
from string import Template

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


MODEL = "fal-ai/kling-video/v3/standard/image-to-video"
ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "motion-prompts.json"
MOTION_TEMPLATE = (ROOT / "task-driven-motion-v1.txt").read_text()


def motion_prompt(action: str) -> str:
    return Template(MOTION_TEMPLATE).substitute(task_continuation=action)


def main() -> None:
    items = json.loads(PROMPTS.read_text())
    expected = {"open-fridge", "mug-into-sink", "wipe-counter", "move-grocery-bag"}
    if len(items) != 4 or {item["key"] for item in items} != expected:
        raise ValueError("Animation scope must remain the four selected task frames")

    settings = Settings.from_env()
    require_media_tools()
    provider = FalKlingProvider(
        settings.fal_api_key,
        model=MODEL,
        request_timeout=120,
        poll_seconds=20,
        initial_poll_delay=60,
        job_timeout=1800,
        transient_retries=0,
    )
    provider.check_queue_access()
    lab = AnimationLab(provider)

    for item in items:
        key = item["key"]
        image = (ROOT / item["image"]).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Selected starting frame missing: {image}")
        validate_vertical_asset(image)
        output_root = ROOT / "outputs" / key
        manifests = sorted(output_root.glob("*/manifest.json"))
        if manifests:
            if len(manifests) != 1:
                raise RuntimeError(f"Unexpected duplicate runs for {key}")
            print(lab.resume(manifests[0].parent), flush=True)
            continue
        output_root.mkdir(parents=True, exist_ok=True)
        with (output_root / "animation-submission.json").open("x") as handle:
            json.dump({
                "duration_seconds": 3,
                "estimated_cost_usd": 0.252,
                "model": MODEL,
                "motion_prompt_source": "lean-into-drawer minimal task-continuation baseline",
                "source_image": str(image),
            }, handle, indent=2)
        print("Animating selected task frame:", key, flush=True)
        print(lab.run(
            image=image,
            motion_prompt=motion_prompt(item["action"]),
            output_root=output_root,
            duration=3,
        ), flush=True)


if __name__ == "__main__":
    main()

"""Animate the four user-selected, physically occupied kitchen frames."""
import json
from pathlib import Path

from flagd_ugc.animation_lab import AnimationLab
from flagd_ugc.config import Settings
from flagd_ugc.fal_provider import FalKlingProvider
from flagd_ugc.media import require_media_tools, validate_vertical_asset


MODEL = "fal-ai/kling-video/v3/standard/image-to-video"
ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts.json"


def motion_prompt(action: str) -> str:
    return f"""This is a random three-second excerpt from a longer casual front-camera recording. Nothing noteworthy happens during these three seconds.

Her face orientation remains essentially unchanged throughout the shot. She does not deliberately look away or turn her head toward anything. Her eyes make only tiny unconscious gaze adjustments near the lens. One natural blink may occur. Her mouth stays completely relaxed and does not form a smile, smirk, frown, or other reaction.

Continue only the small physical task already underway in the starting frame: {action} These movements stay slight, ordinary, and unperformed. Her hair responds only subtly to the body movement. The phone hand drifts minimally with an irregular natural wrist movement, causing tiny changes in framing and background perspective. Keep the task movement and phone drift independent; do not choreograph them as a sequence.

No reaction, no deliberate head turn, no look-away action, no completed expression, no new task, no ending pose. The clip simply stops in the middle of the ordinary action."""


def main() -> None:
    items = json.loads(PROMPTS.read_text())
    expected = {"lean-into-drawer", "adjust-coffee-maker", "reach-for-mug", "sit-at-island"}
    if len(items) != 4 or {item["key"] for item in items} != expected:
        raise ValueError("Animation scope must remain the four selected frames")

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
                raise RuntimeError(f"Unexpected duplicate run for {key}")
            print(lab.resume(manifests[0].parent), flush=True)
            continue
        output_root.mkdir(parents=True, exist_ok=True)
        marker = output_root / "animation-submission.json"
        with marker.open("x") as handle:
            json.dump({
                "duration_seconds": 3,
                "estimated_cost_usd": 0.252,
                "model": MODEL,
                "motion_prompt_source": "prompts.json plus kitchen-no-reaction-v5 baseline",
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

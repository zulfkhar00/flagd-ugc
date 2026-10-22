"""Compare two phone-agent traces without exposing screen content.

Usage: python phone-agent/scripts/compare_perception.py BASELINE_RUN PILOT_RUN
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def summarize(directory: Path) -> dict[str, object]:
    path = directory / "trace.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    decisions = [item for item in events if item.get("event") == "decision"]
    requested = {item.get("step"): item.get("time") for item in events
                 if item.get("event") == "model_requested" and item.get("step") is not None}
    observations = [item for item in events if item.get("event") == "observation"]
    semantic = [item for item in events if item.get("event") == "semantic_observation"]
    unavailable = [item for item in events if item.get("event") == "semantic_unavailable"]
    result = next((item for item in reversed(events) if item.get("event") == "run_finished"), {})
    start = next((item for item in events if item.get("event") == "run_started"), {})
    usages = [request.get("usage", {}) for decision in decisions
              for request in decision.get("requests", [decision])]
    # A run may include verification calls outside decision events. The
    # decision counts here are deliberately scoped to perception changes.
    return {
        "run": directory.name,
        "status": result.get("status", "unknown"),
        "elapsed_s": round(result.get("time", 0) - start.get("time", 0), 1),
        "decisions": len(decisions),
        "semantic_decisions": sum(item.get("perception") == "semantic" for item in decisions),
        "vision_fallbacks": sum(item.get("perception") == "vision_fallback" for item in decisions),
        "decision_model_requests": sum(item.get("api_calls", 1) for item in decisions),
        "decision_wall_s": round(sum(item["time"] - requested[item["step"]]
                                     for item in decisions if item.get("step") in requested), 1),
        "prompt_tokens": sum(item.get("promptTokenCount", 0) for item in usages),
        "output_tokens": sum(item.get("candidatesTokenCount", 0) for item in usages),
        "image_prompt_tokens": sum(part.get("tokenCount", 0) for usage in usages
                                   for part in usage.get("promptTokensDetails", [])
                                   if part.get("modality") == "IMAGE"),
        "screenshot_count": len(observations),
        "screenshot_portal_s": round(sum(item.get("portal_screenshot_ms", 0)
                                         for item in observations) / 1000, 1),
        "state_count": len(semantic),
        "state_unavailable": len(unavailable),
        "state_portal_s": round(sum(item.get("portal_state_ms", 0)
                                    for item in [*semantic, *unavailable]) / 1000, 1),
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    for name in sys.argv[1:]:
        print(json.dumps(summarize(Path(name)), indent=2))


if __name__ == "__main__":
    main()

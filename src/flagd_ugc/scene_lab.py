from __future__ import annotations

import html
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any
from uuid import uuid4

from .errors import AssetError, QualityError
from .media import validate_vertical_asset
from .retry import retry_transient
from .camera_style import (
    US_BEDROOM_SETTING_INSTRUCTION,
    US_ENTRYWAY_SETTING_INSTRUCTION,
    US_KITCHEN_SETTING_INSTRUCTION,
    US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION,
    US_STREET_SETTING_INSTRUCTION,
)
from .scenarios import DEFAULT_SCENARIO, get_scenario
from .wardrobes import get_wardrobe


def default_scene_prompt() -> str:
    return get_scenario(DEFAULT_SCENARIO).scene_prompt()


def default_motion_prompt() -> str:
    return get_scenario(DEFAULT_SCENARIO).motion_prompt()


def estimated_image_cost(model: str, image_size: str) -> float | None:
    prices = {
        ("gemini-3-pro-image", "1K"): 0.134,
        ("gemini-3-pro-image", "2K"): 0.134,
        ("gemini-3-pro-image", "4K"): 0.24,
        ("gemini-3.1-flash-image", "1K"): 0.067,
        ("gemini-3.1-flash-image", "2K"): 0.101,
        ("gemini-3.1-flash-image", "4K"): 0.151,
    }
    return prices.get((model, image_size.upper()))


@dataclass(frozen=True)
class Candidate:
    file: str
    source: str
    status: str
    error: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class SceneLabResult:
    run_dir: Path
    report_path: Path
    manifest_path: Path
    candidates: tuple[Candidate, ...]

    @property
    def generated_successfully(self) -> int:
        return sum(
            candidate.source == "generated" and candidate.status == "ready"
            for candidate in self.candidates
        )


class SceneLab:
    def __init__(self, provider: Any | None, *, transient_retries: int = 3):
        self.provider = provider
        self.transient_retries = transient_retries

    def run(
        self,
        *,
        master: Path,
        output_root: Path,
        samples: int,
        model: str,
        scenario: str = DEFAULT_SCENARIO,
        wardrobe: str | None = None,
        prompt: str | None = None,
        motion_prompt: str | None = None,
        imported_images: list[Path] | None = None,
        dry_run: bool = False,
        image_size: str = "2K",
    ) -> SceneLabResult:
        master = master.expanduser().resolve()
        if not master.is_file():
            raise AssetError(f"master reference is missing: {master}")
        imported_images = [
            path.expanduser().resolve() for path in imported_images or []
        ]
        missing_imports = [str(path) for path in imported_images if not path.is_file()]
        if missing_imports:
            raise AssetError(
                "imported candidates are missing: " + ", ".join(missing_imports)
            )
        if samples < 0:
            raise ValueError("samples must not be negative")
        if samples == 0 and not imported_images:
            raise ValueError("request at least one sample or import a candidate")
        if samples and not dry_run and self.provider is None:
            raise ValueError("an image provider is required for generation")

        scenario_definition = get_scenario(scenario)
        wardrobe_definition = get_wardrobe(
            wardrobe or scenario_definition.default_wardrobe
        )
        scene_prompt = (
            prompt or scenario_definition.scene_prompt(wardrobe_definition.key)
        ).strip()
        if prompt is not None:
            scene_prompt = (
                f"{scene_prompt}\n\n{US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION}"
            )
            if scenario_definition.key in {
                "bedroom-seated", "bed-reclined", "bed-facepalm"
            }:
                scene_prompt = (
                    f"{scene_prompt}\n\n{US_BEDROOM_SETTING_INSTRUCTION}"
                )
            if scenario_definition.key == "kitchen-cover-mouth":
                scene_prompt = (
                    f"{scene_prompt}\n\n{US_KITCHEN_SETTING_INSTRUCTION}"
                )
            if scenario_definition.key == "doorway-backout":
                scene_prompt = (
                    f"{scene_prompt}\n\n{US_ENTRYWAY_SETTING_INSTRUCTION}"
                )
            if scenario_definition.key == "city-walk":
                scene_prompt = (
                    f"{scene_prompt}\n\n{US_STREET_SETTING_INSTRUCTION}"
                )
        animation_prompt = (
            motion_prompt or scenario_definition.motion_prompt()
        ).strip()
        prompt_version = scenario_definition.prompt_version
        if prompt is not None or motion_prompt is not None:
            prompt_version += "-custom"
        now = datetime.now(UTC)
        run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{prompt_version}-{uuid4().hex[:6]}"
        run_dir = output_root.expanduser().resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        master_name = f"master{master.suffix.lower()}"
        shutil.copy2(master, run_dir / master_name)
        (run_dir / "SCENE_PROMPT.txt").write_text(scene_prompt + "\n", encoding="utf-8")
        (run_dir / "MOTION_PROMPT.txt").write_text(
            animation_prompt + "\n", encoding="utf-8"
        )

        candidates: list[Candidate] = []
        for index, source in enumerate(imported_images, start=1):
            destination = run_dir / f"imported-{index:02d}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            candidates.append(self._inspect(destination, source="imported"))

        if not dry_run:
            assert self.provider is not None
            for index in range(1, samples + 1):
                output = run_dir / f"generated-{index:02d}.png"
                try:
                    retry_transient(
                        lambda output=output: self.provider.generate_image(
                            scene_prompt,
                            [master],
                            output,
                            aspect_ratio="9:16",
                            image_size=image_size,
                        ),
                        retries=self.transient_retries,
                    )
                    candidates.append(self._inspect(output, source="generated"))
                except Exception as exc:
                    candidates.append(
                        Candidate(
                            file=output.name,
                            source="generated",
                            status="failed",
                            error=str(exc),
                        )
                    )

        unit_cost = estimated_image_cost(model, image_size)
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": now.isoformat(),
            "scenario": scenario_definition.key,
            "scenario_label": scenario_definition.label,
            "wardrobe": wardrobe_definition.key,
            "wardrobe_label": wardrobe_definition.label,
            "prompt_version": prompt_version,
            "model": model,
            "aspect_ratio": "9:16",
            "image_size": image_size,
            "mode": "dry-run" if dry_run else "standard-api",
            "master_source": str(master),
            "master_file": master_name,
            "requested_samples": samples,
            "unit_output_cost_usd": unit_cost,
            "estimated_generation_cost_usd": (
                round(unit_cost * samples, 4)
                if unit_cost is not None and not dry_run
                else 0
            ),
            "scene_prompt_file": "SCENE_PROMPT.txt",
            "motion_prompt_file": "MOTION_PROMPT.txt",
            "candidates": [asdict(candidate) for candidate in candidates],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report_path = run_dir / "report.html"
        report_path.write_text(
            _render_report(manifest, scene_prompt, animation_prompt), encoding="utf-8"
        )
        return SceneLabResult(
            run_dir,
            report_path,
            manifest_path,
            tuple(candidates),
        )

    @staticmethod
    def _inspect(path: Path, *, source: str) -> Candidate:
        try:
            width, height = validate_vertical_asset(path)
            return Candidate(path.name, source, "ready", width=width, height=height)
        except QualityError as exc:
            return Candidate(path.name, source, "invalid", error=str(exc))


def _render_report(manifest: dict[str, Any], prompt: str, motion_prompt: str) -> str:
    candidates = manifest["candidates"]
    cards: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        filename = html.escape(candidate["file"])
        status = html.escape(candidate["status"])
        dimensions = (
            f"{candidate['width']}×{candidate['height']}"
            if candidate.get("width") and candidate.get("height")
            else "dimensions unavailable"
        )
        cards.append(
            f"""
            <article class="card" data-file="{filename}">
              <img src="{filename}" alt="Candidate {index}">
              <div class="body">
                <div class="row"><strong>{filename}</strong><span class="status {status}">{status}</span></div>
                <small>{html.escape(candidate["source"])} · {dimensions}</small>
                <fieldset>
                  <legend>Hard gates</legend>
                  <label><input type="checkbox" data-field="identity"> Same unmistakable identity</label>
                  <label><input type="checkbox" data-field="selfie"> Clearly her own front-camera selfie</label>
                  <label><input type="checkbox" data-field="realism"> Looks like ordinary phone footage</label>
                  <label><input type="checkbox" data-field="frame_zero"> Reaction has not already happened</label>
                  <label><input type="checkbox" data-field="motion_ready"> Can complete the motion in 3 seconds</label>
                  <label><input type="checkbox" data-field="anatomy"> Face, body and hands are defect-free</label>
                  <label><input type="checkbox" data-field="caption"> Face remains clear above caption area</label>
                </fieldset>
                <label class="score">Decision
                  <select data-field="decision">
                    <option value="">Unreviewed</option>
                    <option value="accept">Accept</option>
                    <option value="maybe">Maybe</option>
                    <option value="reject">Reject</option>
                  </select>
                </label>
                <label class="notes">Notes<textarea data-field="notes" rows="3" placeholder="What drifted or looked artificial?"></textarea></label>
              </div>
            </article>
            """
        )
    if not cards:
        cards.append(
            '<p class="empty">Prompt-only run. Add <code>--confirm-paid</code> to generate samples.</p>'
        )
    template = Template(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>flagd scene lab · $run_id</title>
  <style>
    :root { color-scheme: dark; --bg:#0e1013; --panel:#171a1f; --line:#303640; --text:#f2f4f7; --muted:#a8b0bd; --green:#62d29f; }
    * { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 ui-sans-serif,system-ui,sans-serif }
    header,main { width:min(1500px,94vw); margin:auto } header { padding:32px 0 20px } h1,h2 { margin:.2em 0 } p { color:var(--muted) }
    .overview { display:grid; grid-template-columns:minmax(220px,360px) 1fr; gap:24px; align-items:start }
    .master,.panel,.card { background:var(--panel); border:1px solid var(--line); border-radius:14px; overflow:hidden }
    .master img { display:block; width:100%; max-height:580px; object-fit:cover } .panel { padding:20px }
    .gates { columns:2; padding-left:20px } .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(290px,1fr)); gap:20px; margin:18px 0 40px }
    .card img { width:100%; aspect-ratio:9/16; object-fit:cover; display:block; background:#090a0c } .body { padding:16px }
    .row { display:flex; justify-content:space-between; gap:12px } small { color:var(--muted) } .status { text-transform:uppercase; font-size:11px; letter-spacing:.08em }
    .ready { color:var(--green) } .invalid,.failed { color:#ff8d8d } fieldset { border:0; padding:14px 0; display:grid; gap:8px }
    legend { font-weight:700; margin-bottom:8px } fieldset label { display:flex; gap:8px; align-items:flex-start }
    select,textarea { width:100%; margin-top:6px; background:#0f1115; color:var(--text); border:1px solid var(--line); border-radius:7px; padding:9px }
    .notes,.score { display:block; margin-top:12px } details { margin:12px 0 } pre { white-space:pre-wrap; color:#dce2ea; background:#0f1115; padding:16px; border-radius:8px; max-height:480px; overflow:auto }
    button { background:var(--green); border:0; border-radius:8px; padding:11px 16px; font-weight:700; cursor:pointer } .empty { padding:30px }
    @media(max-width:760px) { .overview { grid-template-columns:1fr } .gates { columns:1 } }
  </style>
</head>
<body>
<header><small>FLAGD · SCENE LAB · $prompt_version</small><h1>$scenario_label</h1><p>Wardrobe: $wardrobe_label. Review the still as the beginning of a three-second performance—not as a standalone portrait.</p></header>
<main>
  <section class="overview">
    <figure class="master"><img src="$master_file" alt="Master identity reference"></figure>
    <div class="panel">
      <h2>Acceptance rule</h2>
      <p>A candidate passes only when every hard gate is checked. One attractive image is not enough; production readiness requires at least 3 accepted images from 4 generations of the same prompt.</p>
      <ul class="gates"><li>Identity is unmistakable</li><li>Clearly self-shot with her own front camera</li><li>Human, non-polished realism</li><li>Frame zero precedes the reaction</li><li>Motion is achievable in 3 seconds</li><li>No anatomy or background defects</li><li>Caption will not cover the face</li></ul>
      <button id="download">Download review JSON</button>
      <details><summary>Scene-generation prompt</summary><pre>$scene_prompt</pre></details>
      <details><summary>Paired three-second motion prompt</summary><pre>$motion_prompt</pre></details>
    </div>
  </section>
  <h2>Candidates</h2>
  <section class="grid">$cards</section>
</main>
<script>
  const runId = $run_id_json;
  const key = `flagd-scene-lab:$${runId}`;
  const cards = [...document.querySelectorAll('.card')];
  const load = () => JSON.parse(localStorage.getItem(key) || '{}');
  const collect = () => Object.fromEntries(cards.map(card => [card.dataset.file,
    Object.fromEntries([...card.querySelectorAll('[data-field]')].map(input => [input.dataset.field, input.type === 'checkbox' ? input.checked : input.value]))
  ]));
  const saved = load();
  cards.forEach(card => [...card.querySelectorAll('[data-field]')].forEach(input => {
    const value = saved[card.dataset.file]?.[input.dataset.field];
    if (value !== undefined) input.type === 'checkbox' ? input.checked = value : input.value = value;
    input.addEventListener('input', () => localStorage.setItem(key, JSON.stringify(collect())));
  }));
  document.getElementById('download').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify({run_id:runId,reviews:collect()}, null, 2)], {type:'application/json'});
    const link = Object.assign(document.createElement('a'), {href:URL.createObjectURL(blob), download:'review.json'});
    link.click(); URL.revokeObjectURL(link.href);
  });
</script>
</body></html>"""
    )
    return template.substitute(
        run_id=html.escape(manifest["run_id"]),
        run_id_json=json.dumps(manifest["run_id"]),
        prompt_version=html.escape(manifest["prompt_version"]),
        scenario_label=html.escape(manifest["scenario_label"]),
        wardrobe_label=html.escape(manifest["wardrobe_label"]),
        master_file=html.escape(manifest["master_file"]),
        scene_prompt=html.escape(prompt),
        motion_prompt=html.escape(motion_prompt),
        cards="".join(cards),
    )

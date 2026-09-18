from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assets import Demo, load_demos
from .errors import AssetError, QualityError
from .hook_formats import hook_format_keys, visible_hook_text
from .hooks import validate_hook
from .media import MediaComposer, ProbeResult, validate_output, validate_vertical_asset


@dataclass(frozen=True)
class PreviewVideo:
    opening: Path
    demo: Demo
    hook: str
    hook_format: str
    output_name: str


@dataclass(frozen=True)
class PreviewResult:
    output_dir: Path
    videos: tuple[Path, ...]
    report_path: Path
    manifest_path: Path


def _read_plan(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"invalid batch preview plan {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssetError("batch preview plan must be a JSON object")
    return payload


def _resolve(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AssetError(f"{label} must be a non-empty path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def load_preview_plan(plan_path: Path) -> list[PreviewVideo]:
    """Load a four-video local preview plan without touching providers or the database."""
    plan_path = plan_path.expanduser().resolve()
    payload = _read_plan(plan_path)
    root = _resolve(payload.get("asset_root", "."), plan_path.parent, "asset_root")
    demos_dir = _resolve(payload.get("demos_dir", "data/demos"), root, "demos_dir")
    demos = {str(demo.path.resolve()): demo for demo in load_demos(demos_dir)}
    configured = payload.get("videos")
    if not isinstance(configured, list) or len(configured) != 4:
        raise AssetError("batch preview plan must contain exactly four videos")

    preview_videos: list[PreviewVideo] = []
    seen_formats: set[str] = set()
    seen_demos: set[str] = set()
    for ordinal, item in enumerate(configured, start=1):
        if not isinstance(item, dict):
            raise AssetError(f"preview video #{ordinal} must be an object")
        opening = _resolve(item.get("opening"), root, f"preview video #{ordinal} opening")
        demo_path = _resolve(item.get("demo"), root, f"preview video #{ordinal} demo")
        if not opening.is_file():
            raise AssetError(f"preview opening is missing: {opening}")
        if str(demo_path) not in demos:
            raise AssetError(
                f"preview demo is not an approved file in {demos_dir}: {demo_path}"
            )
        hook_format = str(item.get("hook_format", ""))
        if hook_format not in hook_format_keys():
            raise AssetError(
                f"preview video #{ordinal} has unknown hook_format {hook_format!r}"
            )
        if hook_format in seen_formats:
            raise QualityError(
                f"preview batch repeats hook format {hook_format!r}; choose four distinct formats"
            )
        if str(demo_path) in seen_demos:
            raise QualityError(
                f"preview batch repeats demo {demo_path.name}; choose four distinct demos"
            )
        demo = demos[str(demo_path)]
        hook = validate_hook(
            str(item.get("hook", "")),
            hook_format=hook_format,
            demo_description=demo.description,
        )
        output_name = f"{ordinal:02d}-{hook_format}.mp4"
        preview_videos.append(
            PreviewVideo(opening, demo, hook, hook_format, output_name)
        )
        seen_formats.add(hook_format)
        seen_demos.add(str(demo_path))
    return preview_videos


def _report_html(videos: list[dict[str, Any]]) -> str:
    cards = "".join(
        f"""
        <article>
          <video controls playsinline preload=\"metadata\" src=\"{html.escape(item['file'])}\"></video>
          <h2>{item['ordinal']}. {html.escape(item['hook_format'])}</h2>
          <p>{html.escape(item['visible_hook'])}</p>
          <p class=\"muted\">{html.escape(item['demo_file'])}</p>
        </article>
        """
        for item in videos
    )
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>flagd local batch preview</title>
<style>
  :root {{ color-scheme: dark; --bg:#0e0f12; --panel:#181a20; --line:#2b2e37; --muted:#aeb5c3; }}
  * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#f4f6fa; font:15px/1.45 ui-sans-serif,system-ui,sans-serif; }}
  header,main {{ width:min(1320px,94vw); margin:auto; }} header {{ padding:30px 0 18px; }} h1 {{ margin:0 0 8px; }} p {{ margin:6px 0; }} .muted {{ color:var(--muted); }}
  main {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:16px; padding-bottom:32px; }}
  article {{ padding:12px; border:1px solid var(--line); background:var(--panel); border-radius:14px; }}
  video {{ width:100%; display:block; aspect-ratio:9/16; background:#050607; border-radius:9px; }} h2 {{ font-size:15px; margin:12px 0 2px; text-transform:none; }}
</style></head><body><header><small>FLAGD · LOCAL BATCH PREVIEW</small><h1>Four silent hook-and-demo prototypes</h1><p class=\"muted\">No providers, music, database writes, Telegram, or deployment.</p></header><main>{cards}</main></body></html>"""


def render_batch_preview(
    plan_path: Path,
    output_dir: Path,
    font_file: Path,
    *,
    composer: MediaComposer | None = None,
) -> PreviewResult:
    """Render all four planned videos and a simple browser review page."""
    plan = load_preview_plan(plan_path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    composer = composer or MediaComposer(font_file)
    rendered: list[Path] = []
    manifest_videos: list[dict[str, Any]] = []
    for ordinal, item in enumerate(plan, start=1):
        validate_vertical_asset(item.opening)
        validate_vertical_asset(item.demo.path)
        output = output_dir / item.output_name
        composer.compose(
            item.opening,
            item.demo.path,
            None,
            item.hook,
            output,
            hook_format=item.hook_format,
        )
        probe: ProbeResult = validate_output(output, require_audio=False)
        rendered.append(output)
        manifest_videos.append(
            {
                "ordinal": ordinal,
                "file": output.name,
                "opening": str(item.opening),
                "demo_file": item.demo.path.name,
                "hook": item.hook,
                "visible_hook": visible_hook_text(item.hook, item.hook_format),
                "hook_format": item.hook_format,
                "probe": probe.as_dict(),
            }
        )
    manifest_path = output_dir / "manifest.json"
    temporary_manifest = output_dir / ".manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps({"version": 1, "silent": True, "videos": manifest_videos}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_path)
    report_path = output_dir / "report.html"
    temporary_report = output_dir / ".report.html.tmp"
    temporary_report.write_text(_report_html(manifest_videos), encoding="utf-8")
    os.replace(temporary_report, report_path)
    return PreviewResult(output_dir, tuple(rendered), report_path, manifest_path)

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .assets import Demo, load_demos
from .errors import AssetError, QualityError
from .hook_formats import HookFormat, assign_hook_formats, visible_hook_text
from .hooks import validate_hook, validate_title


@dataclass(frozen=True)
class HookCopyLabResult:
    output_dir: Path
    report_path: Path
    manifest_path: Path
    status: str


def _html_report(items: list[dict[str, Any]], *, status: str, run_date: date) -> str:
    cards = "".join(
        f"""
        <article>
          <p class=\"format\">{html.escape(item['hook_format_name'])}</p>
          <p class=\"title\">{html.escape(item['title'])}</p>
          <p class=\"hook\">{html.escape(item['visible_hook'])}</p>
          <p class=\"demo\">{html.escape(item['demo_file'])}</p>
          <p class=\"description\">{html.escape(item['demo_description'])}</p>
        </article>
        """
        for item in items
    )
    generated_note = (
        "Gemini generated and safety-reviewed these four copy options."
        if status == "complete"
        else "Dry run only: these are format examples, not Gemini-generated copy."
    )
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>flagd hook copy review</title>
<style>
  :root {{ color-scheme:dark; --bg:#0e0f12; --panel:#181a20; --line:#2b2e37; --muted:#aeb5c3; --accent:#c7adff; }}
  * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#f4f6fa; font:16px/1.45 ui-sans-serif,system-ui,sans-serif; }}
  header,main {{ width:min(980px,94vw); margin:auto; }} header {{ padding:34px 0 20px; }} h1 {{ margin:0 0 8px; }}
  .muted,.description {{ color:var(--muted); }} main {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; padding-bottom:34px; }}
  article {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }} p {{ margin:0; }}
  .format {{ color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }}
  .title {{ margin:14px 0 0; color:#d8dde8; font-size:13px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; }}
  .hook {{ font-size:25px; line-height:1.15; font-weight:750; margin:7px 0 18px; }} .demo {{ font-weight:650; margin-bottom:5px; }}
</style></head><body><header><small>FLAGD · HOOK COPY LAB</small><h1>Four-format copy review · {run_date.isoformat()}</h1><p class=\"muted\">{html.escape(generated_note)}</p></header><main>{cards}</main></body></html>"""


class HookCopyLab:
    """A deliberately narrow, text-only hook generation experiment."""

    def __init__(self, provider: Any | None = None, *, quality_retries: int = 2):
        self.provider = provider
        self.quality_retries = quality_retries

    @staticmethod
    def _example_title(format_key: str, description: str) -> str:
        """Safe titles for a local dry-run review, without a provider call."""
        if format_key == "scorecard":
            score = re.search(
                r"\b(100|[1-9]\d?)\s+(?:out\s+of\s+)?100\b", description
            )
            if score:
                return f"the {score.group(1)} conversation score"
            return "the conversation score"
        return {
            "inner-thought": "practice date feedback",
            "pov": "a practice date result",
            "quote-receipt": "his texting confidence",
            "when": "the practice date feedback",
            "direct-observation": "the conversation feedback",
            "reaction-meme": "the practice date feedback",
        }[format_key]

    @staticmethod
    def _ordered_items(
        assignments: list[tuple[Demo, HookFormat]], generated: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        by_demo = {
            str(Path(item["demo_path"]).resolve()): item for item in generated
        }
        if len(by_demo) != len(assignments):
            raise QualityError("Gemini returned duplicate or missing hook demos")
        items: list[dict[str, Any]] = []
        for ordinal, (demo, format_definition) in enumerate(assignments, start=1):
            generated_item = by_demo.get(str(demo.path.resolve()))
            if generated_item is None:
                raise QualityError(f"Gemini omitted the assigned demo {demo.path.name}")
            if generated_item.get("hook_format") != format_definition.key:
                raise QualityError(f"Gemini changed the format for {demo.path.name}")
            hook = validate_hook(
                str(generated_item["hook"]),
                hook_format=format_definition.key,
                demo_description=demo.description,
            )
            title = validate_title(
                str(generated_item["title"]), demo_description=demo.description
            )
            if title.casefold() in {
                hook.casefold(),
                visible_hook_text(hook, format_definition.key).casefold(),
            }:
                raise QualityError("upload title must be separate from the on-video hook")
            items.append(
                {
                    "ordinal": ordinal,
                    "demo_file": demo.path.name,
                    "demo_description": demo.description,
                    "hook_format": format_definition.key,
                    "hook_format_name": format_definition.name,
                    "hook": hook,
                    "title": title,
                    "visible_hook": visible_hook_text(hook, format_definition.key),
                }
            )
        return items

    def run(
        self,
        demos_dir: Path,
        run_date: date,
        output_dir: Path,
        *,
        dry_run: bool = False,
    ) -> HookCopyLabResult:
        demos = load_demos(demos_dir.expanduser().resolve())
        assignments = assign_hook_formats(demos, 4, run_date)
        if dry_run:
            items = [
                {
                    "ordinal": ordinal,
                    "demo_file": demo.path.name,
                    "demo_description": demo.description,
                    "hook_format": format_definition.key,
                    "hook_format_name": format_definition.name,
                    "hook": format_definition.example,
                    "title": self._example_title(
                        format_definition.key, demo.description
                    ),
                    "visible_hook": visible_hook_text(
                        format_definition.example, format_definition.key
                    ),
                }
                for ordinal, (demo, format_definition) in enumerate(assignments, start=1)
            ]
            status = "dry-run"
        else:
            if self.provider is None:
                raise AssetError("hook-copy-lab needs a Gemini text provider")
            last_error: QualityError | None = None
            for _ in range(self.quality_retries + 1):
                try:
                    generated = self.provider.generate_hooks(
                        [demo for demo, _ in assignments],
                        formats=[
                            format_definition for _, format_definition in assignments
                        ],
                    )
                    items = self._ordered_items(assignments, generated)
                    break
                except QualityError as exc:
                    last_error = exc
            else:
                assert last_error is not None
                raise last_error
            status = "complete"

        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"
        temporary_manifest = output_dir / ".manifest.json.tmp"
        temporary_manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": status,
                    "run_date": run_date.isoformat(),
                    "videos": items,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, manifest_path)
        report_path = output_dir / "report.html"
        temporary_report = output_dir / ".report.html.tmp"
        temporary_report.write_text(
            _html_report(items, status=status, run_date=run_date), encoding="utf-8"
        )
        os.replace(temporary_report, report_path)
        return HookCopyLabResult(output_dir, report_path, manifest_path, status)

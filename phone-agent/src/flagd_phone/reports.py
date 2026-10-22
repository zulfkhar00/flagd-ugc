from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from .session import SessionStore


def _markdown(value: str) -> str:
    # Findings and answers contain untrusted screen text, not report markup.
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def _write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_report(store: SessionStore) -> dict[str, str]:
    """Rebuild from saved observations, never from another model request."""
    observations = {row["frame_id"]: dict(row) for row in store.db.execute("SELECT * FROM observations")}
    findings = store.all_findings()
    for finding in findings:
        observation = observations[finding["frame_id"]]
        finding["screenshot"] = observation["image_path"]
        finding["observed_at"] = observation["captured_at"]
    report = {
        "goal": store.get("task", {}).get("goal", ""), "status": store.get("status", "unknown"),
        "started_at": store.get("started_at"), "finished_at": store.get("finished_at"),
        "plan": store.get("plan", {}), "progress": store.get("progress", {}),
        "summary": store.get("summary", ""), "answer": store.get("answer", ""),
        "answer_finding_ids": store.get("answer_finding_ids", []), "findings": findings,
        "visited_sources": [dict(row) for row in store.db.execute("SELECT * FROM visited ORDER BY rowid")],
        "result": store.get("result", {}),
        "limitations": [
            "Screen readings and conclusions were produced by a vision model; inspect cited screenshots.",
            "Completion is a model assessment, not independently verified task success.",
            "Only captured visible content was considered; unseen pages and video/audio content were not verified.",
        ],
    }
    lines = ["# Phone task report", "", _markdown(report["goal"]), "",
             f"Status: `{report['status']}`. Independently verified success: **no**.", ""]
    if report["answer"]:
        lines.extend(["## Answer", "", _markdown(report["answer"]), "",
                      "Supporting findings: " + ", ".join(report["answer_finding_ids"]), ""])
    if report["summary"]:
        lines.extend(["## Session notes", "", _markdown(report["summary"]), ""])
    if report["plan"]:
        lines.extend(["## Plan and progress", ""])
        for goal in report["plan"]["subgoals"]:
            progress = report["progress"].get(goal["id"], {})
            lines.append(f"- {_markdown(goal['description'])} — {_markdown(progress.get('status', 'pending'))}")
            if progress.get("evidence"):
                lines.append("  Evidence: " + _markdown(progress["evidence"]))
        lines.extend(["", "Success criteria:", ""])
        lines.extend("- " + _markdown(item) for item in report["plan"]["success_criteria"])
        if report["plan"]["search_queries"]:
            lines.extend(["", "Initial search ideas:", ""])
            lines.extend("- " + _markdown(item) for item in report["plan"]["search_queries"])
        lines.append("")
    lines.extend(["## Observed findings", ""])
    if not findings:
        lines.extend(["No findings recorded. This may be a navigation task or an incomplete research run.", ""])
    for finding in findings:
        path = finding["screenshot"]
        if not re.fullmatch(r"\d{3}\.png", path):
            raise ValueError("invalid saved screenshot path")
        lines.extend([f"### {finding['id']}", "", _markdown(finding["claim"]), "",
                      "Source: " + _markdown(finding["source_label"]), "",
                      "Visible text:", ""])
        lines.extend("> " + _markdown(line) for line in finding["visible_text"].splitlines())
        lines.extend(["", f"[Inspect screenshot]({path}) · observed at Unix time {finding['observed_at']:.3f}.", ""])
        if finding["source_url"]:
            url = quote(finding["source_url"], safe="/:?&=.%#@+-_")
            lines.extend([f"[Open observed source](<{url}>)", ""])
        if finding["uncertainty"]:
            lines.extend(["Uncertainty: " + _markdown(finding["uncertainty"]), ""])
    if report["result"]:
        lines.extend(["## Run usage", "",
                      f"- Model requests: {report['result'].get('model_calls', 0)}",
                      f"- Acknowledged inputs: {report['result'].get('acknowledged_inputs', 0)}",
                      f"- Reported total tokens: {report['result'].get('reported_total_tokens', 0)}", ""])
        for name, usage in report["result"].get("model_usage", {}).items():
            lines.append(f"- {_markdown(name)}: {usage['calls']} requests, "
                         f"{usage['promptTokenCount']} input, "
                         f"{usage['candidatesTokenCount']} output, "
                         f"{usage['cachedContentTokenCount']} cached input tokens")
        lines.append("")
    lines.extend(["## Limits", ""])
    lines.extend("- " + item for item in report["limitations"])
    json_path, markdown_path = store.directory / "report.json", store.directory / "report.md"
    _write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write(markdown_path, "\n".join(lines) + "\n")
    return {"markdown": str(markdown_path), "json": str(json_path)}

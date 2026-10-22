from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .vision import ACTION_SCHEMA, Frame, validate_action


PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "subgoals": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "description": {"type": "string"}},
            "required": ["id", "description"],
        }},
        "success_criteria": {"type": "array", "minItems": 1, "maxItems": 8,
                             "items": {"type": "string"}},
        "search_queries": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
    },
    "required": ["subgoals", "success_criteria", "search_queries"],
}

ASSESSMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": ACTION_SCHEMA,
        "summary": {"type": "string"},
        "findings": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in (
                "claim", "visible_text", "source_label", "source_url", "uncertainty",
            )},
            "required": ["claim", "visible_text", "source_label", "source_url", "uncertainty"],
        }},
        "visited_sources": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "progress": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "subgoal_id": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "done", "blocked"]},
                "evidence": {"type": "string"},
            },
            "required": ["subgoal_id", "status", "evidence"],
        }},
        "answer": {"type": "string"},
        "answer_finding_ids": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
    },
    "required": ["action", "summary", "findings", "visited_sources", "progress",
                 "answer", "answer_finding_ids"],
}


def _text(value: Any, limit: int, *, empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= limit and (empty or bool(value.strip()))


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != set(PLAN_SCHEMA["required"]):
        raise ValueError("invalid plan fields")
    goals = plan["subgoals"]
    if not isinstance(goals, list) or not 1 <= len(goals) <= 8:
        raise ValueError("a plan needs 1–8 subgoals")
    ids = []
    for goal in goals:
        if (not isinstance(goal, dict) or set(goal) != {"id", "description"}
                or not _text(goal["id"], 40) or not _text(goal["description"], 500)):
            raise ValueError("invalid subgoal")
        ids.append(goal["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("subgoal IDs must be unique")
    criteria = plan["success_criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 8 or any(not _text(x, 500) for x in criteria):
        raise ValueError("invalid success criteria")
    queries = plan["search_queries"]
    if (not isinstance(queries, list) or len(queries) > 6
            or any(not _text(x, 200) or any(ord(c) < 32 or ord(c) == 127 for c in x) for x in queries)):
        raise ValueError("invalid planned search queries")
    return plan


def validate_assessment(
    value: Any, frame: Frame, text_fields: dict[str, str], memory: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(ASSESSMENT_SCHEMA["required"]):
        raise ValueError("invalid assessment fields")
    validate_action(value["action"], frame, text_fields)
    if not _text(value["summary"], 2000, empty=True) or not _text(value["answer"], 4000, empty=True):
        raise ValueError("invalid session summary or answer")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) > 5:
        raise ValueError("invalid findings")
    finding_fields = {"claim", "visible_text", "source_label", "source_url", "uncertainty"}
    for finding in findings:
        if (not isinstance(finding, dict) or set(finding) != finding_fields
                or not _text(finding["claim"], 1000) or not _text(finding["visible_text"], 1500)
                or not _text(finding["source_label"], 300)
                or not _text(finding["uncertainty"], 1000, empty=True)
                or not _text(finding["source_url"], 1000, empty=True)):
            raise ValueError("each finding needs a claim, visible text and source")
        if finding["source_url"]:
            url = finding["source_url"]
            parts = urlsplit(url)
            if (parts.scheme not in ("http", "https") or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or any(c.isspace() or ord(c) < 32 for c in url)
                    or url not in finding["visible_text"]):
                raise ValueError("source URL must be an HTTP(S) URL transcribed in visible text")
    visited = value["visited_sources"]
    if not isinstance(visited, list) or len(visited) > 5 or any(not _text(x, 300) for x in visited):
        raise ValueError("invalid visited sources")
    progress = value["progress"]
    known_goals = {x["id"] for x in memory.get("plan", {}).get("subgoals", [])}
    if not isinstance(progress, list) or len(progress) > 8:
        raise ValueError("invalid progress")
    seen_goals = set()
    for update in progress:
        if (not isinstance(update, dict) or set(update) != {"subgoal_id", "status", "evidence"}
                or not isinstance(update["subgoal_id"], str) or update["subgoal_id"] not in known_goals
                or update["subgoal_id"] in seen_goals
                or update["status"] not in ("pending", "done", "blocked")
                or not _text(update["evidence"], 500, empty=update["status"] == "pending")):
            raise ValueError("progress must refer to a known subgoal with supporting evidence")
        seen_goals.add(update["subgoal_id"])
    answer_ids = value["answer_finding_ids"]
    known_findings = {x["id"] for x in memory.get("findings", [])}
    if (not isinstance(answer_ids, list) or len(answer_ids) > 20
            or any(not isinstance(x, str) or x not in known_findings for x in answer_ids)):
        raise ValueError("answer citations must refer to recorded findings")
    # Results collected in this same observation have not received IDs yet.
    # A research answer must cite prior recorded findings; observe before finishing.
    if value["answer"].strip() and not answer_ids:
        raise ValueError("an answer requires recorded finding citations")
    return value


class SessionStore:
    """A durable research notebook. Opening it never resumes phone inputs."""

    def __init__(self, directory: Path, *, create: bool = True):
        self.directory = directory
        path = directory / "memory.sqlite3"
        if not create and not path.is_file():
            raise ValueError("no task memory in this run directory")
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (
                frame_id TEXT PRIMARY KEY, step INTEGER NOT NULL, captured_at REAL NOT NULL,
                image_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY, claim TEXT NOT NULL, visible_text TEXT NOT NULL,
                source_label TEXT NOT NULL, source_url TEXT NOT NULL, uncertainty TEXT NOT NULL,
                frame_id TEXT NOT NULL REFERENCES observations(frame_id),
                UNIQUE(claim, visible_text, source_label, source_url)
            );
            CREATE TABLE IF NOT EXISTS visited (
                source TEXT PRIMARY KEY, frame_id TEXT NOT NULL REFERENCES observations(frame_id)
            );
        """)

    def set(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def start(self, task: dict[str, Any]) -> None:
        with self.db:
            self.set("task", task)
            self.set("status", "running")
            self.set("started_at", time.time())

    def plan(self, plan: dict[str, Any]) -> None:
        validate_plan(plan)
        with self.db:
            self.set("plan", plan)
            self.set("progress", {x["id"]: {"status": "pending", "evidence": ""} for x in plan["subgoals"]})

    def observation(self, step: int, frame: Frame) -> None:
        with self.db:
            self.db.execute("INSERT INTO observations VALUES (?, ?, ?, ?)",
                            (frame.id, step, time.time(), f"{step:03d}.png"))

    @staticmethod
    def _finding(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["id"] = f"F{value['id']:04d}"
        return value

    def all_findings(self) -> list[dict[str, Any]]:
        return [self._finding(row) for row in self.db.execute("SELECT * FROM findings ORDER BY id")]

    def context(self) -> dict[str, Any]:
        findings = [self._finding(row) for row in self.db.execute("SELECT * FROM findings ORDER BY id DESC LIMIT 20")]
        visited = [row[0] for row in self.db.execute("SELECT source FROM visited ORDER BY rowid DESC LIMIT 30")]
        return {"plan": self.get("plan", {}), "progress": self.get("progress", {}),
                "summary": self.get("summary", ""), "findings": list(reversed(findings)),
                "visited_sources": visited,
                "finding_count": self.db.execute("SELECT count(*) FROM findings").fetchone()[0]}

    def assess(self, value: dict[str, Any], frame: Frame, text_fields: dict[str, str]) -> None:
        validate_assessment(value, frame, text_fields, self.context())
        if not self.db.execute("SELECT 1 FROM observations WHERE frame_id=?", (frame.id,)).fetchone():
            raise ValueError("finding evidence must refer to a stored observation")
        with self.db:
            for item in value["findings"]:
                self.db.execute("""INSERT OR IGNORE INTO findings
                    (claim, visible_text, source_label, source_url, uncertainty, frame_id)
                    VALUES (?, ?, ?, ?, ?, ?)""", (item["claim"], item["visible_text"], item["source_label"],
                    item["source_url"], item["uncertainty"], frame.id))
            for source in value["visited_sources"]:
                self.db.execute("INSERT OR IGNORE INTO visited VALUES (?, ?)", (source, frame.id))
            progress = self.get("progress", {})
            for item in value["progress"]:
                progress[item["subgoal_id"]] = {"status": item["status"], "evidence": item["evidence"],
                                                "frame_id": frame.id}
            self.set("progress", progress)
            self.set("summary", value["summary"])
            if value["answer"]:
                self.set("answer", value["answer"])
                self.set("answer_finding_ids", value["answer_finding_ids"])

    def finish(self, result: dict[str, Any]) -> None:
        with self.db:
            self.set("result", result)
            self.set("status", result["status"])
            self.set("finished_at", time.time())

    def close(self) -> None:
        self.db.close()

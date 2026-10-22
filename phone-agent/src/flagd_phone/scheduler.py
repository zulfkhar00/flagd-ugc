"""Serial, local daily browsing slots. No replay of missed or interrupted jobs."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .controller import ControlError
from .workflows import CALIFORNIA, browse_task, next_california_slot


SCHEDULE_NAMESPACE = uuid.UUID("0788d24e-d0bb-4d87-934c-c237a5f796a1")


def validate_schedule(config: Any) -> dict[str, Any]:
    if (not isinstance(config, dict) or set(config) != {"enabled", "timezone", "accounts"}
            or type(config["enabled"]) is not bool or config["timezone"] != "America/Los_Angeles"
            or not isinstance(config["accounts"], list) or not 1 <= len(config["accounts"]) <= 20):
        raise ValueError("schedule needs enabled, America/Los_Angeles timezone, and accounts")
    seen = set()
    for item in config["accounts"]:
        if (not isinstance(item, dict) or set(item) != {
            "app", "account", "keywords", "minutes_per_day", "times",
            "max_likes_per_session", "max_comments_per_session",
        }):
            raise ValueError("invalid scheduled account fields")
        minutes, times = item["minutes_per_day"], item["times"]
        if (type(minutes) is not int or not 2 <= minutes <= 60
                or not isinstance(times, list) or len(times) != 2 or times[0] == times[1]):
            raise ValueError("use two distinct daily times and 2–60 daily minutes per account/app")
        for slot in times:
            if not isinstance(slot, str):
                raise ValueError("use HH:MM schedule times")
            next_california_slot(datetime.now(timezone.utc), slot)
        task = browse_task(app=item["app"], account=item["account"], keywords=item["keywords"],
                           seconds=minutes * 30, max_likes=item["max_likes_per_session"],
                           max_comments=item["max_comments_per_session"])
        key = (item["app"], task["authorization"]["account"])
        if key in seen:
            raise ValueError("duplicate scheduled app/account")
        seen.add(key)
    return config


def schedule_preview(config: dict[str, Any], now: datetime) -> dict[str, Any]:
    config = validate_schedule(config)
    rows = []
    for item in config["accounts"]:
        for slot in item["times"]:
            start = next_california_slot(now, slot)
            rows.append({"app": item["app"], "account": item["account"],
                         "local_start": start.isoformat(),
                         "utc_start": start.astimezone(timezone.utc).isoformat(),
                         "session_seconds": item["minutes_per_day"] * 30})
    return {"enabled": config["enabled"], "timezone": config["timezone"],
            "next_slots": sorted(rows, key=lambda row: row["utc_start"])}


class DailyScheduler:
    def __init__(self, controller: Any, path: Path):
        self.controller, self.path = controller, path

    async def run(self) -> None:
        # Don't catch up on yesterday's jobs when the Mac wakes or restarts.
        pending: dict[str, tuple[datetime, dict[str, Any]]] = {}
        signature = None
        while True:
            config = validate_schedule(json.loads(self.path.read_text()))
            now = datetime.now(timezone.utc)
            current = json.dumps(config, sort_keys=True)
            if current != signature:
                pending.clear()
                if config["enabled"]:
                    for i, item in enumerate(config["accounts"]):
                        for slot in item["times"]:
                            pending[f"{i}:{slot}"] = (next_california_slot(now, slot), item)
                signature = current
                print(json.dumps({"event": "schedule_loaded", **schedule_preview(config, now)}), flush=True)
            for key, (due, item) in sorted(pending.items(), key=lambda entry: entry[1][0].astimezone(timezone.utc)):
                if due.astimezone(timezone.utc) > now:
                    continue
                age = (now - due.astimezone(timezone.utc)).total_seconds()
                # Give a slot 60 minutes for earlier accounts on this one phone.
                # Every job still gets its own full session duration once started.
                if age > 3600:
                    print(json.dumps({"event": "schedule_skipped", "account": item["account"],
                                      "app": item["app"], "reason": "slot_expired"}), flush=True)
                else:
                    request_id = str(uuid.uuid5(SCHEDULE_NAMESPACE,
                        f"{item['app']}:{item['account']}:{due.isoformat()}"))
                    task = browse_task(app=item["app"], account=item["account"], keywords=item["keywords"],
                                       seconds=item["minutes_per_day"] * 30,
                                       max_likes=item["max_likes_per_session"],
                                       max_comments=item["max_comments_per_session"])
                    try:
                        receipt = self.controller.submit(task, execute=True, request_id=request_id)
                    except ControlError as error:
                        if error.code in ("busy", "offline", "recovery_required"):
                            continue  # Wait for ownership/connection; no AI call.
                        raise
                    print(json.dumps({"event": "schedule_submitted", "account": item["account"],
                                      "job_id": receipt["job"]["id"], "duplicate": receipt["duplicate"]}), flush=True)
                pending[key] = (next_california_slot(now + timedelta(seconds=1), key.split(":", 1)[1]), item)
            await asyncio.sleep(10)

from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .media import cleanup_renders
from .pipeline import Pipeline

LOGGER = logging.getLogger(__name__)


def parse_run_time(value: str) -> time:
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        parsed = time(hour=int(hour_text), minute=int(minute_text))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"RUN_TIME must be HH:MM, got {value!r}") from exc
    return parsed


def seconds_until_run(now: datetime, scheduled: time) -> float:
    target = now.replace(
        hour=scheduled.hour, minute=scheduled.minute, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds()


def run_daemon(settings: Settings) -> None:
    timezone = ZoneInfo(settings.timezone)
    scheduled = parse_run_time(settings.run_time)
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pipeline = Pipeline(settings)
    first_iteration = True
    while not stopped.is_set():
        now = datetime.now(timezone)
        if first_iteration and now.time() < scheduled:
            delay = seconds_until_run(now, scheduled)
            LOGGER.info("next batch scheduled in %.0f seconds", delay)
            if stopped.wait(delay):
                break
            now = datetime.now(timezone)
        first_iteration = False
        try:
            result = pipeline.run(now.date())
            LOGGER.info(
                "batch %s finished with %s (%s/%s)",
                result.run_date,
                result.status,
                result.completed,
                result.expected,
            )
        except Exception:
            LOGGER.exception("scheduled batch failed")
        removed = cleanup_renders(
            settings.renders_dir,
            settings.final_retention_days,
            settings.intermediate_retention_days,
        )
        LOGGER.info("retention cleanup removed %s files", removed)
        delay = seconds_until_run(datetime.now(timezone), scheduled)
        LOGGER.info("next batch scheduled in %.0f seconds", delay)
        stopped.wait(delay)

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY,
                    run_date TEXT NOT NULL UNIQUE,
                    expected_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    music_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS batch_stages (
                    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    output_path TEXT,
                    metadata_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, name)
                );

                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    hook TEXT NOT NULL,
                    hook_format TEXT NOT NULL DEFAULT 'direct-observation',
                    title TEXT NOT NULL DEFAULT '',
                    opening_direction TEXT NOT NULL DEFAULT '',
                    demo_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (batch_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS video_stages (
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    output_path TEXT,
                    metadata_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (video_id, name)
                );

                CREATE TABLE IF NOT EXISTS music_usage (
                    id INTEGER PRIMARY KEY,
                    batch_id INTEGER NOT NULL UNIQUE REFERENCES batches(id) ON DELETE CASCADE,
                    music_path TEXT NOT NULL,
                    used_on TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS music_usage_recent
                    ON music_usage (used_on, music_path);

                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY,
                    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    delivery_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    telegram_message_id INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (batch_id, delivery_key)
                );

                CREATE TABLE IF NOT EXISTS leases (
                    lock_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Existing local databases predate caption formats. A default preserves their
            # stored composition input exactly and lets interrupted historical batches resume.
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(videos)").fetchall()
            }
            if "hook_format" not in columns:
                connection.execute(
                    "ALTER TABLE videos ADD COLUMN hook_format TEXT NOT NULL "
                    "DEFAULT 'direct-observation'"
                )
            if "title" not in columns:
                connection.execute(
                    "ALTER TABLE videos ADD COLUMN title TEXT NOT NULL DEFAULT ''"
                )
            if "opening_direction" not in columns:
                connection.execute(
                    "ALTER TABLE videos ADD COLUMN opening_direction TEXT NOT NULL "
                    "DEFAULT ''"
                )

    def get_or_create_batch(self, run_date: date, expected_count: int) -> sqlite3.Row:
        now = _iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO batches(run_date, expected_count, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_date) DO NOTHING
                """,
                (run_date.isoformat(), expected_count, now, now),
            )
            row = connection.execute(
                "SELECT * FROM batches WHERE run_date = ?", (run_date.isoformat(),)
            ).fetchone()
            assert row is not None
            if row["expected_count"] != expected_count:
                raise ValueError(
                    f"batch {run_date} already expects {row['expected_count']} videos, "
                    f"not {expected_count}"
                )
            return row

    def get_batch(self, batch_id: int) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return row

    def set_batch_status(
        self, batch_id: int, status: str, error: str | None = None
    ) -> None:
        completed = _iso() if status in {"complete", "partial", "failed"} else None
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE batches
                SET status = ?, error = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, error, _iso(), completed, batch_id),
            )

    def start_batch_stage(self, batch_id: int, name: str) -> int:
        now = _iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO batch_stages(batch_id, name, status, attempts, updated_at)
                VALUES (?, ?, 'running', 1, ?)
                ON CONFLICT(batch_id, name) DO UPDATE SET
                    status = 'running', attempts = attempts + 1,
                    error = NULL, updated_at = excluded.updated_at
                """,
                (batch_id, name, now),
            )
            return int(
                connection.execute(
                    "SELECT attempts FROM batch_stages WHERE batch_id = ? AND name = ?",
                    (batch_id, name),
                ).fetchone()[0]
            )

    def complete_batch_stage(
        self,
        batch_id: int,
        name: str,
        *,
        output_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO batch_stages(
                    batch_id, name, status, attempts, output_path, metadata_json, updated_at
                ) VALUES (?, ?, 'complete', 1, ?, ?, ?)
                ON CONFLICT(batch_id, name) DO UPDATE SET
                    status = 'complete', output_path = excluded.output_path,
                    metadata_json = excluded.metadata_json, error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    batch_id,
                    name,
                    str(output_path) if output_path else None,
                    json.dumps(metadata) if metadata is not None else None,
                    _iso(),
                ),
            )

    def fail_batch_stage(self, batch_id: int, name: str, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE batch_stages SET status = 'failed', error = ?, updated_at = ?
                WHERE batch_id = ? AND name = ?
                """,
                (error[:4000], _iso(), batch_id, name),
            )

    def get_batch_stage(self, batch_id: int, name: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM batch_stages WHERE batch_id = ? AND name = ?",
                (batch_id, name),
            ).fetchone()

    def create_videos(
        self, batch_id: int, items: list[dict[str, str]]
    ) -> list[sqlite3.Row]:
        now = _iso()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0]
            if existing and existing != len(items):
                raise ValueError(
                    "persisted hook count does not match generated hook count"
                )
            for ordinal, item in enumerate(items, start=1):
                connection.execute(
                    """
                    INSERT INTO videos(
                        batch_id, ordinal, hook, hook_format, title, opening_direction,
                        demo_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, ordinal) DO NOTHING
                    """,
                    (
                        batch_id,
                        ordinal,
                        item["hook"],
                        item.get("hook_format", "direct-observation"),
                        item.get("title", ""),
                        item.get("opening_direction", ""),
                        item["demo_path"],
                        now,
                        now,
                    ),
                )
            return list(
                connection.execute(
                    "SELECT * FROM videos WHERE batch_id = ? ORDER BY ordinal",
                    (batch_id,),
                ).fetchall()
            )

    def set_video_opening_direction(self, video_id: int, direction: str) -> None:
        """Persist a deterministic direction before its first paid scene generation."""
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE videos SET opening_direction = ?, updated_at = ?
                WHERE id = ? AND opening_direction = ''
                """,
                (direction, _iso(), video_id),
            )

    def list_videos(self, batch_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM videos WHERE batch_id = ? ORDER BY ordinal",
                    (batch_id,),
                ).fetchall()
            )

    def rebase_missing_data_paths(self, data_dir: Path) -> dict[str, int]:
        """Rebase persisted local asset paths after moving `/data` to another host."""
        demo_updates = 0
        stage_updates = 0
        with self.connect() as connection:
            videos = connection.execute(
                "SELECT id, demo_path FROM videos"
            ).fetchall()
            for video in videos:
                current = Path(str(video["demo_path"]))
                candidate = data_dir / "demos" / current.name
                if current.is_file() or not candidate.is_file():
                    continue
                connection.execute(
                    "UPDATE videos SET demo_path = ?, updated_at = ? WHERE id = ?",
                    (str(candidate), _iso(), int(video["id"])),
                )
                demo_updates += 1

            stages = connection.execute(
                """
                SELECT video_id, name, output_path FROM video_stages
                WHERE output_path IS NOT NULL
                """
            ).fetchall()
            for stage in stages:
                current = Path(str(stage["output_path"]))
                if current.is_file():
                    continue
                _, marker, suffix = str(current).partition("/renders/")
                if not marker:
                    continue
                candidate = data_dir / "renders" / suffix
                if not candidate.is_file():
                    continue
                connection.execute(
                    """
                    UPDATE video_stages SET output_path = ?, updated_at = ?
                    WHERE video_id = ? AND name = ?
                    """,
                    (
                        str(candidate),
                        _iso(),
                        int(stage["video_id"]),
                        str(stage["name"]),
                    ),
                )
                stage_updates += 1
        return {"demos": demo_updates, "stages": stage_updates}

    def set_video_status(
        self, video_id: int, status: str, error: str | None = None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE videos SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error[:4000] if error else None, _iso(), video_id),
            )

    def start_video_stage(
        self, video_id: int, name: str, *, clear_metadata: bool = False
    ) -> int:
        now = _iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO video_stages(video_id, name, status, attempts, updated_at)
                VALUES (?, ?, 'running', 1, ?)
                ON CONFLICT(video_id, name) DO UPDATE SET
                    status = 'running', attempts = attempts + 1,
                    error = NULL, updated_at = excluded.updated_at
                """,
                (video_id, name, now),
            )
            if clear_metadata:
                connection.execute(
                    """
                    UPDATE video_stages
                    SET output_path = NULL, metadata_json = NULL
                    WHERE video_id = ? AND name = ?
                    """,
                    (video_id, name),
                )
            return int(
                connection.execute(
                    "SELECT attempts FROM video_stages WHERE video_id = ? AND name = ?",
                    (video_id, name),
                ).fetchone()[0]
            )

    def update_video_stage_metadata(
        self, video_id: int, name: str, metadata: dict[str, Any]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE video_stages SET metadata_json = ?, updated_at = ?
                WHERE video_id = ? AND name = ?
                """,
                (json.dumps(metadata), _iso(), video_id, name),
            )

    def complete_video_stage(
        self,
        video_id: int,
        name: str,
        *,
        output_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO video_stages(
                    video_id, name, status, attempts, output_path, metadata_json, updated_at
                ) VALUES (?, ?, 'complete', 1, ?, ?, ?)
                ON CONFLICT(video_id, name) DO UPDATE SET
                    status = 'complete', output_path = excluded.output_path,
                    metadata_json = excluded.metadata_json, error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    video_id,
                    name,
                    str(output_path) if output_path else None,
                    json.dumps(metadata) if metadata is not None else None,
                    _iso(),
                ),
            )

    def fail_video_stage(self, video_id: int, name: str, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE video_stages SET status = 'failed', error = ?, updated_at = ?
                WHERE video_id = ? AND name = ?
                """,
                (error[:4000], _iso(), video_id, name),
            )

    def reset_video_stage_attempts(self, video_id: int, name: str) -> None:
        """Refund attempts rejected by provider validation before generation."""
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE video_stages SET attempts = 0, updated_at = ?
                WHERE video_id = ? AND name = ?
                """,
                (_iso(), video_id, name),
            )

    def get_video_stage(self, video_id: int, name: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM video_stages WHERE video_id = ? AND name = ?",
                (video_id, name),
            ).fetchone()

    def recent_music(self, run_date: date, days: int = 14) -> set[str]:
        cutoff = (run_date - timedelta(days=days)).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT music_path FROM music_usage WHERE used_on >= ? AND used_on < ?",
                (cutoff, run_date.isoformat()),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def assign_music(self, batch_id: int, run_date: date, music_path: Path) -> str:
        path = str(music_path.resolve())
        with self.connect() as connection:
            batch = connection.execute(
                "SELECT music_path FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            if batch["music_path"]:
                return str(batch["music_path"])
            connection.execute(
                "UPDATE batches SET music_path = ?, updated_at = ? WHERE id = ?",
                (path, _iso(), batch_id),
            )
            connection.execute(
                """
                INSERT INTO music_usage(batch_id, music_path, used_on, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, path, run_date.isoformat(), _iso()),
            )
        return path

    def acquire_lease(self, key: str, owner: str, ttl: timedelta) -> bool:
        now = utc_now()
        expires = now + ttl
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner, expires_at FROM leases WHERE lock_key = ?", (key,)
            ).fetchone()
            if row and row["owner"] != owner:
                if datetime.fromisoformat(row["expires_at"]) > now:
                    return False
            connection.execute(
                """
                INSERT INTO leases(lock_key, owner, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lock_key) DO UPDATE SET
                    owner = excluded.owner, expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (key, owner, expires.isoformat(), now.isoformat()),
            )
            return True

    def renew_lease(self, key: str, owner: str, ttl: timedelta) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE leases SET expires_at = ?, updated_at = ?
                WHERE lock_key = ? AND owner = ?
                """,
                ((utc_now() + ttl).isoformat(), _iso(), key, owner),
            )
            return cursor.rowcount == 1

    def release_lease(self, key: str, owner: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM leases WHERE lock_key = ? AND owner = ?", (key, owner)
            )

    def claim_delivery(
        self,
        batch_id: int,
        delivery_key: str,
        kind: str,
        video_id: int | None = None,
    ) -> bool:
        """Reserve a send durably. An existing inflight send is never repeated."""
        now = _iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO deliveries(
                    batch_id, delivery_key, kind, video_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(batch_id, delivery_key) DO NOTHING
                """,
                (batch_id, delivery_key, kind, video_id, now, now),
            )
            row = connection.execute(
                "SELECT status FROM deliveries WHERE batch_id = ? AND delivery_key = ?",
                (batch_id, delivery_key),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return False
            connection.execute(
                """
                UPDATE deliveries SET status = 'inflight', updated_at = ?
                WHERE batch_id = ? AND delivery_key = ? AND status = 'pending'
                """,
                (now, batch_id, delivery_key),
            )
            return True

    def complete_delivery(
        self, batch_id: int, delivery_key: str, telegram_message_id: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE deliveries
                SET status = 'sent', telegram_message_id = ?, error = NULL, updated_at = ?
                WHERE batch_id = ? AND delivery_key = ?
                """,
                (telegram_message_id, _iso(), batch_id, delivery_key),
            )

    def mark_delivery_error(
        self, batch_id: int, delivery_key: str, error: str, *, safe_to_retry: bool
    ) -> None:
        status = "pending" if safe_to_retry else "uncertain"
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE deliveries SET status = ?, error = ?, updated_at = ?
                WHERE batch_id = ? AND delivery_key = ?
                """,
                (status, error[:4000], _iso(), batch_id, delivery_key),
            )

    def delivery_status(self, batch_id: int, delivery_key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE batch_id = ? AND delivery_key = ?",
                (batch_id, delivery_key),
            ).fetchone()
        return str(row[0]) if row else None

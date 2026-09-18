from datetime import date, timedelta
from pathlib import Path
import sqlite3

from flagd_ugc.db import Database


def test_batch_and_video_creation_are_idempotent(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    first = db.get_or_create_batch(date(2026, 9, 16), 1)
    second = db.get_or_create_batch(date(2026, 9, 16), 1)
    assert first["id"] == second["id"]

    items = [{"hook": "I can see my plans clearly", "demo_path": "/demo.mp4"}]
    assert len(db.create_videos(int(first["id"]), items)) == 1
    assert len(db.create_videos(int(first["id"]), items)) == 1
    assert db.list_videos(int(first["id"]))[0]["hook_format"] == "direct-observation"
    assert db.list_videos(int(first["id"]))[0]["title"] == ""
    assert db.list_videos(int(first["id"]))[0]["opening_direction"] == ""


def test_video_persists_its_caption_format(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    batch = db.get_or_create_batch(date(2026, 9, 16), 1)

    rows = db.create_videos(
        int(batch["id"]),
        [
            {
                "hook": "rating the chat: 43/100",
                "hook_format": "scorecard",
                "title": "the 43 conversation score",
                "opening_direction": "scorecard-bed-facepalm-v1",
                "demo_path": "/progress.mp4",
            }
        ],
    )

    assert rows[0]["hook_format"] == "scorecard"
    assert rows[0]["title"] == "the 43 conversation score"
    assert rows[0]["opening_direction"] == "scorecard-bed-facepalm-v1"


def test_rebases_missing_paths_when_persistent_data_moves_hosts(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    batch = db.get_or_create_batch(date(2026, 9, 18), 1)
    rows = db.create_videos(
        int(batch["id"]),
        [{"hook": "a hook", "demo_path": "/old/mac/data/demos/demo.mp4"}],
    )
    video_id = int(rows[0]["id"])
    db.start_video_stage(video_id, "scene")
    db.complete_video_stage(
        video_id,
        "scene",
        output_path=Path("/old/mac/data/renders/2026-09-18/scene.png"),
    )
    data_dir = tmp_path / "data"
    demo = data_dir / "demos" / "demo.mp4"
    scene = data_dir / "renders" / "2026-09-18" / "scene.png"
    demo.parent.mkdir(parents=True)
    scene.parent.mkdir(parents=True)
    demo.write_bytes(b"demo")
    scene.write_bytes(b"scene")

    result = db.rebase_missing_data_paths(data_dir)

    video = db.list_videos(int(batch["id"]))[0]
    stage = db.get_video_stage(video_id, "scene")
    assert result == {"demos": 1, "stages": 1}
    assert video["demo_path"] == str(demo)
    assert stage["output_path"] == str(scene)


def test_initialize_migrates_a_pre_format_video_table(tmp_path):
    path = tmp_path / "db.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                hook TEXT NOT NULL,
                demo_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (batch_id, ordinal)
            )
            """
        )

    db = Database(path)
    db.initialize()

    with db.connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(videos)")
        }
    assert "hook_format" in columns
    assert "title" in columns
    assert "opening_direction" in columns


def test_inflight_delivery_is_never_claimed_twice(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    batch = db.get_or_create_batch(date(2026, 9, 16), 1)
    batch_id = int(batch["id"])

    assert db.claim_delivery(batch_id, "video:1", "document") is True
    assert db.claim_delivery(batch_id, "video:1", "document") is False
    assert db.delivery_status(batch_id, "video:1") == "inflight"


def test_date_lease_excludes_another_worker(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    assert db.acquire_lease("batch:2026-09-16", "worker-a", timedelta(hours=1))
    assert not db.acquire_lease("batch:2026-09-16", "worker-b", timedelta(hours=1))
    db.release_lease("batch:2026-09-16", "worker-a")
    assert db.acquire_lease("batch:2026-09-16", "worker-b", timedelta(hours=1))

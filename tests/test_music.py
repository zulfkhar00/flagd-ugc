import random
from datetime import date, timedelta

import pytest

from flagd_ugc.assets import select_music
from flagd_ugc.db import Database
from flagd_ugc.errors import AssetError


def database(tmp_path):
    db = Database(tmp_path / "pipeline.sqlite3")
    db.initialize()
    return db


def test_selects_only_track_not_used_in_previous_fourteen_days(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    used = music / "used.mp3"
    fresh = music / "fresh.wav"
    used.touch()
    fresh.touch()
    db = database(tmp_path)
    today = date(2026, 9, 16)
    batch = db.get_or_create_batch(today - timedelta(days=1), 4)
    db.assign_music(int(batch["id"]), today - timedelta(days=1), used)

    selected = select_music(music, db, today, rng=random.Random(1), verify_audio=False)

    assert selected == fresh.resolve()


def test_track_used_exactly_fourteen_days_ago_remains_ineligible(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    track = music / "track.m4a"
    track.touch()
    db = database(tmp_path)
    today = date(2026, 9, 16)
    old = db.get_or_create_batch(today - timedelta(days=14), 4)
    db.assign_music(int(old["id"]), today - timedelta(days=14), track)

    with pytest.raises(AssetError, match="last 14 days"):
        select_music(music, db, today, verify_audio=False)


def test_track_used_fifteen_days_ago_is_eligible(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    track = music / "track.mp4"
    track.touch()
    db = database(tmp_path)
    today = date(2026, 9, 16)
    old = db.get_or_create_batch(today - timedelta(days=15), 4)
    db.assign_music(int(old["id"]), today - timedelta(days=15), track)

    assert select_music(music, db, today, verify_audio=False) == track.resolve()


def test_empty_bank_stops_selection(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    with pytest.raises(AssetError, match="empty"):
        select_music(music, database(tmp_path), date.today(), verify_audio=False)

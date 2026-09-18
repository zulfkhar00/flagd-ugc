from datetime import date

import httpx
import pytest

from flagd_ugc.db import Database
from flagd_ugc.errors import DeliveryUncertain
from flagd_ugc.telegram import TelegramClient


def _database(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    batch = database.get_or_create_batch(date(2026, 9, 16), 1)
    return database, int(batch["id"])


def test_document_is_sent_only_once(tmp_path):
    database, batch_id = _database(tmp_path)
    video_id = int(
        database.create_videos(
            batch_id,
            [{"hook": "I can share my plans", "demo_path": "/demo.mp4"}],
        )[0]["id"]
    )
    document = tmp_path / "video.mp4"
    document.write_bytes(b"video")
    calls = 0
    request_bodies = []

    def handler(request):
        nonlocal calls
        calls += 1
        request_bodies.append(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    telegram = TelegramClient(
        "token",
        "chat",
        database,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        telegram.send_document(
            batch_id, video_id, 1, document, caption="the practice date feedback"
        )
        is True
    )
    assert telegram.send_document(batch_id, video_id, 1, document) is False
    assert calls == 1
    assert b"the practice date feedback" in request_bodies[0]
    assert database.delivery_status(batch_id, "video:1") == "sent"


def test_ambiguous_network_failure_is_not_resent(tmp_path):
    database, batch_id = _database(tmp_path)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("unknown outcome", request=request)

    telegram = TelegramClient(
        "token",
        "chat",
        database,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(DeliveryUncertain):
        telegram.send_text(batch_id, "summary", "hello", kind="summary")
    assert telegram.send_text(batch_id, "summary", "hello", kind="summary") is False
    assert calls == 1
    assert database.delivery_status(batch_id, "summary") == "uncertain"

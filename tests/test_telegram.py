from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from flagd_ugc.db import Database
from flagd_ugc.config import Settings
from flagd_ugc.errors import ConfigurationError, DeliveryUncertain
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


def test_each_creator_routes_videos_summaries_and_alerts_to_their_chat(tmp_path):
    destinations = {f"creator-0{index}": f"-{5000 + index}" for index in range(1, 6)}
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="",
        kling_api_key="",
        telegram_bot_token="token",
        telegram_chat_id="legacy-chat",
        telegram_creator_chat_ids=destinations,
    )
    calls = []

    def handler(request):
        if request.url.path.endswith("/sendDocument"):
            body = request.content.decode()
            chat_id = body.split('name="chat_id"\r\n\r\n', 1)[1].split("\r\n", 1)[0]
        else:
            chat_id = parse_qs(request.content.decode())["chat_id"][0]
        calls.append((request.url.path.rsplit("/", 1)[-1], chat_id))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})

    transport = httpx.MockTransport(handler)
    document = tmp_path / "video.mp4"
    document.write_bytes(b"video")
    for creator_id, chat_id in destinations.items():
        # Identical dates and ordinals remain isolated in each creator's batch state.
        database, batch_id = _database(tmp_path / creator_id)
        video_id = database.create_videos(
            batch_id, [{"hook": "practice feedback", "demo_path": "/demo.mp4"}]
        )[0]["id"]
        telegram = TelegramClient.for_creator(
            settings, creator_id, database, client=httpx.Client(transport=transport)
        )
        assert telegram.send_document(batch_id, video_id, 1, document)
        assert telegram.send_text(batch_id, "summary", "daily summary", kind="summary")
        assert telegram.send_text(batch_id, "partial-alert", "a video failed", kind="alert")
        assert not telegram.send_document(batch_id, video_id, 1, document)
        assert calls[-3:] == [
            ("sendDocument", chat_id), ("sendMessage", chat_id), ("sendMessage", chat_id)
        ]
    assert len(calls) == 15


def test_unconfigured_creator_cannot_use_original_chat(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="",
        kling_api_key="",
        telegram_bot_token="token",
        telegram_chat_id="original-chat",
    )
    database, _batch_id = _database(tmp_path)
    with pytest.raises(ConfigurationError, match="creator-02"):
        TelegramClient.for_creator(settings, "creator-02", database)

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .db import Database
from .errors import DeliveryUncertain, ProviderError, TransientError

if TYPE_CHECKING:
    from .config import Settings


class TelegramClient:
    @classmethod
    def for_creator(
        cls,
        settings: Settings,
        creator_id: str,
        database: Database,
        *,
        client: httpx.Client | None = None,
    ) -> TelegramClient:
        return cls(
            settings.telegram_bot_token,
            settings.telegram_chat_for_creator(creator_id),
            database,
            timeout=settings.request_timeout_seconds,
            client=client,
        )

    def __init__(
        self,
        token: str,
        chat_id: str,
        database: Database,
        *,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.database = database
        self.client = client or httpx.Client(timeout=timeout)
        self.base_url = f"https://api.telegram.org/bot{token}"

    @staticmethod
    def _message_id(response: httpx.Response) -> int:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError("Telegram returned non-JSON") from exc
        if response.is_error or payload.get("ok") is not True:
            description = payload.get("description", response.text[:1000])
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientError(f"Telegram temporarily failed: {description}")
            raise ProviderError(f"Telegram rejected delivery: {description}")
        return int(payload["result"]["message_id"])

    def _reserved_send(
        self,
        batch_id: int,
        key: str,
        kind: str,
        send: Any,
        *,
        video_id: int | None = None,
    ) -> bool:
        if not self.database.claim_delivery(batch_id, key, kind, video_id):
            return False
        try:
            response = send()
            message_id = self._message_id(response)
        except (ProviderError, TransientError) as exc:
            self.database.mark_delivery_error(
                batch_id, key, str(exc), safe_to_retry=True
            )
            raise
        except Exception as exc:
            # Network errors are ambiguous: at-most-once delivery takes priority over retry.
            self.database.mark_delivery_error(
                batch_id, key, str(exc), safe_to_retry=False
            )
            raise DeliveryUncertain(
                f"Telegram delivery {key!r} is uncertain and will not be repeated"
            ) from exc
        self.database.complete_delivery(batch_id, key, message_id)
        return True

    def send_document(
        self,
        batch_id: int,
        video_id: int,
        ordinal: int,
        path: Path,
        *,
        caption: str | None = None,
    ) -> bool:
        key = f"video:{ordinal}"

        def send() -> httpx.Response:
            with path.open("rb") as handle:
                data = {
                    "chat_id": self.chat_id,
                    "disable_content_type_detection": "false",
                }
                if caption:
                    data["caption"] = caption
                return self.client.post(
                    f"{self.base_url}/sendDocument",
                    data=data,
                    files={"document": (path.name, handle, "video/mp4")},
                )

        return self._reserved_send(batch_id, key, "document", send, video_id=video_id)

    def send_text(self, batch_id: int, key: str, text: str, *, kind: str) -> bool:
        return self._reserved_send(
            batch_id,
            key,
            kind,
            lambda: self.client.post(
                f"{self.base_url}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": "true",
                },
            ),
        )

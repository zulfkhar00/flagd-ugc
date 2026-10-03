from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import ssl
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from .errors import (
    ConfigurationError,
    ProviderError,
    ProviderValidationError,
    ProxyBlockError,
    QualityError,
    TransientError,
)
from .retry import retry_transient


DEFAULT_FAL_KLING_MODEL = "fal-ai/kling-video/v3/standard/image-to-video"
MAX_FAL_PROMPT_CHARS = 2500
LOGGER = logging.getLogger(__name__)


def _exception_chain(exc: BaseException) -> str:
    details: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        detail = f"{type(current).__name__}: {str(current).strip()}"
        if detail not in details:
            details.append(detail)
        current = current.__cause__ or current.__context__
    return " -> ".join(details)


def _is_certificate_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current).upper():
            return True
        current = current.__cause__ or current.__context__
    return False


def _system_trust_client(timeout: float) -> httpx.Client:
    try:
        import truststore
    except ImportError as exc:
        raise ConfigurationError(
            "truststore is required for secure fal TLS connections; run "
            "pip install -e '.[dev]'"
        ) from exc
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(timeout=timeout, verify=context, trust_env=True)


@dataclass(frozen=True)
class FalJob:
    request_id: str
    status_url: str
    response_url: str
    cancel_url: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FalJob":
        try:
            return cls(
                request_id=str(value["request_id"]),
                status_url=str(value["status_url"]),
                response_url=str(value["response_url"]),
                cancel_url=str(value["cancel_url"]),
            )
        except (KeyError, TypeError) as exc:
            raise ProviderError("saved fal request details are incomplete") from exc


class FalKlingProvider:
    """Small REST adapter for fal's persistent Kling queue."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_FAL_KLING_MODEL,
        request_timeout: float = 120.0,
        poll_seconds: float = 60.0,
        initial_poll_delay: float = 180.0,
        job_timeout: float = 1800.0,
        transient_retries: int = 3,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.model = model.strip("/")
        self.poll_seconds = poll_seconds
        self.initial_poll_delay = initial_poll_delay
        self.job_timeout = job_timeout
        self.transient_retries = transient_retries
        self.client = client or _system_trust_client(request_timeout)
        self.sleep = sleep

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def image_data_url(path: Path) -> str:
        with path.open("rb") as handle:
            header = handle.read(16)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif header.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
            raise QualityError(f"fal does not accept this starting-image type: {path.suffix}")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _is_html_proxy_block(response: httpx.Response) -> bool:
        if response.status_code != 403:
            return False
        content_type = response.headers.get("content-type", "").lower()
        return "text/html" in content_type or "ns-template" in response.text.lower()

    @staticmethod
    def _is_bloomberg_policy_block(response: httpx.Response) -> bool:
        if response.status_code != 403:
            return False
        body = response.text.lower()
        return any(
            marker in body
            for marker in (
                "x-bbg-block",
                "bloomberg proxy exception page",
                "cust_policy_denied",
            )
        )

    @staticmethod
    def _raise_proxy_block(response: httpx.Response, context: str) -> None:
        if FalKlingProvider._is_bloomberg_policy_block(response):
            raise ProviderError(
                f"fal {context} was denied by Bloomberg web policy "
                "(cust_policy_denied; category Suspicious). Retrying on this network "
                "cannot succeed. Ask IT to allow queue.fal.run and *.fal.media, or "
                "resume the saved request from an approved network."
            )
        if FalKlingProvider._is_html_proxy_block(response):
            raise ProxyBlockError(
                f"fal {context} was temporarily blocked by the HTTPS proxy (403 HTML)"
            )

    @staticmethod
    def _check(response: httpx.Response, context: str) -> dict[str, Any]:
        FalKlingProvider._raise_proxy_block(response, context)
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TransientError(
                f"fal {context} temporarily failed ({response.status_code})"
            )
        if response.is_error:
            raise ProviderError(
                f"fal {context} failed ({response.status_code}): {response.text[:1000]}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"fal {context} returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError(f"fal {context} returned an invalid response")
        return payload

    def _request_json(
        self, method: str, url: str, *, context: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                url,
                headers={**self.headers, "Connection": "close"},
                **kwargs,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if _is_certificate_error(exc):
                raise ProviderError(
                    "fal TLS certificate verification failed. The configured HTTPS "
                    "proxy certificate is not trusted by the active certificate store; "
                    "do not disable TLS verification. Details: " + _exception_chain(exc)
                ) from exc
            raise TransientError(
                f"fal {context} request failed: {_exception_chain(exc)}"
            ) from exc
        return self._check(response, context)

    def _request_result_if_ready(self, url: str) -> dict[str, Any] | None:
        """Fetch a completed result directly, tolerating documented not-ready states."""
        try:
            response = self.client.request(
                "GET",
                url,
                headers={**self.headers, "Connection": "close"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if _is_certificate_error(exc):
                raise ProviderError(
                    "fal result TLS certificate verification failed. Details: "
                    + _exception_chain(exc)
                ) from exc
            raise TransientError(
                f"fal result request failed: {_exception_chain(exc)}"
            ) from exc
        self._raise_proxy_block(response, "result")
        if response.status_code == 422:
            body = response.text.lower()
            if "string_too_long" in body and '"prompt"' in body:
                raise ProviderValidationError(
                    "fal rejected the saved Kling request because its prompt exceeded "
                    f"{MAX_FAL_PROMPT_CHARS:,} characters"
                )
        if response.status_code == 202:
            return None
        if response.status_code in {400, 409, 425}:
            body = response.text.lower()
            if any(
                marker in body
                for marker in (
                    "in progress",
                    "not ready",
                    "not completed",
                    "still running",
                    "in queue",
                )
            ):
                return None
        payload = self._check(response, "result")
        result = payload.get("data", payload)
        video = result.get("video", {}) if isinstance(result, dict) else {}
        if str(video.get("url", "")).strip():
            return payload
        status = str(payload.get("status", "")).upper()
        if status in {"IN_QUEUE", "IN_PROGRESS", "PENDING"}:
            return None
        raise ProviderError("fal Kling result returned no video URL")

    def check_connection(self, attempts: int = 3) -> dict[str, Any]:
        """Force fresh, free TLS connections to fal before any paid submission."""
        if attempts < 1:
            raise ValueError("fal check attempts must be positive")
        statuses: list[int] = []
        for _ in range(attempts):
            try:
                response = self.client.get(
                    "https://queue.fal.run/",
                    headers={"Connection": "close"},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if _is_certificate_error(exc):
                    raise ProviderError(
                        "fal TLS certificate verification failed. The configured HTTPS "
                        "proxy certificate is not trusted by the macOS certificate store. "
                        "Do not disable TLS verification. Details: "
                        + _exception_chain(exc)
                    ) from exc
                raise TransientError(
                    "fal connectivity check failed: " + _exception_chain(exc)
                ) from exc
            statuses.append(response.status_code)
        return {"status": "ok", "fresh_tls_connections": attempts, "http_statuses": statuses}

    def check_queue_access(self) -> dict[str, Any]:
        """Check a queue-shaped URL without creating an inference request."""
        probe_id = "00000000-0000-0000-0000-000000000000"
        url = f"https://queue.fal.run/{self.model}/requests/{probe_id}/status"
        try:
            response = self.client.get(
                url,
                headers={**self.headers, "Connection": "close"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientError(
                "fal queue-path check failed: " + _exception_chain(exc)
            ) from exc
        self._raise_proxy_block(response, "queue-path check")
        # A fabricated request ID never reaches inference. Depending on fal's
        # edge/router version, a reachable queue route answers 400, 404, or
        # 405. Each proves that the managed proxy allowed the queue-shaped URL.
        if response.status_code not in {400, 404, 405}:
            if response.is_error:
                raise ProviderError(
                    f"fal queue-path check failed ({response.status_code})"
                )
        return {"status": "ok", "http_status": response.status_code}

    def submit(
        self,
        scene: Path,
        prompt: str,
        *,
        duration: int = 3,
        allow_hand_entry: bool = False,
    ) -> FalJob:
        if duration not in range(3, 16):
            raise ValueError("Kling duration must be between 3 and 15 seconds")
        if not prompt.strip():
            raise ValueError("motion prompt must not be empty")
        normalized_prompt = prompt.strip()
        if len(normalized_prompt) > MAX_FAL_PROMPT_CHARS:
            raise ProviderValidationError(
                "Kling motion prompt exceeds fal's "
                f"{MAX_FAL_PROMPT_CHARS:,}-character limit"
            )
        negative_prompt = (
            "face morph, identity change, deformed face, deformed hands, extra "
            "fingers, extra limbs, warped body, duplicate person, camera cut, "
            "scene change, text, logo, beauty filter, plastic skin, talking, "
            "speaking, lip-sync, silent mouthing, phoneme mouth shapes, "
            "visible phone, mirror selfie, camera equipment"
        )
        if not allow_hand_entry:
            negative_prompt += ", hand entering frame"
        payload = self._request_json(
            "POST",
            f"https://queue.fal.run/{self.model}",
            context="submission",
            json={
                "start_image_url": self.image_data_url(scene),
                "prompt": normalized_prompt,
                "duration": str(duration),
                "generate_audio": False,
                "shot_type": "customize",
                "negative_prompt": negative_prompt,
                "cfg_scale": 0.5,
            },
        )
        request_id = str(payload.get("request_id", "")).strip()
        if not request_id:
            raise ProviderError("fal submission returned no request_id")
        request_path = (
            f"https://queue.fal.run/{self.model}/requests/{quote(request_id)}"
        )
        return FalJob(
            request_id=request_id,
            status_url=str(payload.get("status_url") or f"{request_path}/status"),
            response_url=str(
                payload.get("response_url") or f"{request_path}/response"
            ),
            cancel_url=str(payload.get("cancel_url") or f"{request_path}/cancel"),
        )

    def wait_and_download(
        self,
        job: FalJob,
        output: Path,
        *,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        new_submission: bool = False,
        prefer_result_endpoint: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.job_timeout
        consecutive_transient_failures = 0
        if new_submission and self.initial_poll_delay > 0:
            if on_status is not None:
                on_status(
                    {
                        "status": "WAITING_BEFORE_POLL",
                        "delay_seconds": self.initial_poll_delay,
                    }
                )
            self.sleep(self.initial_poll_delay)
        response_payload: dict[str, Any] | None = None
        if prefer_result_endpoint:
            while time.monotonic() < deadline:
                try:
                    response_payload = self._request_result_if_ready(job.response_url)
                except ProxyBlockError as exc:
                    LOGGER.warning(
                        "fal result retrieval was blocked by the HTTPS proxy for %s; "
                        "falling back to the status endpoint: %s",
                        job.request_id,
                        exc,
                    )
                    if on_status is not None:
                        on_status({"status": "RESULT_PROXY_BLOCK", "error": str(exc)})
                    break
                if response_payload is not None:
                    if on_status is not None:
                        on_status({"status": "COMPLETED"})
                    break
                if on_status is not None:
                    on_status({"status": "RESULT_NOT_READY"})
                break
        while time.monotonic() < deadline:
            if response_payload is not None:
                break
            try:
                status_payload = self._request_json(
                    "GET",
                    job.status_url,
                    context="status",
                )
            except ProxyBlockError as exc:
                LOGGER.warning(
                    "fal polling was blocked by the HTTPS proxy for %s; cooling down: %s",
                    job.request_id,
                    exc,
                )
                if on_status is not None:
                    on_status({"status": "PROXY_COOLDOWN", "error": str(exc)})
                consecutive_transient_failures += 1
                backoff = min(
                    180.0 * consecutive_transient_failures,
                    300.0,
                )
                self.sleep(backoff)
                continue
            except TransientError as exc:
                LOGGER.warning(
                    "temporary fal polling failure for %s; continuing: %s",
                    job.request_id,
                    exc,
                )
                if on_status is not None:
                    on_status({"status": "RETRYING", "error": str(exc)})
                consecutive_transient_failures += 1
                backoff = min(30.0 * (2 ** (consecutive_transient_failures - 1)), 120.0)
                self.sleep(backoff)
                continue
            consecutive_transient_failures = 0
            if on_status is not None:
                on_status(status_payload)
            status = str(status_payload.get("status", "")).upper()
            if status == "COMPLETED":
                if status_payload.get("error"):
                    raise ProviderError(
                        f"fal Kling generation failed: {status_payload['error']}"
                    )
                break
            if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                raise ProviderError(f"fal returned unknown request status: {status!r}")
            self.sleep(self.poll_seconds)
        else:
            raise TransientError(
                f"fal request {job.request_id} did not complete before timeout; resume it"
            )

        if response_payload is None:
            response_payload = retry_transient(
                lambda: self._request_json(
                    "GET", job.response_url, context="result"
                ),
                retries=self.transient_retries,
                sleep=self.sleep,
            )
        result = response_payload.get("data", response_payload)
        video = result.get("video", {}) if isinstance(result, dict) else {}
        video_url = str(video.get("url", "")).strip()
        if not video_url:
            raise ProviderError("fal Kling result returned no video URL")
        retry_transient(
            lambda: self._download(video_url, output),
            retries=self.transient_retries,
            sleep=self.sleep,
        )
        return response_payload

    def _download(self, url: str, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.unlink(missing_ok=True)
        try:
            try:
                with self.client.stream(
                    "GET", url, headers={"Connection": "close"}
                ) as response:
                    if response.status_code == 403:
                        response.read()
                    self._raise_proxy_block(response, "video download")
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise TransientError(
                            f"fal video download temporarily failed ({response.status_code})"
                        )
                    if response.is_error:
                        raise ProviderError(
                            f"fal video download failed ({response.status_code})"
                        )
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if _is_certificate_error(exc):
                    raise ProviderError(
                        "fal video download TLS verification failed: "
                        + _exception_chain(exc)
                    ) from exc
                raise TransientError(
                    f"fal video download failed: {_exception_chain(exc)}"
                ) from exc
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise QualityError("fal returned an empty video")
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)

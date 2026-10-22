"""Authenticated instruction API for a separate scheduler/content service."""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx

from .controller import ControlError, MAX_REQUEST, control_request


async def api_request(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    parts = urlsplit(base_url)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment
            or (parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost"))):
        raise ValueError("use local loopback HTTP or a trusted HTTPS instruction endpoint")
    if len(token) < 32:
        raise ValueError("configure PHONE_CONTROL_TOKEN for the instruction API")
    operation, body = payload["operation"], None
    if operation in ("submit", "enqueue"):
        method, route = "POST", "/tasks" if operation == "submit" else "/queue"
        body = {"instruction": payload["task"]["goal"],
                "options": {k: v for k, v in payload["task"].items() if k != "goal"},
                "execute": payload["execute"], "request_id": payload["request_id"]}
    elif operation == "status":
        method, route = "GET", "/tasks/" + payload["job_id"] if payload.get("job_id") else "/status"
    elif operation == "cancel":
        method, route = "POST", "/tasks/" + payload["job_id"] + "/cancel"
    elif operation == "queue_status":
        method, route = "GET", "/queue" + ("/" + payload["item_id"] if payload.get("item_id") else "")
    elif operation == "queue_cancel":
        method, route = "POST", "/queue/" + payload["item_id"] + "/cancel"
    else:
        raise ValueError("unsupported instruction API operation")
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.request(method, base_url.rstrip("/") + route, json=body,
                                        headers={"Authorization": "Bearer " + token})
    value = response.json()
    if response.is_error:
        raise ControlError(value.get("error", "api_error"), value.get("message", "Instruction API rejected the request."))
    return value


def instruction_request(body: Any, *, operation: str = "submit") -> dict[str, Any]:
    if (not isinstance(body, dict) or set(body) - {"instruction", "options", "execute", "request_id"}
            or not isinstance(body.get("instruction"), str) or not body["instruction"].strip()):
        raise ValueError("supply an instruction and optional task options")
    options = body.get("options", {})
    if not isinstance(options, dict) or "goal" in options:
        raise ValueError("options cannot replace the instruction")
    # Validate before crossing the private controller boundary or calling AI.
    from .runner import validate_task
    task = validate_task({**options, "goal": body["instruction"]})
    execute = body.get("execute", False)
    if type(execute) is not bool:
        raise ValueError("execute must be a boolean")
    request_id = body.get("request_id")
    if not isinstance(request_id, str):
        raise ValueError("supply a stable request_id UUID for delivery deduplication")
    return {"operation": operation, "task": task, "execute": execute,
            "request_id": str(uuid.UUID(request_id))}


@contextmanager
def instruction_server(socket_path: Path, token: str, *, bind: str = "127.0.0.1",
                       port: int = 8788) -> Iterator[ThreadingHTTPServer]:
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("configure a PHONE_CONTROL_TOKEN of at least 32 characters")
    # Put this local endpoint behind a trusted TLS tunnel/reverse proxy for
    # remote operation. Never expose bearer credentials over plain LAN HTTP.
    if bind not in ("127.0.0.1", "localhost"):
        raise ValueError("instruction API binds to loopback; use a secure tunnel for remote clients")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass  # No bearer headers, instructions or supplied copy in access logs.

        def reply(self, code: int, value: dict[str, Any]) -> None:
            encoded = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def handle_request(self) -> None:
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.reply(401, {"error": "unauthorized"})
                return
            try:
                if self.command == "GET" and self.path == "/status":
                    payload = {"operation": "status"}
                elif self.command == "GET" and self.path == "/queue":
                    payload = {"operation": "queue_status"}
                elif self.command == "GET" and re.fullmatch(r"/queue/[a-f0-9]{32}", self.path):
                    payload = {"operation": "queue_status", "item_id": self.path.rsplit("/", 1)[1]}
                elif self.command == "GET" and re.fullmatch(r"/tasks/[a-f0-9]{32}", self.path):
                    payload = {"operation": "status", "job_id": self.path.rsplit("/", 1)[1]}
                elif self.command == "POST" and self.path in ("/tasks", "/queue"):
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_REQUEST:
                        self.reply(413, {"error": "invalid_body_size"})
                        return
                    if self.headers.get_content_type() != "application/json":
                        self.reply(415, {"error": "use_application_json"})
                        return
                    payload = instruction_request(json.loads(self.rfile.read(length)),
                                                  operation="enqueue" if self.path == "/queue" else "submit")
                elif self.command == "POST" and re.fullmatch(r"/queue/[a-f0-9]{32}/cancel", self.path):
                    payload = {"operation": "queue_cancel", "item_id": self.path.split("/")[2]}
                elif self.command == "POST" and re.fullmatch(r"/tasks/[a-f0-9]{32}/cancel", self.path):
                    payload = {"operation": "cancel", "job_id": self.path.split("/")[2]}
                else:
                    self.reply(404, {"error": "not_found"})
                    return
                value = asyncio.run(control_request(socket_path, payload))
                self.reply(202 if payload["operation"] in ("submit", "enqueue") else 200, value)
            except ControlError as error:
                self.reply(409, {"error": error.code, "message": str(error)})
            except (ValueError, TypeError, UnicodeDecodeError):
                self.reply(400, {"error": "invalid_request"})
            except Exception:
                self.reply(503, {"error": "controller_unavailable"})

        def do_GET(self) -> None:
            self.handle_request()

        def do_POST(self) -> None:
            self.handle_request()

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

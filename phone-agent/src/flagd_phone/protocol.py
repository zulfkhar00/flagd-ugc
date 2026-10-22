from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any


class PortalError(RuntimeError):
    pass


class UncertainAction(PortalError):
    """An input may have executed; it must not be automatically replayed."""


class PortalConnection:
    """Portal's reverse JSON-RPC protocol, with one command in flight.

    Portal sends events without IDs. They must never satisfy a command request.
    A gesture acknowledgement means dispatched, not visually completed.
    """

    def __init__(self, websocket: Any, *, timeout: float = 30):
        self.websocket = websocket
        self.timeout = timeout
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.lock = asyncio.Lock()
        self.uncertain = False
        self.reader = asyncio.create_task(self._receive())

    async def _receive(self) -> None:
        try:
            async for raw in self.websocket:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    continue
                future = self.pending.get(str(payload.get("id", "")))
                if future is not None and not future.done():
                    future.set_result(payload)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(PortalError("phone disconnected"))

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, mutation: bool = False
    ) -> Any:
        async with self.lock:
            if mutation and self.uncertain:
                raise UncertainAction("inspect the phone before another input")
            if self.reader.done():
                raise PortalError("phone disconnected")
            request_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            try:
                await self.websocket.send(json.dumps({
                    "id": request_id, "method": method, "params": params or {},
                }))
                reply = await asyncio.wait_for(future, self.timeout)
                if reply.get("status") != "success":
                    raise PortalError(f"Portal rejected {method}: {reply.get('error', 'invalid reply')}")
                if "result" not in reply:
                    raise PortalError("Portal reply has no result")
                return reply["result"]
            except asyncio.CancelledError:
                if mutation:
                    self.uncertain = True
                raise
            except Exception as exc:
                if mutation:
                    self.uncertain = True
                    raise UncertainAction(
                        f"{method} outcome is uncertain; stop and inspect, do not replay"
                    ) from exc
                raise PortalError(f"{method} failed ({type(exc).__name__})") from exc
            finally:
                self.pending.pop(request_id, None)
                if not future.done():
                    future.cancel()

    async def close(self) -> None:
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve
from websockets.datastructures import MultipleValuesError

from .model import GeminiVision
from .protocol import PortalConnection
from .runner import Journal, load_task, run_agent, screenshot, validate_task
from .reports import write_report
from .session import SessionStore
from .device import ensure_screen_awake, park_phone
from .controller import TaskController, control_request, control_server


class PhoneHost:
    def __init__(self, token: str, device_id: str | None = None, *, persistent: bool = False):
        if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("PHONE_DEVICE_TOKEN needs at least 32 ASCII characters without whitespace")
        self.token = token
        self.device_id = device_id
        self.persistent = persistent
        self.current: PortalConnection | None = None
        self.connected: asyncio.Future[PortalConnection] = asyncio.get_running_loop().create_future()
        self.finished = asyncio.Event()

    def authorize(self, connection: Any, request: Any) -> Any:
        def reject(status: HTTPStatus, reason: str) -> Any:
            # Report the cause without exposing tokens or arbitrary request data.
            print(f"Portal connection rejected: {reason.strip()}", flush=True)
            return connection.respond(status, reason)

        try:
            authorization = request.headers.get("Authorization", "")
            device_id = request.headers.get("X-Device-ID", "")
            if request.path != "/portal":
                return reject(HTTPStatus.NOT_FOUND, "unknown endpoint (expected /portal)\n")
            if not hmac.compare_digest(authorization, "Bearer " + self.token):
                has_bearer = authorization.startswith("Bearer ")
                received = authorization[7:] if has_bearer else ""
                differences = [
                    index + 1 for index, (actual, expected) in
                    enumerate(zip(received, self.token)) if actual != expected
                ]
                print(
                    "Token diagnostic: "
                    f"bearer_header={has_bearer}, received_length={len(received)}, "
                    f"expected_length={len(self.token)}, mismatch_positions={differences}",
                    flush=True,
                )
                return reject(HTTPStatus.UNAUTHORIZED, "invalid device token\n")
            if not device_id or (self.device_id and device_id != self.device_id):
                return reject(HTTPStatus.FORBIDDEN, "missing or unexpected device ID\n")
            if self.connected.done() and not self.persistent:
                return reject(HTTPStatus.CONFLICT, "one phone per run\n")
        except (MultipleValuesError, TypeError, ValueError):
            return reject(HTTPStatus.BAD_REQUEST, "invalid headers\n")
        return None

    async def handle(self, websocket: Any) -> None:
        if self.connected.done() and not self.persistent:
            await websocket.close(code=1008, reason="phone already connected")
            return
        portal = PortalConnection(websocket)
        previous = self.current
        self.current = portal
        self.device_id = self.device_id or websocket.request.headers["X-Device-ID"]
        if not self.connected.done():
            self.connected.set_result(portal)
        else:
            print("Paired phone reconnected. Task inputs are not automatically resumed.", flush=True)
        if previous is not None:
            # Abort the old transport; pending mutations become uncertain and
            # must never be replayed against the replacement connection.
            await previous.close()
        print(f"Phone connected: {websocket.request.headers['X-Device-ID']}", flush=True)
        finish_waiter = asyncio.create_task(self.finished.wait())
        try:
            await asyncio.wait((finish_waiter, portal.reader), return_when=asyncio.FIRST_COMPLETED)
        finally:
            finish_waiter.cancel()
            await asyncio.gather(finish_waiter, return_exceptions=True)
            await portal.close()
            if self.current is portal:
                self.current = None
                if self.persistent and not self.finished.is_set():
                    print("Phone disconnected; listener is waiting for reconnection.", flush=True)


async def run(args: argparse.Namespace) -> None:
    if args.command == "gateway":
        from .gateway import instruction_server
        with instruction_server(args.control_socket, os.environ.get("PHONE_CONTROL_TOKEN", ""),
                                port=args.port):
            print(f"Instruction API: http://127.0.0.1:{args.port} (Bearer authentication required)", flush=True)
            await asyncio.Event().wait()
        return
    if args.command == "schedule-preview":
        from .scheduler import schedule_preview
        print(json.dumps(schedule_preview(json.loads(args.config.read_text()), datetime.now(timezone.utc))))
        return
    if args.command == "make-browse-task":
        from .workflows import browse_task
        print(json.dumps(browse_task(app=args.app, account=args.account, keywords=args.keyword,
                                    seconds=args.minutes * 60, max_likes=args.max_likes,
                                    max_comments=args.max_comments), ensure_ascii=False))
        return
    if args.command == "make-slideshow-task":
        from .workflows import slideshow_task
        source = json.loads(args.source.read_text())
        print(json.dumps(slideshow_task(**source, publish=args.publish), ensure_ascii=False))
        return
    if args.command == "report":
        store = SessionStore(args.run_dir, create=False)
        try:
            print(json.dumps(write_report(store)))
        finally:
            store.close()
        return
    if args.command in ("submit", "enqueue", "queue-status", "queue-cancel", "queue-expedite", "queue-reconcile-profile",
                        "status", "cancel", "capture", "acknowledge", "park"):
        await run_client(args)
        return
    if args.command == "serve":
        await run_service(args)
        return
    # Validate the task and credentials before listening or making any request.
    task = task_from_args(args) if args.command == "run" else None
    host = PhoneHost(os.environ.get("PHONE_DEVICE_TOKEN", ""), args.device_id,
                     persistent=args.keep_connected)
    model = GeminiVision(os.environ.get("PHONE_GEMINI_API_KEY", ""),
                         os.environ.get("PHONE_VISION_MODEL", "")) if task else None
    if model:
        model.browse_model = os.environ.get("PHONE_BROWSE_MODEL") or None
        model.navigation_model = os.environ.get("PHONE_NAV_MODEL") or None
        model.semantic_model = os.environ.get("PHONE_SEMANTIC_MODEL") or None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = args.runs_dir / f"{stamp}-{uuid.uuid4().hex[:8]}"
    journal = Journal(directory)
    try:
        async with serve(host.handle, args.bind, args.port, process_request=host.authorize,
                         max_size=32 * 1024 * 1024, compression=None):
            print(f"Waiting for Portal at {args.bind}:{args.port}/portal", flush=True)
            print(f"Artifacts: {directory}", flush=True)
            try:
                while True:
                    try:
                        portal = await asyncio.wait_for(asyncio.shield(host.connected), args.connect_timeout)
                        break
                    except TimeoutError:
                        if not args.keep_connected:
                            raise
                        print("Still waiting for the paired phone; listener remains available.", flush=True)
                try:
                    if args.keep_screen_awake:
                        print("Enabling Portal's screen-awake watchdog...", flush=True)
                        await ensure_screen_awake(portal, journal)
                        print("Screen awake. Starting inspection or task.", flush=True)
                    if args.command == "inspect":
                        frame = await screenshot(portal)
                        journal.frame(0, frame)
                        print(json.dumps({"status": "screenshot_saved", "width": frame.width,
                                          "height": frame.height, "path": str(directory / '000.png')}))
                    else:
                        result = await run_agent(portal, model, task, journal, execute=args.execute)
                        if (args.park_on_finish and args.execute
                                and result["status"] in ("completion_candidate", "duration_limit")):
                            try:
                                result["cleanup"] = {"status": "completed", **await park_phone(portal, journal)}
                            except Exception as error:
                                result["cleanup"] = {"status": "failed", "error_type": type(error).__name__}
                                journal.record("screen_park_failed", error_type=type(error).__name__)
                        print(json.dumps(result))
                except Exception as error:
                    if not args.keep_connected:
                        raise
                    journal.record("task_stopped_connection_kept", error_type=type(error).__name__)
                    print(f"Task stopped ({type(error).__name__}); connection remains available.", flush=True)
                if args.keep_connected:
                    print("Listener stays open for this phone. Ctrl+C stops it.", flush=True)
                    await host.finished.wait()
            finally:
                host.finished.set()
    finally:
        host.finished.set()
        if model:
            await model.close()


async def run_service(args: argparse.Namespace) -> None:
    if args.schedule:
        from .scheduler import validate_schedule
        validate_schedule(json.loads(args.schedule.read_text()))
    host = PhoneHost(os.environ.get("PHONE_DEVICE_TOKEN", ""), args.device_id, persistent=True)
    model = GeminiVision(os.environ.get("PHONE_GEMINI_API_KEY", ""),
                         os.environ.get("PHONE_VISION_MODEL", ""))
    model.browse_model = os.environ.get("PHONE_BROWSE_MODEL") or None
    model.navigation_model = os.environ.get("PHONE_NAV_MODEL") or None
    model.semantic_model = os.environ.get("PHONE_SEMANTIC_MODEL") or None
    controller = None
    try:
        async with serve(host.handle, args.bind, args.port, process_request=host.authorize,
                         max_size=32 * 1024 * 1024, compression=None):
            controller = TaskController(host, model, args.runs_dir, keep_awake=args.keep_screen_awake,
                                        park_on_finish=args.park_on_finish)
            try:
                async with control_server(controller, args.control_socket):
                    print(f"Waiting for Portal at {args.bind}:{args.port}/portal", flush=True)
                    print(f"Task control ready: {args.control_socket.resolve()}", flush=True)
                    print("Send instructions with submit. No tasks or AI requests run while idle.", flush=True)
                    if args.schedule:
                        from .scheduler import DailyScheduler
                        schedule = asyncio.create_task(DailyScheduler(controller, args.schedule).run())
                        waiting = asyncio.create_task(host.finished.wait())
                        try:
                            done, _ = await asyncio.wait([schedule, waiting], return_when=asyncio.FIRST_COMPLETED)
                            for finished in done:
                                if not finished.cancelled() and finished.exception():
                                    raise finished.exception()
                        finally:
                            schedule.cancel()
                            waiting.cancel()
                            await asyncio.gather(schedule, waiting, return_exceptions=True)
                    else:
                        await host.finished.wait()
            finally:
                await controller.close()
                host.finished.set()
    finally:
        host.finished.set()
        await model.close()


async def run_client(args: argparse.Namespace) -> None:
    async def send(payload: dict[str, Any]) -> dict[str, Any]:
        if getattr(args, "api_url", None):
            from .gateway import api_request
            return await api_request(args.api_url, os.environ.get("PHONE_CONTROL_TOKEN", ""), payload)
        return await control_request(args.control_socket, payload)
    if args.command in ("submit", "enqueue"):
        task = task_from_args(args)
        request_id = args.request_id or str(uuid.uuid4())
        # The caller can reuse this receipt ID if the reply is lost. The client
        # never automatically retries a submission or resubmits on wait timeout.
        print(f"Request ID: {request_id}", file=sys.stderr, flush=True)
        payload = {"operation": args.command, "task": task, "execute": args.execute,
                   "request_id": request_id}
    else:
        payload = {"operation": args.command}
        if args.command in ("status", "cancel") and args.job_id is not None:
            payload["job_id"] = args.job_id
        if args.command in ("queue-status", "queue-cancel", "queue-expedite", "queue-reconcile-profile"):
            payload["operation"] = args.command.replace("-", "_")
            if args.item_id is not None:
                payload["item_id"] = args.item_id
            if args.command == "queue-reconcile-profile":
                payload["expected_headline"] = args.expected_headline
        if args.command == "acknowledge":
            payload["frame_id"] = args.frame_id
        if args.command == "capture" and args.show_overlay:
            payload["show_overlay"] = True
    result = await send(payload)
    print(json.dumps(result), flush=True)
    if args.command != "submit" or not args.wait:
        return
    deadline = asyncio.get_running_loop().time() + args.wait_timeout
    job_id = result["job"]["id"]
    while result["job"]["state"] in ("running", "cancelling"):
        if asyncio.get_running_loop().time() >= deadline:
            print(json.dumps({"wait_timed_out": True, "job_id": job_id,
                              "task_cancelled": False}), flush=True)
            return
        await asyncio.sleep(0.5)
        result = await send({"operation": "status", "job_id": job_id})
    print(json.dumps(result), flush=True)


def task_from_args(args: argparse.Namespace) -> dict[str, Any]:
    task = load_task(args.task) if args.task else {"goal": args.goal, "text_fields": {}}
    fields = dict(task.get("text_fields", {}))
    for item in args.text:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("--text must be FIELD=VALUE")
        if key in fields:
            raise ValueError("--text cannot repeat or overwrite a supplied field")
        fields[key] = value
    task["text_fields"] = fields
    if args.allow_robot_verification:
        task["allow_robot_verification"] = True
    if args.max_steps is not None:
        task["max_steps"] = args.max_steps
    return validate_task(task)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Self-hosted one-phone visual-control prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    socket_default = Path(os.environ.get("PHONE_CONTROL_SOCKET", "phone-agent/.control.sock"))
    for name in ("inspect", "run", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--bind", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8787)
        command.add_argument("--device-id", default=os.environ.get("PHONE_DEVICE_ID"))
        command.add_argument("--connect-timeout", type=float, default=120)
        command.add_argument("--runs-dir", type=Path, default=Path("phone-agent/runs"))
        command.add_argument("--keep-connected", action="store_true",
                             help="keep the phone connected after inspection or task completion")
        command.add_argument(
            "--keep-screen-awake", action=argparse.BooleanOptionalAction,
            default=os.environ.get("PHONE_KEEP_SCREEN_AWAKE", "").lower() in
                    {"1", "true", "yes", "on"},
            help="enable Portal's persistent screen-awake watchdog before capture or AI requests",
        )
        if name in ("run", "serve"):
            command.add_argument(
                "--park-on-finish", action=argparse.BooleanOptionalAction,
                default=os.environ.get("PHONE_PARK_ON_FINISH", "").lower() in {"1", "true", "yes", "on"},
                help="return Home, disable the wake watchdog and lock the screen after successful tasks",
            )
        if name == "serve":
            command.add_argument("--control-socket", type=Path, default=socket_default)
            command.add_argument("--schedule", type=Path, help="optional California daily browsing schedule JSON")
        if name == "run":
            instruction = command.add_mutually_exclusive_group(required=True)
            instruction.add_argument("--goal", help="plain-language instruction for the phone")
            instruction.add_argument("--task", type=Path, help="structured task JSON")
            command.add_argument("--text", action="append", default=[], metavar="FIELD=VALUE",
                                 help="exact text the agent may type; can be repeated")
            command.add_argument("--max-steps", type=int, help="override the maximum number of decisions")
            command.add_argument("--execute", action="store_true",
                                 help="execute bounded navigation; default is one decision preview")
            command.add_argument("--allow-robot-verification", action="store_true",
                                 help="operator authorizes solving ordinary visible robot checks for this task")
    for name in ("submit", "enqueue", "queue-status", "queue-cancel", "queue-expedite", "queue-reconcile-profile",
                 "status", "cancel", "capture", "acknowledge", "park"):
        command = sub.add_parser(name, help="control the already running phone service")
        command.add_argument("--control-socket", type=Path, default=socket_default)
        if name in ("submit", "enqueue", "queue-status", "queue-cancel", "queue-expedite", "queue-reconcile-profile", "status", "cancel"):
            command.add_argument("--api-url", help="send through authenticated instruction HTTP API instead of the local socket")
        if name in ("submit", "enqueue"):
            instruction = command.add_mutually_exclusive_group(required=True)
            instruction.add_argument("--goal", help="plain-language instruction for the phone")
            instruction.add_argument("--task", type=Path, help="structured task JSON")
            command.add_argument("--text", action="append", default=[], metavar="FIELD=VALUE")
            command.add_argument("--max-steps", type=int)
            command.add_argument("--execute", action="store_true", help="run inputs; otherwise preview only")
            command.add_argument("--allow-robot-verification", action="store_true")
            command.add_argument("--request-id", help="reuse a submission UUID to retrieve its receipt without rerunning")
            if name == "submit":
                command.add_argument("--wait", action="store_true", help="poll for the task result without AI calls")
                command.add_argument("--wait-timeout", type=float, default=300)
        elif name == "status":
            command.add_argument("--job", dest="job_id", help="show a particular task's saved result")
        elif name == "queue-status":
            command.add_argument("item_id", nargs="?", help="show one queue item or the whole queue")
        elif name == "queue-cancel":
            command.add_argument("item_id", help="cancel a queued item")
        elif name == "queue-expedite":
            command.add_argument("item_id", help="retry a waiting item now when the previous attempt had no public action")
        elif name == "queue-reconcile-profile":
            command.add_argument("item_id", help="blocked queue item after an uncertain Post")
            command.add_argument("expected_headline", help="exact first-slide headline visible on the newest profile tile")
        elif name == "cancel":
            command.add_argument("job_id")
        elif name == "acknowledge":
            command.add_argument("frame_id", help="ID of a fresh capture the operator has inspected")
        elif name == "capture":
            command.add_argument("--show-overlay", action="store_true", help="include Portal overlay while diagnosing black screenshots")
    report = sub.add_parser("report", help="rebuild reports from saved memory without AI or phone inputs")
    report.add_argument("--run-dir", type=Path, required=True)
    schedule = sub.add_parser("schedule-preview", help="check next California slots without AI or phone inputs")
    schedule.add_argument("--config", type=Path, required=True)
    browse = sub.add_parser("make-browse-task", help="emit a bounded browsing task JSON; does not execute it")
    browse.add_argument("--app", choices=("TikTok", "Instagram"), default="TikTok")
    browse.add_argument("--account", required=True)
    browse.add_argument("--keyword", action="append", required=True)
    browse.add_argument("--minutes", type=int, default=15)
    browse.add_argument("--max-likes", type=int, default=0)
    browse.add_argument("--max-comments", type=int, default=0)
    posting = sub.add_parser("make-slideshow-task", help="split caption and emit a Volume phone workflow; does not execute it")
    posting.add_argument("--source", type=Path, required=True,
                         help="JSON with account, volume_id, share_url, caption, slide_count")
    posting.add_argument("--publish", action="store_true", help="authorize one Post tap for this specific source/account")
    gateway = sub.add_parser("gateway", help="authenticated local HTTP API for an external instruction service")
    gateway.add_argument("--control-socket", type=Path, default=socket_default)
    gateway.add_argument("--port", type=int, default=8788)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped. Inspect the phone before retrying an unfinished task.")
    except ValueError as exc:
        parser.exit(1, f"Configuration or decision rejected: {exc}\n")
    except Exception as exc:
        # No network exception payloads or credentials in console output.
        parser.exit(1, f"Stopped ({type(exc).__name__}). Check configuration and the local trace.\n")


if __name__ == "__main__":
    main()

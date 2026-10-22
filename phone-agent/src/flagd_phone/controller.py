from __future__ import annotations

import asyncio
import base64
import copy
import fcntl
import json
import os
import stat
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .device import ensure_screen_awake, park_phone
from .runner import Journal, run_agent, screenshot, validate_task
from .vision import Frame


MAX_REQUEST = 256 * 1024


class ControlError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TaskController:
    """One task owns the paired phone; new goals never replay previous inputs."""

    def __init__(self, host: Any, model: Any, runs_dir: Path, *, keep_awake: bool = False,
                 park_on_finish: bool = False, retry_base_seconds: float = 15):
        self.host = host
        self.model = model
        self.runs_dir = runs_dir.resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.runs_dir / "controller-jobs.json"
        descriptor = os.open(self.state_path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.owner_lock = os.fdopen(descriptor, "w")
        try:
            fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.owner_lock.close()
            raise ControlError("controller_in_use", "Another controller owns this task directory.") from error
        self.jobs: dict[str, dict[str, Any]] = {}
        self.queue: list[dict[str, Any]] = []
        self.queue_worker_error: str | None = None
        self.requests: dict[str, str] = {}
        self.active: asyncio.Task[None] | None = None
        self.active_id: str | None = None
        self.capturing = False
        self.closed = False
        self.keep_awake = keep_awake
        self.park_on_finish = park_on_finish
        self.retry_base_seconds = retry_base_seconds
        self.recovery_required = False
        self.recovery_capture: tuple[str, float, Any] | None = None
        if self.state_path.exists():
            saved = json.loads(self.state_path.read_text())
            self.jobs = saved["jobs"]
            self.queue = saved.get("queue", [])
            self.recovery_required = saved["recovery_required"]
            for job in self.jobs.values():
                job["task"] = validate_task(job["task"])
                self.requests[job["request_id"]] = job["id"]
                if job["state"] in ("running", "cancelling"):
                    # A process restart is never permission to replay a task.
                    job.update(state="interrupted", finished_at=time.time())
                    self.recovery_required = True
            # Older controllers blocked on HTTP 402. Restore only failures that
            # happened before any public action, so the queue can wait for credit.
            for item in self.queue:
                if item["state"] == "blocked" and item.get("block_reason") == "VisionRequestError":
                    job = self.jobs.get(item.get("attempt_job_id"))
                    if job is not None and self._vision_http_status(job) == 402:
                        self._settle_queue_item(item, job)
            self._save()
        self.queue_worker = asyncio.create_task(self._queue_loop())

    def _save(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"jobs": self.jobs, "queue": self.queue,
                       "recovery_required": self.recovery_required}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.state_path)

    @property
    def busy(self) -> bool:
        return self.capturing or (self.active is not None and not self.active.done())

    def _portal(self) -> Any:
        portal = self.host.current
        if portal is None or portal.reader.done():
            raise ControlError("offline", "Paired phone is offline; no task was started.")
        return portal

    @staticmethod
    def _view(job: dict[str, Any]) -> dict[str, Any]:
        # Supplied form text stays in private task memory, not status output.
        value = copy.deepcopy({k: v for k, v in job.items() if k != "task"})
        value["goal"] = job["task"]["goal"]
        value["allow_robot_verification"] = job["task"].get("allow_robot_verification", False)
        return value

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        if job_id is not None:
            if job_id not in self.jobs:
                raise ControlError("no_such_job", "Unknown task ID.")
            return {"job": self._view(self.jobs[job_id])}
        portal = self.host.current
        return {
            "connected": portal is not None and not portal.reader.done(),
            "device_id": self.host.device_id,
            "busy": self.busy,
            "active_job": self.active_id if self.busy else None,
            "recovery_required": self.recovery_required or bool(portal and portal.uncertain),
            "park_on_finish": self.park_on_finish,
            "queue": {state: sum(item["state"] == state for item in self.queue)
                      for state in ("queued", "running", "finished", "blocked")},
            "queue_worker_error": self.queue_worker_error,
            "recent_jobs": [self._view(j) for j in list(self.jobs.values())[-10:]],
        }

    @staticmethod
    def _queue_view(item: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy({k: v for k, v in item.items() if k != "task"})
        value["goal"] = item["task"]["goal"]
        return value

    def queue_status(self, item_id: str | None = None) -> dict[str, Any]:
        if item_id:
            item = next((item for item in self.queue if item["id"] == item_id), None)
            if item is None:
                raise ControlError("no_such_queue_item", "Unknown queue item ID.")
            return {"queue_item": self._queue_view(item)}
        return {"queue": [self._queue_view(item) for item in self.queue]}

    def expedite_queue(self, item_id: str) -> dict[str, Any]:
        item = next((item for item in self.queue if item["id"] == item_id), None)
        if item is None:
            raise ControlError("no_such_queue_item", "Unknown queue item ID.")
        if item["state"] != "queued":
            raise ControlError("not_queued", "Only a waiting queue item can be expedited.")
        previous_id = item.get("last_job_id")
        if previous_id:
            previous = self.jobs.get(previous_id)
            public = (previous or {}).get("result", {}).get("public_action_attempts", {})
            if previous is None or any(public.get(action, 0) for action in
                                       ("publish", "delete", "like", "comment")):
                raise ControlError("unsafe_retry", "Prior public action needs inspection.")
        item["next_attempt_at"] = 0.0
        self._save()
        return {"queue_item": self._queue_view(item)}

    async def reconcile_queue_profile(self, item_id: str, expected_headline: str) -> dict[str, Any]:
        """Resolve an uncertain Post tap from a fresh, matching profile screenshot."""
        if self.busy:
            raise ControlError("busy", "Wait for the active phone task to stop.")
        item = next((item for item in self.queue if item["id"] == item_id), None)
        if item is None:
            raise ControlError("no_such_queue_item", "Unknown queue item ID.")
        if item["state"] != "blocked" or not isinstance(expected_headline, str) or not expected_headline.strip():
            raise ControlError("invalid_reconciliation", "Supply a blocked item and its first-slide headline.")
        job = self.jobs.get(item.get("attempt_job_id"))
        result = (job or {}).get("result") or {}
        if (job is None or not item["task"].get("publication")
                or result.get("public_action_attempts", {}).get("publish") != 1
                or result.get("status") in ("uncertain_input", "interrupted")):
            raise ControlError("invalid_reconciliation", "The item has no acknowledged publication attempt.")
        if self.recovery_required:
            raise ControlError("recovery_required", "Inspect uncertain phone input first.")
        portal = self._portal()
        if portal.uncertain:
            raise ControlError("recovery_required", "Inspect uncertain phone input first.")
        checker = getattr(self.model, "verify_profile_publication", None)
        if not callable(checker):
            raise ControlError("unsupported", "Profile publication verification is unavailable.")
        self.capturing = True
        try:
            directory = self.runs_dir / f"queue-reconcile-{uuid.uuid4().hex}"
            journal = Journal(directory)
            frame = await screenshot(portal)
            journal.frame(0, frame)
            assessment, metadata = await checker(item["task"], frame, expected_headline)
            if assessment.get("valid") is not True:
                raise ControlError("unconfirmed_publication", "The profile does not prove this post was published.")
            account = item["task"]["authorization"]["account"]
            reconciliation = {"account": account, "headline": expected_headline,
                              "evidence": assessment["evidence"],
                              "visible_text": assessment["visible_text"],
                              "screenshot": str(directory / "000.png"),
                              "time": time.time(), "model": metadata}
            (directory / "reconciliation.json").write_text(
                json.dumps(reconciliation, ensure_ascii=False, indent=2))
            job["publication_reconciliation"] = reconciliation
            result["publication_account"] = account
            item["state"] = "finished"
            item.pop("block_reason", None)
            self._save()
            return {"queue_item": self._queue_view(item), "job": self._view(job)}
        finally:
            self.capturing = False

    def enqueue(self, task: Any, *, execute: Any, request_id: Any) -> dict[str, Any]:
        try:
            task = copy.deepcopy(validate_task(task))
            if type(execute) is not bool or not isinstance(request_id, str):
                raise ValueError("execute must be boolean and request_id must be a UUID")
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ControlError("invalid_request", str(error)) from error
        for item in self.queue:
            if item["request_id"] == request_id:
                if item["task"] != task or item["execute"] != execute:
                    raise ControlError("request_conflict", "This request ID belongs to a different instruction.")
                return {"queue_item": self._queue_view(item), "duplicate": True}
        if self.closed:
            raise ControlError("stopping", "Controller is stopping; no task was queued.")
        item = {"id": uuid.uuid4().hex, "request_id": request_id,
                "task": task, "execute": execute, "state": "queued",
                "created_at": time.time(), "attempts": 0, "retry_count": 0,
                "next_attempt_at": 0.0, "attempt_request_id": None,
                "attempt_job_id": None, "last_job_id": None}
        self.queue.append(item)
        self._save()
        return {"queue_item": self._queue_view(item), "duplicate": False}

    def cancel_queue(self, item_id: str) -> dict[str, Any]:
        item = next((item for item in self.queue if item["id"] == item_id), None)
        if item is None:
            raise ControlError("no_such_queue_item", "Unknown queue item ID.")
        if item["state"] in ("queued", "running"):
            item["state"] = "cancelled"
            if item.get("attempt_job_id"):
                self.cancel(item["attempt_job_id"])
            self._save()
        return {"queue_item": self._queue_view(item)}

    @staticmethod
    def _vision_http_status(job: dict[str, Any]) -> int | None:
        trace = Path(job["run_dir"]) / "trace.jsonl"
        try:
            errors = [json.loads(line) for line in trace.read_text().splitlines()
                      if '"event": "error"' in line]
            return errors[-1].get("http_status") if errors else None
        except (OSError, ValueError):
            return None

    @classmethod
    def _transient_failure(cls, job: dict[str, Any]) -> bool:
        error = job.get("error_type")
        if error in {"VisionTransportError", "ReadTimeout", "ConnectTimeout", "ConnectError",
                     "RemoteProtocolError", "PoolTimeout", "ConnectionClosed", "OSError"}:
            return True
        if error == "VisionRequestError":
            return cls._vision_http_status(job) in (402, 429, 500, 502, 503, 504)
        return False

    def _settle_queue_item(self, item: dict[str, Any], job: dict[str, Any]) -> None:
        result = job.get("result") or {}
        public = result.get("public_action_attempts") or {}
        publication = item["task"].get("publication")
        expected_account = item["task"].get("authorization", {}).get("account")
        done = (job["state"] == "finished"
                and result.get("status") in (("completion_candidate",) if publication
                                             else ("completion_candidate", "duration_limit"))
                and (not publication or (public.get("publish") == 1
                     and result.get("publication_account") == expected_account)))
        if done:
            item["state"] = "finished"
        elif (self._transient_failure(job) and result
              and all(public.get(action) == 0 for action in ("publish", "delete", "like", "comment"))
              and result.get("status") not in ("uncertain_input", "interrupted")
              and not self.recovery_required):
            item["state"] = "queued"
            item["retry_count"] += 1
            delay = min(1800, self.retry_base_seconds * 2 ** min(item["retry_count"] - 1, 7))
            if job.get("error_type") == "VisionRequestError" and self._vision_http_status(job) == 402:
                delay = 1800  # No balance: avoid hammering the model endpoint.
            item["next_attempt_at"] = time.time() + delay
            item["last_job_id"] = job["id"]
            item["attempt_job_id"] = None
            item["attempt_request_id"] = None
        else:
            item["state"] = "blocked"
            item["block_reason"] = (job.get("error_type") or result.get("status")
                                    or "publication_needs_inspection")
        self._save()

    async def _queue_loop(self) -> None:
        while not self.closed:
            try:
                await asyncio.sleep(1)
                item = next((item for item in self.queue
                             if item["state"] in ("queued", "running", "blocked")), None)
                if item is None or item["state"] == "blocked":
                    continue  # Preserve FIFO when an earlier post needs inspection.
                if item["state"] == "running":
                    job_id = item.get("attempt_job_id") or self.requests.get(item.get("attempt_request_id"))
                    if not job_id:
                        item["state"] = "queued"  # Crash before the durable submit.
                        self._save()
                        continue
                    item["attempt_job_id"] = job_id
                    job = self.jobs[job_id]
                    if job["state"] not in ("running", "cancelling"):
                        self._settle_queue_item(item, job)
                    continue
                if (time.time() < item["next_attempt_at"] or self.busy or self.recovery_required
                        or self.host.current is None or self.host.current.reader.done()
                        or self.host.current.uncertain):
                    continue
                if not item.get("attempt_request_id"):
                    item["attempt_request_id"] = str(uuid.uuid4())
                    self._save()  # Reuse this ID if the controller restarts mid-submit.
                task = copy.deepcopy(item["task"])
                if task.get("publication") and item.get("last_job_id"):
                    task["resume_from_job_id"] = item["last_job_id"]
                try:
                    receipt = self.submit(task, execute=item["execute"],
                                          request_id=item["attempt_request_id"])
                except ControlError as error:
                    if error.code not in ("busy", "offline", "recovery_required", "stopping"):
                        item["state"] = "blocked"
                        item["block_reason"] = error.code
                        self._save()
                    continue
                item["state"] = "running"
                item["attempt_job_id"] = receipt["job"]["id"]
                item["attempts"] += 1
                self._save()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A queue bookkeeping error must not silently lose any item.
                # The durable receipt remains available for inspection.
                self.queue_worker_error = type(error).__name__
                await asyncio.sleep(5)

    def submit(self, task: Any, *, execute: Any, request_id: Any) -> dict[str, Any]:
        try:
            task = copy.deepcopy(validate_task(task))
            if type(execute) is not bool:
                raise ValueError("execute must be a boolean")
            if not isinstance(request_id, str):
                raise ValueError("request_id must be a UUID")
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ControlError("invalid_request", str(error)) from error
        if request_id in self.requests:
            existing = self.jobs[self.requests[request_id]]
            if existing["task"] != task or existing["execute"] != execute:
                raise ControlError("request_conflict", "This request ID belongs to a different instruction.")
            return {"job": self._view(existing), "duplicate": True}
        deletion = task.get("deletion")
        if deletion and execute:
            identity = (deletion["target_id"], task["authorization"]["app"], task["authorization"]["account"])
            for existing in self.jobs.values():
                prior = existing["task"]
                if (existing["execute"] and prior.get("deletion")
                        and (prior["deletion"]["target_id"], prior["authorization"]["app"],
                             prior["authorization"]["account"]) == identity
                        and (existing.get("result", {}).get("public_action_attempts", {}).get("delete") != 0
                             or existing.get("result", {}).get("status") in ("uncertain_input", "interrupted"))):
                    raise ControlError("deletion_exists", "This item already has a deletion attempt. Inspect its receipt; do not repeat it blindly.")
        replacement = task.get("replacement")
        if replacement and execute:
            prior = self.jobs.get(replacement["previous_job_id"], {})
            removal = self.jobs.get(replacement["deletion_job_id"], {})
            removal_result = removal.get("result", {})
            if (not prior.get("execute") or not removal.get("execute")
                    or not prior.get("task", {}).get("publication")
                    or prior.get("result", {}).get("public_action_attempts", {}).get("publish") != 1
                    or prior.get("result", {}).get("status") in ("uncertain_input", "interrupted")
                    or prior.get("task", {}).get("authorization", {}).get("account") != task["authorization"]["account"]
                    or prior.get("task", {}).get("authorization", {}).get("app") != task["authorization"]["app"]
                    or removal.get("task", {}).get("authorization", {}).get("account") != task["authorization"]["account"]
                    or removal.get("task", {}).get("authorization", {}).get("app") != task["authorization"]["app"]
                    or removal.get("state") != "finished" or removal_result.get("status") != "completion_candidate"
                    or removal_result.get("public_action_attempts", {}).get("delete") != 1
                    or removal_result.get("deletion_target_id") != replacement["previous_job_id"]):
                raise ControlError("replacement_not_ready", "Corrective publication needs the matching completed deletion receipt.")
        publication = task.get("publication")
        if publication and task["authorization"]["limits"].get("publish", 0):
            identity = (publication["source_id"], task["authorization"]["app"],
                        task["authorization"]["account"])
            prior_jobs = []
            for existing in self.jobs.values():
                prior = existing["task"]
                if (existing["execute"] and prior.get("publication")
                        and prior.get("authorization", {}).get("limits", {}).get("publish", 0)
                        and (prior["publication"]["source_id"], prior["authorization"]["app"],
                             existing.get("publication_reconciliation", {}).get("account")
                             or prior["authorization"]["account"]) == identity):
                    prior_jobs.append(existing)
            if prior_jobs:
                # An explicit continuation is allowed only when every previous
                # attempt is terminal and known to have sent zero Post taps.
                # Missing receipts, interrupted jobs or any Post attempt remain
                # blocked, including after process restart.
                resumable = (task.get("resume_from_job_id") == prior_jobs[-1]["id"]
                    and all(j["state"] in ("finished", "failed", "cancelled")
                            and j.get("result", {}).get("public_action_attempts", {}).get("publish") == 0
                            and j.get("result", {}).get("status") not in ("uncertain_input", "interrupted")
                            for j in prior_jobs))
                if not resumable:
                    raise ControlError("publication_exists", "This source already has a publication job on this account. Inspect its receipt; do not retry it blindly.")
        if self.closed:
            raise ControlError("stopping", "Controller is stopping; no task was started.")
        if self.busy:
            raise ControlError("busy", "Another task owns the phone. Wait for it or cancel its task ID.")
        portal = self._portal()
        if self.recovery_required or portal.uncertain:
            raise ControlError("recovery_required", "Capture and inspect the phone, then acknowledge that frame before a new task.")
        job_id = uuid.uuid4().hex
        directory = self.runs_dir / f"task-{job_id}"
        job = {
            "id": job_id, "request_id": request_id, "task": task,
            "execute": execute, "state": "running", "created_at": time.time(),
            "run_dir": str(directory),
        }
        self.jobs[job_id] = job
        self.requests[request_id] = job_id
        # Receipt is durable before any model request or phone input.
        self._save()
        self.active_id = job_id
        self.active = asyncio.create_task(self._run(job, portal))
        return {"job": self._view(job), "duplicate": False}

    async def _run(self, job: dict[str, Any], portal: Any) -> None:
        directory = Path(job["run_dir"])
        try:
            journal = Journal(directory)
            job["started_at"] = time.time()
            self._save()
            if self.keep_awake:
                await ensure_screen_awake(portal, journal)
            job["result"] = await run_agent(portal, self.model, job["task"], journal,
                                            execute=job["execute"])
            if (self.park_on_finish and job["execute"]
                    and job["result"]["status"] in ("completion_candidate", "duration_limit")):
                # Keep ownership until cleanup finishes, including between
                # batch posts. Preserve the task's publication receipt even if
                # the separate display cleanup fails; never replay a Post tap.
                try:
                    job["cleanup"] = {"status": "completed", **await park_phone(portal, journal)}
                except Exception as error:
                    job["cleanup"] = {"status": "failed", "error_type": type(error).__name__}
                    journal.record("screen_park_failed", error_type=type(error).__name__)
            job["state"] = "finished"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            self._existing_report(job)
        except Exception as error:
            job.update(state="failed", error_type=type(error).__name__)
            self._existing_report(job)
        finally:
            if portal.uncertain or job.get("result", {}).get("status") == "uncertain_input":
                self.recovery_required = True
                self.recovery_capture = None
            job["finished_at"] = time.time()
            self._save()
            print(json.dumps({"event": "task_finished", **self._view(job)}), flush=True)

    @staticmethod
    def _existing_report(job: dict[str, Any]) -> None:
        report = Path(job["run_dir"]) / "report.json"
        if report.is_file():
            try:
                job["result"] = json.loads(report.read_text())["result"]
                job["result"]["reports"] = {
                    "json": str(report), "markdown": str(report.with_suffix(".md")),
                }
            except (ValueError, KeyError):
                pass

    def cancel(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ControlError("no_such_job", "Unknown task ID.")
        job = self.jobs[job_id]
        if self.active_id == job_id and self.active is not None and not self.active.done():
            # Make cancellation idempotent; repeated requests must not interrupt
            # the first cancellation's journal/report cleanup.
            if job["state"] != "cancelling":
                job["state"] = "cancelling"
                self._save()
                self.active.cancel()
                if "started_at" not in job:
                    job.update(state="cancelled", finished_at=time.time())
                    self._save()
        return {"job": self._view(job)}

    async def capture(self, *, show_overlay: bool = False) -> dict[str, Any]:
        if type(show_overlay) is not bool:
            raise ControlError("invalid_request", "show_overlay must be a boolean")
        if self.busy:
            raise ControlError("busy", "Wait for the active task to stop before capturing the phone.")
        portal = self._portal()
        self.capturing = True
        try:
            directory = self.runs_dir / f"capture-{uuid.uuid4().hex}"
            journal = Journal(directory)
            # A read is allowed after uncertain input; enabling the watchdog is
            # a mutation and must wait until the operator has inspected it.
            if self.keep_awake and not (self.recovery_required or portal.uncertain):
                await ensure_screen_awake(portal, journal)
            frame = await screenshot(portal, hide_overlay=False if show_overlay else None)
            status = await portal.call("screen/keepAwake/status") if self.keep_awake else None
            journal.record("capture_diagnostics", show_overlay=show_overlay, screen_status=status)
            journal.frame(0, frame)
            self.recovery_capture = (frame.id, time.monotonic(), portal)
            return {"frame_id": frame.id, "path": str(directory / "000.png"),
                    "width": frame.width, "height": frame.height, "screen_status": status,
                    "portal_screenshot_ms": round(frame.portal_screenshot_ms, 1),
                    "host_prepare_ms": round(frame.host_prepare_ms, 1)}
        finally:
            self.capturing = False

    async def park(self) -> dict[str, Any]:
        if self.busy:
            raise ControlError("busy", "Wait for the active task to stop before locking the phone.")
        portal = self._portal()
        if self.recovery_required or portal.uncertain:
            raise ControlError("recovery_required", "Inspect the uncertain phone input before locking it.")
        self.capturing = True
        try:
            journal = Journal(self.runs_dir / f"park-{uuid.uuid4().hex}")
            return await park_phone(portal, journal)
        finally:
            self.capturing = False
            if portal.uncertain:
                self.recovery_required = True
                self.recovery_capture = None
                self._save()

    def acknowledge(self, frame_id: str) -> dict[str, Any]:
        if self.busy:
            raise ControlError("busy", "Wait for the active task to stop before acknowledging a frame.")
        portal = self._portal()
        captured = self.recovery_capture
        if (captured is None or captured[0] != frame_id or captured[2] is not portal
                or time.monotonic() - captured[1] > 90):
            raise ControlError("stale_frame", "Capture and inspect a fresh frame from the current phone connection.")
        # This explicit operator acknowledgement does not resume the old job.
        portal.uncertain = False
        self.recovery_required = False
        self.recovery_capture = None
        self._save()
        return {"recovery_required": False, "resumed_task": False}

    async def reconcile_publication(self, job_id: str) -> dict[str, Any]:
        """Resolve a named destination from saved post-success pixels, with no phone input.

        Never reset a Post allowance. A proven different recipient is retained
        as the actual duplicate-protection identity, alongside the original task.
        """
        if self.busy:
            raise ControlError("busy", "Wait for the active task before reconciling its publication.")
        job = self.jobs.get(job_id)
        if job is None:
            raise ControlError("no_such_job", "Unknown task ID.")
        result = job.get("result", {})
        if (job["state"] not in ("finished", "failed")
                or result.get("public_action_attempts", {}).get("publish") != 1
                or result.get("status") in ("uncertain_input", "interrupted")):
            raise ControlError("unconfirmed_publication", "Reconciliation needs a terminal, acknowledged publication.")
        if job.get("publication_reconciliation"):
            return {"job": self._view(job)}
        checker = getattr(self.model, "publication_destination", None)
        if not callable(checker):
            raise ControlError("unsupported", "No visual publication-destination checker is configured.")
        directory = Path(job["run_dir"])
        events = [json.loads(line) for line in (directory / "trace.jsonl").read_text().splitlines()]
        acknowledged = {row.get("step") for row in events if row.get("event") == "input_acknowledged"}
        posts = [row for row in events if row.get("event") == "input_intent"
                 and row.get("step") in acknowledged
                 and isinstance(row.get("action"), dict) and row["action"].get("effect") == "publish"]
        observations = [row for row in events if row.get("event") == "observation"
                        and posts and row.get("step", -1) > posts[-1]["step"]]
        if not observations:
            raise ControlError("unconfirmed_destination", "No saved post-publication screenshot is available.")
        frame_path = directory / f"{observations[-1]['step']:03}.png"
        frame = Frame.from_portal(base64.b64encode(frame_path.read_bytes()).decode())
        self.capturing = True
        try:
            assessment, metadata = await checker(job["task"], frame)
            account = assessment.get("account", "")
            from .workflows import account_name
            if assessment.get("posted") is not True or not account or account_name(account) != account:
                raise ControlError("unconfirmed_destination", "Saved pixels do not name a successful publication destination.")
            reconciliation = {"account": account, "evidence": assessment["evidence"],
                              "screenshot": str(frame_path), "time": time.time(), "model": metadata}
            audit = directory / "publication-reconciliation.json"
            audit.write_text(json.dumps(reconciliation, ensure_ascii=False, indent=2))
            job["publication_reconciliation"] = reconciliation
            result["publication_account"] = account
            if account.casefold() != job["task"]["authorization"]["account"].casefold():
                result["status"] = "misdirected_publication"
            self._save()
            return {"job": self._view(job)}
        finally:
            self.capturing = False

    async def dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ControlError("invalid_request", "A request must be a JSON object.")
        operation = request.get("operation")
        fields = {
            "submit": {"operation", "task", "execute", "request_id"},
            "enqueue": {"operation", "task", "execute", "request_id"},
            "queue_status": {"operation", "item_id"},
            "queue_cancel": {"operation", "item_id"},
            "queue_expedite": {"operation", "item_id"},
            "queue_reconcile_profile": {"operation", "item_id", "expected_headline"},
            "status": {"operation", "job_id"},
            "cancel": {"operation", "job_id"},
            "capture": {"operation", "show_overlay"},
            "acknowledge": {"operation", "frame_id"},
            "park": {"operation"},
            "reconcile_publication": {"operation", "job_id"},
        }
        if not isinstance(operation, str) or operation not in fields or set(request) - fields[operation]:
            raise ControlError("invalid_request", "Unknown operation or request fields.")
        if operation == "submit":
            return self.submit(request.get("task"), execute=request.get("execute", False),
                               request_id=request.get("request_id"))
        if operation == "enqueue":
            return self.enqueue(request.get("task"), execute=request.get("execute", False),
                                request_id=request.get("request_id"))
        if operation == "queue_reconcile_profile":
            return await self.reconcile_queue_profile(request.get("item_id"),
                                                      request.get("expected_headline"))
        if operation in ("queue_status", "queue_cancel", "queue_expedite"):
            item_id = request.get("item_id")
            if item_id is not None and not isinstance(item_id, str):
                raise ControlError("invalid_request", "Queue item ID must be a string.")
            if operation == "queue_status":
                return self.queue_status(item_id)
            if operation == "queue_expedite":
                if not item_id:
                    raise ControlError("invalid_request", "Supply the queue item ID to expedite.")
                return self.expedite_queue(item_id)
            if not item_id:
                raise ControlError("invalid_request", "Supply the queue item ID to cancel.")
            return self.cancel_queue(item_id)
        if operation == "capture":
            return await self.capture(show_overlay=request.get("show_overlay", False))
        if operation == "park":
            return await self.park()
        value = request.get("frame_id" if operation == "acknowledge" else "job_id")
        if value is not None and not isinstance(value, str):
            raise ControlError("invalid_request", "Task and frame IDs must be strings.")
        if operation == "status":
            return self.status(value)
        if not value:
            raise ControlError("invalid_request", "This operation needs a task or frame ID.")
        if operation == "reconcile_publication":
            return await self.reconcile_publication(value)
        return self.cancel(value) if operation == "cancel" else self.acknowledge(value)

    async def close(self) -> None:
        self.closed = True
        self.queue_worker.cancel()
        await asyncio.gather(self.queue_worker, return_exceptions=True)
        if self.active is not None and not self.active.done():
            if self.active_id is not None:
                self.cancel(self.active_id)
            await asyncio.gather(self.active, return_exceptions=True)
        self.owner_lock.close()


@asynccontextmanager
async def control_server(controller: TaskController, socket_path: Path):
    """Owner-only Unix socket: task control is never exposed on the LAN."""
    socket_path = socket_path.resolve()
    if socket_path.exists():
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ControlError("socket_exists", "Control path is not our owned socket; leave it unchanged.")
        try:
            _, writer = await asyncio.open_unix_connection(str(socket_path))
        except ConnectionRefusedError:
            socket_path.unlink()
        else:
            writer.close()
            await writer.wait_closed()
            raise ControlError("socket_in_use", "Another controller is already listening at this control socket.")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                raise ControlError("invalid_request", "Request must be one bounded JSON line.")
            result = await controller.dispatch(json.loads(raw))
            reply = {"ok": True, **result}
        except ControlError as error:
            reply = {"ok": False, "error": {"code": error.code, "message": str(error)}}
        except (ValueError, UnicodeError, TimeoutError):
            reply = {"ok": False, "error": {"code": "invalid_request", "message": "Invalid or incomplete request."}}
        except Exception as error:
            reply = {"ok": False, "error": {"code": "control_failed", "message": type(error).__name__}}
        try:
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        except ConnectionError:
            # Losing a client reply does not undo a saved receipt or stop the
            # task. The same request ID retrieves it without resubmission.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path), limit=MAX_REQUEST)
    os.chmod(socket_path, 0o600)
    owned_inode = socket_path.stat().st_ino
    try:
        async with server:
            yield server
    finally:
        if socket_path.exists() and socket_path.stat().st_ino == owned_inode:
            socket_path.unlink()


async def control_request(socket_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload).encode() + b"\n"
    if len(raw) > MAX_REQUEST:
        raise ControlError("invalid_request", "Control request is too large.")
    reader, writer = await asyncio.open_unix_connection(str(socket_path.resolve()), limit=2 * MAX_REQUEST)
    try:
        writer.write(raw)
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 45))
    finally:
        writer.close()
        await writer.wait_closed()
    if result.get("ok") is not True:
        error = result.get("error", {})
        raise ControlError(error.get("code", "control_failed"), error.get("message", "Controller request failed."))
    return result

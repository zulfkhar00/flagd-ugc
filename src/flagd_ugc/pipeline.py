from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .assets import (
    Demo,
    load_character_references,
    load_demos,
)
from .config import Settings
from .db import Database
from .errors import (
    AssetError,
    ConfigurationError,
    DeliveryUncertain,
    HookReviewError,
    ProviderValidationError,
    ProxyBlockError,
    QualityError,
    TransientError,
)
from .hook_formats import assign_hook_formats, visible_hook_text
from .hooks import validate_hook, validate_title
from .media import (
    MediaComposer,
    extract_review_frames,
    validate_output,
    validate_vertical_asset,
)
from .fal_provider import FalJob, FalKlingProvider
from .providers import GeminiProvider
from .retry import retry_transient
from .telegram import TelegramClient
from .opening_director import (
    OpeningDirection,
    direction_for_hook_format,
    get_opening_direction,
)

LOGGER = logging.getLogger(__name__)
FAL_PROXY_COOLDOWN_SECONDS = 180.0


@dataclass(frozen=True)
class BatchResult:
    batch_id: int
    run_date: date
    status: str
    completed: int
    expected: int
    locked: bool = False


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        gemini: Any | None = None,
        kling: Any | None = None,
        telegram: Any | None = None,
        composer: Any | None = None,
    ):
        self.settings = settings
        settings.create_directories()
        self.database = database or Database(settings.db_path)
        self.database.initialize()
        rebased = self.database.rebase_missing_data_paths(settings.data_dir)
        if any(rebased.values()):
            LOGGER.info(
                "rebased persisted data paths for this host: %s demos, %s stages",
                rebased["demos"],
                rebased["stages"],
            )
        self.gemini = gemini or GeminiProvider(
            settings.gemini_api_key,
            text_model=settings.gemini_text_model,
            image_model=settings.gemini_image_model,
            timeout=settings.request_timeout_seconds,
        )
        self.kling = kling or FalKlingProvider(
            settings.fal_api_key,
            model=settings.fal_kling_model,
            request_timeout=settings.request_timeout_seconds,
            poll_seconds=settings.fal_poll_seconds,
            initial_poll_delay=settings.fal_initial_poll_delay_seconds,
            job_timeout=settings.kling_timeout_seconds,
            transient_retries=settings.transient_retries,
        )
        self.telegram = telegram
        self.composer = composer or MediaComposer(settings.font_file)

    def _telegram_client(self) -> Any:
        if self.telegram is None:
            self.telegram = TelegramClient.for_creator(
                self.settings,
                "creator-01",
                self.database,
            )
        return self.telegram

    def _require_secrets(self, deliver: bool) -> None:
        missing = self.settings.missing_secrets(include_telegram=deliver)
        if missing:
            raise ConfigurationError("missing required secrets: " + ", ".join(missing))

    def _check_fal_queue_access(self) -> None:
        """Fail before Gemini image generation if the paid fal queue is unreachable.

        The real adapter probes a fabricated request URL only; injected offline test doubles
        do not need to implement the check.
        """
        check = getattr(self.kling, "check_queue_access", None)
        if callable(check):
            retry_transient(check, retries=self.settings.transient_retries)

    @staticmethod
    def _stage_file(stage: Any | None) -> Path | None:
        if stage is None or stage["status"] != "complete" or not stage["output_path"]:
            return None
        path = Path(stage["output_path"])
        return path if path.is_file() else None

    def _send_alert(self, batch_id: int, key: str, message: str) -> None:
        try:
            telegram = self._telegram_client()
            retry_transient(
                lambda: telegram.send_text(batch_id, key, message, kind="alert"),
                retries=self.settings.transient_retries,
            )
        except Exception:
            LOGGER.exception("could not send Telegram alert for batch %s", batch_id)

    def _ensure_hooks(
        self, batch_id: int, count: int, demos: list[Demo], run_date: date
    ) -> list[Any]:
        existing = self.database.list_videos(batch_id)
        if existing:
            if len(existing) != count:
                raise QualityError(
                    f"batch has {len(existing)} persisted hooks but expects {count}"
                )
            stage = self.database.get_batch_stage(batch_id, "hooks")
            if stage is None or stage["status"] != "complete":
                self.database.complete_batch_stage(
                    batch_id,
                    "hooks",
                    metadata={
                        "hooks": [
                            {
                                "hook": row["hook"],
                                "hook_format": row["hook_format"],
                                "title": row["title"],
                                "opening_direction": row["opening_direction"],
                            }
                            for row in existing
                        ]
                    },
                )
            return existing

        assignments = assign_hook_formats(demos, count, run_date)
        chosen = [demo for demo, _ in assignments]
        formats = [format_definition for _, format_definition in assignments]
        max_quality_attempts = self.settings.quality_retries + 1
        previous_stage = self.database.get_batch_stage(batch_id, "hooks")
        last_error: Exception | None = (
            QualityError(previous_stage["error"])
            if previous_stage is not None
            and previous_stage["status"] == "failed"
            and previous_stage["error"]
            else None
        )
        repair_metadata: dict[str, Any] = {}
        if previous_stage is not None and previous_stage["metadata_json"]:
            saved = json.loads(previous_stage["metadata_json"])
            if isinstance(saved, dict) and "draft_hooks" in saved:
                repair_metadata = saved
        for _ in range(max_quality_attempts):
            self.database.start_batch_stage(batch_id, "hooks")
            try:
                generation_options: dict[str, Any] = {"formats": formats}
                if last_error is not None:
                    generation_options["review_feedback"] = str(last_error)
                if repair_metadata:
                    generation_options["previous_hooks"] = repair_metadata["draft_hooks"]
                    generation_options["rejected_demo_files"] = repair_metadata[
                        "rejected_demo_files"
                    ]
                items = retry_transient(
                    lambda: self.gemini.generate_hooks(chosen, **generation_options),
                    retries=self.settings.transient_retries,
                )
                items = self._validate_assigned_hooks(
                    items, assignments, run_date=run_date
                )
                rows = self.database.create_videos(batch_id, items)
                self.database.complete_batch_stage(
                    batch_id,
                    "hooks",
                    metadata={
                        "hooks": [
                            {
                                "hook": item["hook"],
                                "hook_format": item["hook_format"],
                                "title": item["title"],
                                "opening_direction": item["opening_direction"],
                            }
                            for item in items
                        ]
                    },
                )
                return rows
            except QualityError as exc:
                last_error = exc
                if isinstance(exc, HookReviewError):
                    repair_metadata = {
                        "draft_hooks": exc.proposal,
                        "rejected_demo_files": exc.rejected_demo_files,
                    }
                self.database.fail_batch_stage(
                    batch_id, "hooks", str(exc), metadata=repair_metadata or None
                )
                continue
            except Exception as exc:
                self.database.fail_batch_stage(batch_id, "hooks", str(exc))
                raise
        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_assigned_hooks(
        items: list[dict[str, str]],
        assignments: list[tuple[Demo, Any]],
        *,
        run_date: date | None = None,
    ) -> list[dict[str, str]]:
        """Defend the persisted boundary even when a provider implementation is replaced."""
        if len(items) != len(assignments):
            raise QualityError(
                f"expected {len(assignments)} generated hooks, got {len(items)}"
            )
        expected = {
            str(demo.path.resolve()): (demo, format_definition)
            for demo, format_definition in assignments
        }
        validated: list[dict[str, str]] = []
        seen: set[str] = set()
        seen_titles: set[str] = set()
        for item in items:
            try:
                demo_path = str(Path(item["demo_path"]).resolve())
                hook_format = str(item["hook_format"])
                raw_hook = str(item["hook"])
                raw_title = str(item["title"])
                reaction_key = str(item["reaction_key"])
            except (KeyError, TypeError) as exc:
                raise QualityError("generated hook is missing required fields") from exc
            if demo_path in seen or demo_path not in expected:
                raise QualityError("generated hook changed or repeated its assigned demo")
            seen.add(demo_path)
            demo, format_definition = expected[demo_path]
            if hook_format != format_definition.key:
                raise QualityError(
                    f"generated hook changed assigned format for {demo.path.name}: "
                    f"expected {format_definition.key!r}, got {hook_format!r}"
                )
            hook = validate_hook(
                raw_hook,
                hook_format=hook_format,
                demo_description=demo.description,
            )
            title = validate_title(raw_title, demo_description=demo.description)
            if title.casefold() in {
                hook.casefold(),
                visible_hook_text(hook, hook_format).casefold(),
            }:
                raise QualityError("upload title must be separate from the on-video hook")
            title_key = title.casefold()
            if title_key in seen_titles:
                raise QualityError("generated batch repeated an upload title")
            seen_titles.add(title_key)
            direction = direction_for_hook_format(
                hook_format,
                rotation=run_date.toordinal() if run_date else 0,
                reaction_key=reaction_key,
            )
            validated.append(
                {
                    "demo_path": demo_path,
                    "hook_format": hook_format,
                    "hook": hook,
                    "title": title,
                    "opening_direction": direction.key,
                }
            )
        return validated

    def _opening_direction_for_video(self, video: Any) -> OpeningDirection:
        """Resolve the persisted recipe, hydrating only pre-direction legacy rows."""
        persisted = str(video["opening_direction"] or "").strip()
        if persisted:
            direction = get_opening_direction(persisted)
            if direction.hook_format != str(video["hook_format"]):
                raise QualityError(
                    "persisted opening direction does not match the video's hook format"
                )
            return direction
        direction = direction_for_hook_format(str(video["hook_format"]))
        self.database.set_video_opening_direction(int(video["id"]), direction.key)
        return direction

    def _ensure_scene(
        self,
        video: Any,
        references: list[Path],
        description: str,
        direction: OpeningDirection,
        intermediate: Path,
    ) -> Path:
        video_id = int(video["id"])
        target = intermediate / "scene.png"
        review = self.database.get_video_stage(video_id, "scene_review")
        existing = self._stage_file(self.database.get_video_stage(video_id, "scene"))
        if review is not None and review["status"] == "complete" and existing:
            return existing

        while True:
            scene_stage = self.database.get_video_stage(video_id, "scene")
            review = self.database.get_video_stage(video_id, "scene_review")
            existing = self._stage_file(scene_stage)
            can_review_existing = existing is not None and (
                review is None or review["status"] in {"pending", "running"}
            )
            if not can_review_existing:
                attempts = int(scene_stage["attempts"]) if scene_stage else 0
                if attempts >= self.settings.quality_retries + 1:
                    rejected = (
                        scene_stage
                        if scene_stage is not None and scene_stage["status"] == "failed"
                        else review
                    )
                    reason = rejected["error"] if rejected is not None else None
                    message = f"scene exhausted its quality retries ({attempts} attempts)"
                    if reason:
                        message += f". Last rejection: {reason}"
                    raise QualityError(message)
                rejected = (
                    scene_stage
                    if scene_stage is not None and scene_stage["status"] == "failed"
                    else review
                )
                generation_options: dict[str, Any] = {"direction": direction.scene_prompt()}
                if rejected is not None and rejected["status"] == "failed" and rejected["error"]:
                    generation_options["review_feedback"] = str(rejected["error"])
                self.database.start_video_stage(video_id, "scene", clear_metadata=True)
                try:
                    retry_transient(
                        lambda: self.gemini.generate_scene(
                            str(video["hook"]),
                            description,
                            references,
                            target,
                            **generation_options,
                        ),
                        retries=self.settings.transient_retries,
                    )
                    validate_vertical_asset(target)
                except QualityError as exc:
                    self.database.fail_video_stage(video_id, "scene", str(exc))
                    continue
                self.database.complete_video_stage(
                    video_id, "scene", output_path=target
                )
                existing = target
            self.database.start_video_stage(video_id, "scene_review")
            reaction_review = (
                {"reaction_key": direction.reaction_key}
                if direction.reaction_scene_key
                else {}
            )
            try:
                result = retry_transient(
                    lambda: self.gemini.review_visual(
                        [existing], references, kind="opening scene", **reaction_review
                    ),
                    retries=self.settings.transient_retries,
                )
            except QualityError as exc:
                previous = (
                    json.loads(review["metadata_json"])
                    if review is not None and review["metadata_json"]
                    else {}
                )
                rejections = list(previous.get("rejections", []))
                scene = self.database.get_video_stage(video_id, "scene")
                attempt = int(scene["attempts"]) if scene else 0
                rejections.append({"scene_attempt": attempt, "reason": str(exc)})
                self.database.update_video_stage_metadata(
                    video_id, "scene_review", {"rejections": rejections}
                )
                LOGGER.warning(
                    "video %s scene attempt %s rejected: %s",
                    video["ordinal"],
                    attempt,
                    exc,
                )
                self.database.fail_video_stage(video_id, "scene_review", str(exc))
                continue
            if review is not None and review["metadata_json"]:
                rejections = json.loads(review["metadata_json"]).get("rejections")
                if rejections:
                    result = {**result, "rejections": rejections}
            self.database.complete_video_stage(
                video_id, "scene_review", output_path=existing, metadata=result
            )
            return existing

    def _animation_job(self, video_id: int) -> FalJob | None:
        stage = self.database.get_video_stage(video_id, "animation")
        if not stage or not stage["metadata_json"]:
            return None
        try:
            value = json.loads(stage["metadata_json"])["fal_job"]
            if not isinstance(value, dict):
                raise TypeError("fal_job is not an object")
            return FalJob.from_dict(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _submit_animation(
        self,
        scene: Path,
        direction: OpeningDirection,
    ) -> FalJob:
        """Submit once at a time, with a long cooldown for managed-proxy blocks."""
        sleeper = getattr(self.kling, "sleep", time.sleep)
        for attempt in range(self.settings.transient_retries + 1):
            try:
                return self.kling.submit(
                    scene,
                    direction.motion_prompt(),
                    duration=3,
                    allow_hand_entry=direction.allow_hand_entry,
                )
            except ProxyBlockError as exc:
                if attempt >= self.settings.transient_retries:
                    raise
                LOGGER.warning(
                    "fal submission blocked by HTTPS proxy; cooling down %.0f "
                    "seconds before retry %d/%d: %s",
                    FAL_PROXY_COOLDOWN_SECONDS,
                    attempt + 1,
                    self.settings.transient_retries,
                    exc,
                )
                sleeper(FAL_PROXY_COOLDOWN_SECONDS)
            except TransientError as exc:
                if attempt >= self.settings.transient_retries:
                    raise
                delay = min(5.0 * (2**attempt), 30.0)
                LOGGER.warning(
                    "temporary fal submission failure; retrying in %.0f seconds: %s",
                    delay,
                    exc,
                )
                sleeper(delay)
        raise AssertionError("unreachable")

    def _ensure_animation(
        self,
        video: Any,
        scene: Path,
        references: list[Path],
        direction: OpeningDirection,
        intermediate: Path,
    ) -> Path:
        video_id = int(video["id"])
        target = intermediate / "opening.mp4"
        frames_dir = intermediate / "review-frames"
        review = self.database.get_video_stage(video_id, "animation_review")
        existing = self._stage_file(
            self.database.get_video_stage(video_id, "animation")
        )
        if review is not None and review["status"] == "complete" and existing:
            return existing

        while True:
            stage = self.database.get_video_stage(video_id, "animation")
            review = self.database.get_video_stage(video_id, "animation_review")
            existing = self._stage_file(stage)
            should_reuse = existing is not None and (
                review is None or review["status"] in {"pending", "running"}
            )
            if not should_reuse:
                job = (
                    self._animation_job(video_id)
                    if stage is not None and stage["status"] == "running"
                    else None
                )
                new_submission = job is None
                if new_submission:
                    attempts = int(stage["attempts"]) if stage else 0
                    # A running stage without a saved fal job means the request never
                    # returned an ID. Do not charge that network failure against the
                    # animation quality retry budget on resume.
                    if stage is not None and stage["status"] == "running":
                        attempts = max(0, attempts - 1)
                    if attempts >= self.settings.quality_retries + 1:
                        rejected = (
                            stage
                            if stage is not None and stage["status"] == "failed"
                            else review
                        )
                        reason = rejected["error"] if rejected is not None else None
                        message = (
                            "animation exhausted its quality retries "
                            f"({attempts} attempts)"
                        )
                        if reason:
                            message += f". Last rejection: {reason}"
                        raise QualityError(message)
                    self.database.start_video_stage(
                        video_id, "animation", clear_metadata=True
                    )
                    job = self._submit_animation(scene, direction)
                    self.database.update_video_stage_metadata(
                        video_id, "animation", {"fal_job": job.as_dict()}
                    )

                assert job is not None

                def update_queue_status(payload: dict[str, Any]) -> None:
                    self.database.update_video_stage_metadata(
                        video_id,
                        "animation",
                        {
                            "fal_job": job.as_dict(),
                            "last_queue_status": payload.get("status"),
                        },
                    )

                try:
                    self.kling.wait_and_download(
                        job,
                        target,
                        on_status=update_queue_status,
                        new_submission=new_submission,
                        prefer_result_endpoint=True,
                    )
                    validate_vertical_asset(target)
                except ProviderValidationError as exc:
                    LOGGER.warning(
                        "discarding invalid saved fal request for video %s and "
                        "retrying with the validated prompt: %s",
                        video["ordinal"],
                        exc,
                    )
                    self.database.fail_video_stage(video_id, "animation", str(exc))
                    self.database.reset_video_stage_attempts(video_id, "animation")
                    continue
                except QualityError as exc:
                    self.database.fail_video_stage(video_id, "animation", str(exc))
                    continue
                self.database.complete_video_stage(
                    video_id,
                    "animation",
                    output_path=target,
                    metadata={
                        "fal_job": job.as_dict(),
                        "last_queue_status": "COMPLETED",
                    },
                )
                existing = target
            is_reaction = bool(getattr(direction, "reaction_scene_key", None))
            # A three-second clip yields only three frames at the default 1 FPS.
            frame_options = {"count": 6, "fps": 2} if is_reaction else {"count": 3}
            frames = extract_review_frames(
                existing, frames_dir, **frame_options
            )
            self.database.start_video_stage(video_id, "animation_review")
            reaction_review = (
                {"reaction_key": direction.reaction_key} if is_reaction else {}
            )
            try:
                result = retry_transient(
                    lambda: self.gemini.review_visual(
                        frames, references, kind="opening animation", **reaction_review
                    ),
                    retries=self.settings.transient_retries,
                )
            except QualityError as exc:
                # Retain every review reason instead of replacing it on each retry.
                # The next paid attempt still uses the normal quality budget.
                previous = (
                    json.loads(review["metadata_json"])
                    if review is not None and review["metadata_json"]
                    else {}
                )
                rejections = list(previous.get("rejections", []))
                animation = self.database.get_video_stage(video_id, "animation")
                attempt = int(animation["attempts"]) if animation else 0
                rejections.append({"animation_attempt": attempt, "reason": str(exc)})
                self.database.update_video_stage_metadata(
                    video_id, "animation_review", {"rejections": rejections}
                )
                LOGGER.warning(
                    "video %s animation attempt %s rejected: %s",
                    video["ordinal"],
                    attempt,
                    exc,
                )
                self.database.fail_video_stage(video_id, "animation_review", str(exc))
                continue
            if review is not None and review["metadata_json"]:
                rejections = json.loads(review["metadata_json"]).get("rejections")
                if rejections:
                    result = {**result, "rejections": rejections}
            self.database.complete_video_stage(
                video_id, "animation_review", output_path=existing, metadata=result
            )
            return existing

    def _ensure_final(
        self, video: Any, opening: Path, music: Path | None, batch_dir: Path
    ) -> Path:
        video_id = int(video["id"])
        target = batch_dir / "final" / f"{int(video['ordinal']):02d}.mp4"
        validation = self.database.get_video_stage(video_id, "output_validation")
        existing = self._stage_file(self.database.get_video_stage(video_id, "compose"))
        if validation is not None and validation["status"] == "complete" and existing:
            return existing
        compose = self.database.get_video_stage(video_id, "compose")
        attempts = int(compose["attempts"]) if compose else 0
        while attempts < self.settings.quality_retries + 1:
            self.database.start_video_stage(video_id, "compose")
            attempts += 1
            try:
                self.composer.compose(
                    opening,
                    Path(video["demo_path"]),
                    music,
                    str(video["hook"]),
                    target,
                    hook_format=str(video["hook_format"]),
                )
                self.database.complete_video_stage(
                    video_id, "compose", output_path=target
                )
                self.database.start_video_stage(video_id, "output_validation")
                probe = validate_output(target, require_audio=False)
                self.database.complete_video_stage(
                    video_id,
                    "output_validation",
                    output_path=target,
                    metadata=probe.as_dict(),
                )
                return target
            except QualityError as exc:
                self.database.fail_video_stage(video_id, "output_validation", str(exc))
                self.database.fail_video_stage(video_id, "compose", str(exc))
        raise QualityError("composition exhausted its quality retries")

    def _process_video(
        self,
        video: Any,
        references: list[Path],
        descriptions: dict[str, str],
        music: Path | None,
        batch_dir: Path,
    ) -> tuple[Any, Path | None, str | None]:
        video_id = int(video["id"])
        intermediate = batch_dir / "intermediate" / f"{int(video['ordinal']):02d}"
        intermediate.mkdir(parents=True, exist_ok=True)
        try:
            demo_path = str(Path(video["demo_path"]).resolve())
            description = descriptions.get(demo_path)
            if description is None:
                raise AssetError(f"persisted demo is no longer approved: {demo_path}")
            direction = self._opening_direction_for_video(video)
            scene = self._ensure_scene(
                video, references, description, direction, intermediate
            )
            opening = self._ensure_animation(
                video, scene, references, direction, intermediate
            )
            final = self._ensure_final(video, opening, music, batch_dir)
            self.database.set_video_status(video_id, "complete")
            return video, final, None
        except Exception as exc:
            LOGGER.exception("video %s failed", video["ordinal"])
            self.database.set_video_status(video_id, "failed", str(exc))
            return video, None, str(exc)

    def _deliver(
        self,
        batch_id: int,
        run_date: date,
        results: list[tuple[Any, Path | None, str | None]],
    ) -> None:
        completed = [(video, path) for video, path, _ in results if path is not None]
        telegram = self._telegram_client()
        delivery_errors: list[str] = []
        for video, path in sorted(completed, key=lambda item: item[0]["ordinal"]):
            assert path is not None
            try:
                sent = retry_transient(
                    lambda v=video, p=path: telegram.send_document(
                        batch_id,
                        int(v["id"]),
                        int(v["ordinal"]),
                        p,
                        caption=str(v["title"]) or None,
                    ),
                    retries=self.settings.transient_retries,
                )
                status = self.database.delivery_status(
                    batch_id, f"video:{video['ordinal']}"
                )
                if not sent and status != "sent":
                    delivery_errors.append(
                        f"video #{video['ordinal']} is in delivery state {status}"
                    )
            except DeliveryUncertain as exc:
                LOGGER.exception("document delivery became uncertain")
                delivery_errors.append(str(exc))
            except Exception as exc:
                LOGGER.exception("document delivery failed")
                delivery_errors.append(f"video #{video['ordinal']}: {exc}")
        if completed:
            lines = [f"flagd UGC — {run_date.isoformat()}", ""]
            for video, path, error in sorted(
                results, key=lambda item: item[0]["ordinal"]
            ):
                suffix = "" if path is not None else " [FAILED]"
                lines.append(
                    f"{video['ordinal']}. title: {str(video['title']) or 'unavailable'}"
                    f"\nopening text: "
                    f"{visible_hook_text(str(video['hook']), str(video['hook_format']))}"
                    f"{suffix}"
                )
            try:
                sent = retry_transient(
                    lambda: telegram.send_text(
                        batch_id, "summary", "\n".join(lines), kind="summary"
                    ),
                    retries=self.settings.transient_retries,
                )
                status = self.database.delivery_status(batch_id, "summary")
                if not sent and status != "sent":
                    delivery_errors.append(f"summary is in delivery state {status}")
            except Exception as exc:
                LOGGER.exception("summary delivery failed")
                delivery_errors.append(f"summary: {exc}")
        if len(completed) != len(results):
            failures = [
                f"#{video['ordinal']}: {error}"
                for video, path, error in results
                if path is None
            ]
            self._send_alert(
                batch_id,
                "partial-alert",
                f"flagd UGC batch {run_date.isoformat()} completed only "
                f"{len(completed)}/{len(results)} videos.\n" + "\n".join(failures),
            )
        if delivery_errors:
            self._send_alert(
                batch_id,
                "delivery-alert",
                f"flagd UGC delivery issue for {run_date.isoformat()}:\n"
                + "\n".join(delivery_errors),
            )

    def run(
        self, run_date: date, *, count: int | None = None, deliver: bool = True
    ) -> BatchResult:
        self._require_secrets(deliver)
        expected = self.settings.video_count if count is None else count
        if expected < 1:
            raise ValueError("video count must be positive")
        batch = self.database.get_or_create_batch(run_date, expected)
        batch_id = int(batch["id"])
        lease_key = f"batch:{run_date.isoformat()}"
        owner = f"{socket.gethostname()}:{uuid.uuid4()}"
        lease_ttl = timedelta(hours=3)
        if not self.database.acquire_lease(lease_key, owner, lease_ttl):
            LOGGER.info("batch %s is already being processed", run_date)
            return BatchResult(
                batch_id, run_date, str(batch["status"]), 0, expected, True
            )
        try:
            if batch["status"] != "complete":
                self._check_fal_queue_access()
            references = load_character_references(self.settings.characters_dir)
            demos = load_demos(self.settings.demos_dir)
            videos = self._ensure_hooks(batch_id, expected, demos, run_date)
            descriptions = {
                str(demo.path.resolve()): demo.description for demo in demos
            }
            batch_dir = self.settings.renders_dir / run_date.isoformat()
            results: list[tuple[Any, Path | None, str | None]] = []
            self.database.renew_lease(lease_key, owner, lease_ttl)
            # Process sequentially because the managed HTTPS proxy blocks bursts of
            # concurrent fal POST/poll requests. The 08:00–10:00 California production window
            # comfortably accommodates four sequential three-second renders.
            for video in videos:
                self.database.renew_lease(lease_key, owner, lease_ttl)
                results.append(
                    self._process_video(
                        video,
                        references,
                        descriptions,
                        None,
                        batch_dir,
                    )
                )
            results.sort(key=lambda item: item[0]["ordinal"])
            complete_count = sum(path is not None for _, path, _ in results)
            status = (
                "complete"
                if complete_count == expected
                else "partial"
                if complete_count
                else "failed"
            )
            errors = "; ".join(error for _, _, error in results if error)
            self.database.set_batch_status(batch_id, status, errors or None)
            if deliver:
                self._deliver(batch_id, run_date, results)
            return BatchResult(batch_id, run_date, status, complete_count, expected)
        except Exception as exc:
            self.database.set_batch_status(batch_id, "failed", str(exc))
            if deliver:
                self._send_alert(
                    batch_id,
                    "batch-alert",
                    f"flagd UGC batch {run_date.isoformat()} failed before completion: {exc}",
                )
            raise
        finally:
            self.database.release_lease(lease_key, owner)

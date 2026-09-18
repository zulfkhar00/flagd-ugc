import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from flagd_ugc.config import Settings
from flagd_ugc.db import Database
from flagd_ugc.errors import ProviderError, ProxyBlockError, TransientError
from flagd_ugc.fal_provider import FalJob
from flagd_ugc.media import ProbeResult
from flagd_ugc.pipeline import Pipeline


class FakeGemini:
    def __init__(self):
        self.hook_calls = 0
        self.scene_calls = 0
        self.review_calls = 0

    def generate_hooks(self, demos, *, formats):
        self.hook_calls += 1
        samples = {
            "pov": ("pov: the feedback explained it", "the feedback result"),
            "quote-receipt": ("nailed it", "his texting confidence"),
            "when": ("when i get the feedback", "the feedback moment"),
            "direct-observation": (
                "the feedback made sense",
                "the conversation feedback",
            ),
            "reaction-meme": (
                "my face when i get feedback",
                "the feedback reaction",
            ),
            "scorecard": ("rating the chat: 43/100", "the 43 conversation score"),
            "inner-thought": (
                "i did not expect that feedback",
                "the unexpected feedback",
            ),
        }
        hook, title = samples[formats[0].key]
        return [
            {
                "hook": hook,
                "hook_format": formats[0].key,
                "title": title,
                "demo_path": str(demos[0].path),
            }
        ]

    def generate_scene(self, hook, description, references, output, *, direction=None):
        self.scene_calls += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"scene")

    def review_visual(self, images, references, *, kind):
        self.review_calls += 1
        return {
            "passed": True,
            "adult": True,
            "consistent": True,
            "defect_free": True,
            "reason": "ok",
        }


class FakeKling:
    def __init__(self):
        self.calls = 0
        self.motion_prompts = []
        self.submitted_jobs = []
        self.waits = []

    def submit(self, scene, prompt, *, duration, allow_hand_entry=False):
        self.calls += 1
        self.motion_prompts.append(prompt)
        job = FalJob(
            f"task-{self.calls}",
            f"https://queue.test/{self.calls}/status",
            f"https://queue.test/{self.calls}/response",
            f"https://queue.test/{self.calls}/cancel",
        )
        self.submitted_jobs.append((job, duration, allow_hand_entry))
        return job

    def wait_and_download(
        self,
        job,
        output,
        *,
        on_status,
        new_submission=False,
        prefer_result_endpoint=False,
    ):
        self.waits.append((job, new_submission, prefer_result_endpoint))
        on_status({"status": "COMPLETED"})
        output.write_bytes(b"video")
        return {"video": {"url": "https://cdn.test/video.mp4"}}


class FakeComposer:
    def __init__(self):
        self.calls = 0
        self.music = []

    def compose(self, opening, demo, music, hook, output, *, hook_format):
        self.calls += 1
        self.music.append(music)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")


def test_daily_fal_submission_cools_down_after_proxy_block(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        fal_api_key="fal",
        transient_retries=2,
        font_file=tmp_path / "font.ttf",
    )

    class ProxyBlockedOnce(FakeKling):
        def __init__(self):
            super().__init__()
            self.sleeps = []
            self.sleep = self.sleeps.append

        def submit(self, scene, prompt, *, duration, allow_hand_entry=False):
            if not self.calls:
                self.calls += 1
                raise ProxyBlockError("temporary 403 HTML")
            return super().submit(
                scene,
                prompt,
                duration=duration,
                allow_hand_entry=allow_hand_entry,
            )

    kling = ProxyBlockedOnce()
    pipeline = Pipeline(
        settings,
        gemini=FakeGemini(),
        kling=kling,
        composer=FakeComposer(),
    )
    direction = SimpleNamespace(
        motion_prompt=lambda: "small silent reaction",
        allow_hand_entry=False,
    )

    job = pipeline._submit_animation(tmp_path / "scene.png", direction)

    assert job.request_id == "task-2"
    assert kling.sleeps == [180.0]


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_text(self, batch_id, key, text, *, kind):
        self.messages.append((key, text, kind))
        return True

    def send_document(self, batch_id, video_id, ordinal, path, *, caption=None):
        self.messages.append((f"video:{ordinal}", caption, "document"))
        return True


class DeliveryLedgerTelegram:
    """Small in-memory transport backed by the same durable delivery ledger as Telegram."""

    def __init__(self, database: Database):
        self.database = database
        self.documents = []
        self.messages = []

    def send_document(self, batch_id, video_id, ordinal, path, *, caption=None):
        key = f"video:{ordinal}"
        if not self.database.claim_delivery(batch_id, key, "document", video_id):
            return False
        self.documents.append((ordinal, path, caption))
        self.database.complete_delivery(batch_id, key, ordinal)
        return True

    def send_text(self, batch_id, key, text, *, kind):
        if not self.database.claim_delivery(batch_id, key, kind):
            return False
        self.messages.append((key, text, kind))
        self.database.complete_delivery(batch_id, key, len(self.messages))
        return True


def test_daily_pipeline_defaults_to_the_fal_kling_adapter(tmp_path, monkeypatch):
    created = {}

    class DefaultFal:
        def __init__(self, api_key, **kwargs):
            created["api_key"] = api_key
            created.update(kwargs)

    monkeypatch.setattr("flagd_ugc.pipeline.FalKlingProvider", DefaultFal)
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="",
        telegram_bot_token="telegram",
        telegram_chat_id="chat",
        fal_api_key="fal-secret",
        fal_kling_model="fal-ai/kling-video/v3/standard/image-to-video",
        font_file=tmp_path / "font.ttf",
    )

    pipeline = Pipeline(settings, gemini=object(), composer=object())

    assert isinstance(pipeline.kling, DefaultFal)
    assert created["api_key"] == "fal-secret"
    assert created["model"] == "fal-ai/kling-video/v3/standard/image-to-video"


def test_daily_batch_checks_fal_queue_before_any_generation(tmp_path):
    class BlockedFal:
        def check_queue_access(self):
            raise ProviderError("fal queue blocked")

    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        fal_api_key="fal",
        font_file=tmp_path / "font.ttf",
    )
    gemini = FakeGemini()
    pipeline = Pipeline(settings, gemini=gemini, kling=BlockedFal(), composer=object())

    with pytest.raises(ProviderError, match="fal queue blocked"):
        pipeline.run(date(2026, 9, 19), count=1, deliver=False)

    assert gemini.hook_calls == 0

    def send_text(self, batch_id, key, text, *, kind):
        if not self.database.claim_delivery(batch_id, key, kind):
            return False
        self.messages.append((key, text, kind))
        self.database.complete_delivery(batch_id, key, len(self.messages))
        return True


def test_completed_stages_are_not_generated_or_delivered_twice(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="kling",
        telegram_bot_token="telegram",
        telegram_chat_id="chat",
        fal_api_key="fal",
        video_count=1,
        font_file=tmp_path / "font.ttf",
    )
    settings.create_directories()
    (settings.characters_dir / "woman.png").write_bytes(b"reference")
    (settings.demos_dir / "demo.mp4").write_bytes(b"demo")
    settings.font_file.write_bytes(b"font")

    def fake_frames(video, output_dir, count=3):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(count):
            path = output_dir / f"frame-{index}.jpg"
            path.write_bytes(b"frame")
            paths.append(path)
        return paths

    monkeypatch.setattr("flagd_ugc.pipeline.extract_review_frames", fake_frames)
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_vertical_asset", lambda path: (1080, 1920)
    )
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_output",
        lambda path, require_audio=True: ProbeResult(1080, 1920, "h264", 30, None, 15),
    )
    gemini = FakeGemini()
    kling = FakeKling()
    composer = FakeComposer()
    pipeline = Pipeline(settings, gemini=gemini, kling=kling, composer=composer)

    first = pipeline.run(date(2026, 9, 16), count=1, deliver=False)
    second = pipeline.run(date(2026, 9, 16), count=1, deliver=False)

    assert first.status == second.status == "complete"
    assert (gemini.hook_calls, gemini.scene_calls, gemini.review_calls) == (1, 1, 2)
    assert kling.calls == 1
    assert composer.calls == 1
    assert composer.music == [None]
    assert kling.motion_prompts[0]


def test_empty_music_bank_does_not_block_silent_generation(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="kling",
        telegram_bot_token="telegram",
        telegram_chat_id="chat",
        fal_api_key="fal",
        video_count=1,
        font_file=tmp_path / "font.ttf",
    )
    settings.create_directories()
    (settings.characters_dir / "woman.png").write_bytes(b"reference")
    (settings.demos_dir / "demo.mp4").write_bytes(b"demo")
    settings.font_file.write_bytes(b"font")

    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_vertical_asset", lambda path: (1080, 1920)
    )
    # The fake reviewer needs readable paths even though no real media is rendered.
    def fake_frames(_video, output_dir, count=3):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [output_dir / f"frame-{index}.jpg" for index in range(count)]
        for path in paths:
            path.write_bytes(b"frame")
        return paths

    monkeypatch.setattr("flagd_ugc.pipeline.extract_review_frames", fake_frames)
    audio_requirements = []
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_output",
        lambda _path, require_audio=True: (
            audio_requirements.append(require_audio)
            or ProbeResult(1080, 1920, "h264", 30, None, 15)
        ),
    )
    gemini = FakeGemini()
    composer = FakeComposer()
    pipeline = Pipeline(
        settings,
        gemini=gemini,
        kling=FakeKling(),
        composer=composer,
    )

    result = pipeline.run(date(2026, 9, 17), count=1, deliver=False)

    assert result.status == "complete"
    assert gemini.hook_calls == 1
    assert composer.music == [None]
    assert audio_requirements == [False]


def test_submitted_fal_job_resumes_without_a_second_paid_submission(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="",
        telegram_bot_token="telegram",
        telegram_chat_id="chat",
        fal_api_key="fal",
        video_count=1,
        font_file=tmp_path / "font.ttf",
    )
    settings.create_directories()
    (settings.characters_dir / "woman.png").write_bytes(b"reference")
    (settings.demos_dir / "demo.mp4").write_bytes(b"demo")
    settings.font_file.write_bytes(b"font")

    def fake_frames(_video, output_dir, count=3):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [output_dir / f"frame-{index}.jpg" for index in range(count)]
        for path in paths:
            path.write_bytes(b"frame")
        return paths

    monkeypatch.setattr("flagd_ugc.pipeline.extract_review_frames", fake_frames)
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_vertical_asset", lambda _path: (1080, 1920)
    )
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_output",
        lambda _path, require_audio=True: ProbeResult(1080, 1920, "h264", 30, None, 15),
    )

    class InterruptedFal(FakeKling):
        def wait_and_download(
            self,
            job,
            output,
            *,
            on_status,
            new_submission=False,
            prefer_result_endpoint=False,
        ):
            self.waits.append((job, new_submission, prefer_result_endpoint))
            on_status({"status": "IN_PROGRESS"})
            if len(self.waits) == 1:
                raise TransientError("network interrupted after fal submission")
            output.write_bytes(b"video")
            return {"video": {"url": "https://cdn.test/video.mp4"}}

    kling = InterruptedFal()
    pipeline = Pipeline(
        settings,
        gemini=FakeGemini(),
        kling=kling,
        composer=FakeComposer(),
    )

    first = pipeline.run(date(2026, 9, 16), count=1, deliver=False)
    video = pipeline.database.list_videos(first.batch_id)[0]
    animation = pipeline.database.get_video_stage(int(video["id"]), "animation")
    second = pipeline.run(date(2026, 9, 16), count=1, deliver=False)

    assert first.status == "failed"
    assert '"fal_job"' in animation["metadata_json"]
    assert second.status == "complete"
    assert kling.calls == 1
    assert [new_submission for _, new_submission, _ in kling.waits] == [True, False]


def test_daily_batch_creates_four_distinct_silent_videos_and_delivers_titles(
    tmp_path, monkeypatch
):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini",
        kling_api_key="kling",
        telegram_bot_token="telegram",
        telegram_chat_id="chat",
        fal_api_key="fal",
        video_count=4,
        font_file=tmp_path / "font.ttf",
    )
    settings.create_directories()
    (settings.characters_dir / "woman.png").write_bytes(b"reference")
    descriptions = (
        "The user explores practice partners.",
        "The user practises a text opener.",
        "The app gives feedback after a simulated date.",
        "The user browses practice drills.",
        "The app gives an immediate positive result.",
        "The app explains an opener score.",
        "The Progress screen shows an overall conversation score of 43 out of 100.",
        "The app gives feedback after a positive simulated date.",
    )
    entries = []
    for ordinal, description in enumerate(descriptions, start=1):
        file = f"demo-{ordinal}.mp4"
        (settings.demos_dir / file).write_bytes(b"demo")
        entries.append({"file": file, "description": description, "approved": True})
    (settings.demos_dir / "manifest.json").write_text(
        json.dumps({"demos": entries}), encoding="utf-8"
    )
    settings.font_file.write_bytes(b"font")

    class FourVideoGemini(FakeGemini):
        def generate_hooks(self, demos, *, formats):
            self.hook_calls += 1
            hooks = {
                "scorecard": "rating the chat: 43/100",
                "inner-thought": "i did not expect that feedback",
                "pov": "pov: the feedback explained it",
                "quote-receipt": "nailed it",
            }
            titles = {
                "scorecard": "the 43 conversation score",
                "inner-thought": "the unexpected feedback",
                "pov": "the practice date result",
                "quote-receipt": "his texting confidence",
            }
            return [
                {
                    "demo_path": str(demo.path),
                    "hook_format": format.key,
                    "hook": hooks[format.key],
                    "title": titles[format.key],
                }
                for demo, format in zip(demos, formats, strict=True)
            ]

    def fake_frames(_video, output_dir, count=3):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [output_dir / f"frame-{index}.jpg" for index in range(count)]
        for path in paths:
            path.write_bytes(b"frame")
        return paths

    validations = []
    monkeypatch.setattr("flagd_ugc.pipeline.extract_review_frames", fake_frames)
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_vertical_asset", lambda _path: (1080, 1920)
    )
    monkeypatch.setattr(
        "flagd_ugc.pipeline.validate_output",
        lambda _path, require_audio=True: (
            validations.append(require_audio)
            or ProbeResult(1080, 1920, "h264", 30, None, 15)
        ),
    )
    gemini = FourVideoGemini()
    kling = FakeKling()
    composer = FakeComposer()
    database = Database(settings.db_path)
    telegram = DeliveryLedgerTelegram(database)
    pipeline = Pipeline(
        settings,
        database=database,
        gemini=gemini,
        kling=kling,
        composer=composer,
        telegram=telegram,
    )

    first = pipeline.run(date(2026, 9, 18), deliver=True)
    videos = pipeline.database.list_videos(first.batch_id)
    second = pipeline.run(date(2026, 9, 18), deliver=True)

    assert (first.status, first.completed, first.expected) == ("complete", 4, 4)
    assert second.status == "complete"
    assert len({row["hook_format"] for row in videos}) == 4
    assert len({row["demo_path"] for row in videos}) == 4
    assert len({row["title"] for row in videos}) == 4
    assert len({row["opening_direction"] for row in videos}) == 4
    assert composer.music == [None, None, None, None]
    assert validations == [False, False, False, False]
    assert [item[2] for item in telegram.documents] == [row["title"] for row in videos]
    assert "title: the 43 conversation score" in telegram.messages[0][1]
    assert "opening text:" in telegram.messages[0][1]
    assert len(telegram.documents) == 4
    assert len(telegram.messages) == 1
    assert (gemini.hook_calls, gemini.scene_calls, kling.calls, composer.calls) == (1, 4, 4, 4)

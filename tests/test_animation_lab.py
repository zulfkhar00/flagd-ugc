import json

import pytest

from flagd_ugc.animation_lab import AnimationLab, AnimationProbe
from flagd_ugc.errors import PipelineError, ProxyBlockError, TransientError
from flagd_ugc.fal_provider import FalJob


class FakeProvider:
    model = "fal-ai/kling-video/v3/standard/image-to-video"

    def __init__(self, *, interrupt=False, keyboard_interrupt=False):
        self.submit_calls = 0
        self.wait_calls = 0
        self.interrupt = interrupt
        self.keyboard_interrupt = keyboard_interrupt
        self.new_submission = None

    def submit(self, scene, prompt, *, duration, allow_hand_entry=False):
        self.submit_calls += 1
        self.allow_hand_entry = allow_hand_entry
        return FalJob(
            "job-1",
            "https://queue.test/status",
            "https://queue.test/response",
            "https://queue.test/cancel",
        )

    def wait_and_download(
        self,
        job,
        output,
        *,
        on_status,
        new_submission=False,
        prefer_result_endpoint=False,
    ):
        self.wait_calls += 1
        self.new_submission = new_submission
        self.prefer_result_endpoint = prefer_result_endpoint
        on_status({"status": "IN_PROGRESS"})
        if self.keyboard_interrupt:
            raise KeyboardInterrupt
        if self.interrupt:
            raise TransientError("connection interrupted")
        output.write_bytes(b"video")
        return {"video": {"url": "https://cdn.test/video.mp4"}}


@pytest.fixture
def media_checks(monkeypatch):
    # This fixture replaces every media operation; unit tests need no FFmpeg binary.
    monkeypatch.setattr("flagd_ugc.animation_lab.require_media_tools", lambda: None)
    monkeypatch.setattr(
        "flagd_ugc.animation_lab.validate_vertical_asset", lambda path: (720, 1280)
    )
    monkeypatch.setattr(
        "flagd_ugc.animation_lab.validate_animation",
        lambda path, duration: AnimationProbe(720, 1280, "h264", 30, 3.0, False),
    )

    def frames(video, output_dir, count, *, fps=1):
        output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for index in range(count):
            path = output_dir / f"frame-{index + 1:02d}.jpg"
            path.write_bytes(b"frame")
            result.append(path)
        return result

    monkeypatch.setattr("flagd_ugc.animation_lab.extract_review_frames", frames)


def test_animation_lab_persists_request_and_review_bundle(tmp_path, media_checks):
    scene_run = tmp_path / "scene-run"
    scene_run.mkdir()
    image = scene_run / "generated-01.png"
    image.write_bytes(b"image")
    (scene_run / "master.jpeg").write_bytes(b"master")
    provider = FakeProvider()

    result = AnimationLab(provider).run(
        image=image,
        motion_prompt="small natural reaction",
        output_root=tmp_path / "animations",
        duration=3,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert result.status == "complete"
    assert provider.submit_calls == 1
    assert provider.new_submission is True
    assert provider.prefer_result_endpoint is True
    assert manifest["request"]["request_id"] == "job-1"
    assert manifest["estimated_cost_usd"] == 0.252
    assert manifest["generate_audio"] is False
    assert manifest["allow_hand_entry"] is False
    assert manifest["validation"]["video_codec"] == "h264"
    assert manifest["review_fps"] == 4
    assert len(manifest["review_frames"]) == 12
    assert "Manual hard gates" in result.report_path.read_text()


def test_animation_lab_passes_hand_entry_guardrail(tmp_path, media_checks):
    image = tmp_path / "generated-01.png"
    image.write_bytes(b"image")
    provider = FakeProvider()

    result = AnimationLab(provider).run(
        image=image,
        motion_prompt="one hand rises to cover her mouth",
        output_root=tmp_path / "animations",
        duration=3,
        allow_hand_entry=True,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert provider.allow_hand_entry is True
    assert manifest["allow_hand_entry"] is True


def test_animation_lab_records_pro_tier_and_cost(tmp_path, media_checks):
    image = tmp_path / "generated-01.png"
    image.write_bytes(b"image")

    class ProProvider(FakeProvider):
        model = "fal-ai/kling-video/v3/pro/image-to-video"

    result = AnimationLab(ProProvider()).run(
        image=image,
        motion_prompt="small natural reaction",
        output_root=tmp_path / "animations",
        duration=4,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert "kling-v3-pro" in result.run_dir.name
    assert manifest["model"] == ProProvider.model
    assert manifest["estimated_cost_usd"] == 0.448


def test_animation_lab_resume_never_submits_again(tmp_path, media_checks):
    image = tmp_path / "generated-01.png"
    image.write_bytes(b"image")
    first_provider = FakeProvider(interrupt=True)
    output_root = tmp_path / "animations"

    with pytest.raises(PipelineError, match="experiment saved"):
        AnimationLab(first_provider).run(
            image=image,
            motion_prompt="small natural reaction",
            output_root=output_root,
            duration=3,
        )

    run_dir = next(output_root.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["request"]["request_id"] == "job-1"

    resumed_provider = FakeProvider()
    result = AnimationLab(resumed_provider).resume(run_dir)

    assert result.status == "complete"
    assert resumed_provider.submit_calls == 0
    assert resumed_provider.wait_calls == 1
    assert resumed_provider.new_submission is False
    assert resumed_provider.prefer_result_endpoint is True


def test_animation_lab_ctrl_c_preserves_request_for_resume(tmp_path, media_checks):
    image = tmp_path / "generated-01.png"
    image.write_bytes(b"image")
    output_root = tmp_path / "animations"

    with pytest.raises(PipelineError, match="resume it without --confirm-paid"):
        AnimationLab(FakeProvider(keyboard_interrupt=True)).run(
            image=image,
            motion_prompt="small natural reaction",
            output_root=output_root,
            duration=3,
        )

    manifest = json.loads(
        (next(output_root.iterdir()) / "manifest.json").read_text()
    )
    assert manifest["status"] == "interrupted"
    assert manifest["request"]["request_id"] == "job-1"
    assert "local polling interrupted" in manifest["error"]


def test_animation_lab_retries_proxy_block_before_request_exists(
    tmp_path, media_checks
):
    image = tmp_path / "generated-01.png"
    image.write_bytes(b"image")
    sleeps = []

    class ProxyThenSuccessProvider(FakeProvider):
        transient_retries = 3

        def __init__(self):
            super().__init__()
            self.sleep = sleeps.append

        def submit(self, scene, prompt, *, duration, allow_hand_entry=False):
            self.submit_calls += 1
            if self.submit_calls < 3:
                raise ProxyBlockError("proxy denied request before submission")
            return FalJob(
                "job-after-proxy",
                "https://queue.test/status",
                "https://queue.test/response",
                "https://queue.test/cancel",
            )

    provider = ProxyThenSuccessProvider()

    result = AnimationLab(provider).run(
        image=image,
        motion_prompt="small natural reaction",
        output_root=tmp_path / "animations",
        duration=3,
    )

    assert result.status == "complete"
    assert provider.submit_calls == 3
    assert sleeps == [5.0, 10.0]

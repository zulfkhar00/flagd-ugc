import json
from types import SimpleNamespace

import pytest

from flagd_ugc.animation_lab import AnimationLabResult
from flagd_ugc.errors import QualityError
from flagd_ugc.hook_video_lab import HookVideoLab


class ImageProvider:
    def __init__(self):
        self.review_calls = []

    def review_visual(self, images, references, *, kind):
        self.review_calls.append((images, references, kind))
        return {
            "passed": True,
            "consistent": True,
            "defect_free": True,
            "self_shot": True,
            "reason": "ok",
        }


class AnimationProvider:
    pass


def test_hook_video_lab_keeps_scene_internal_and_returns_video_report(
    tmp_path, monkeypatch
):
    master = tmp_path / "master.jpeg"
    master.write_bytes(b"master")
    seen = {}

    class FakeSceneLab:
        def __init__(self, provider, *, transient_retries):
            seen["image_provider"] = provider
            seen["retries"] = transient_retries

        def run(self, **kwargs):
            seen["scene_args"] = kwargs
            run_dir = kwargs["output_root"] / "scene-run"
            run_dir.mkdir(parents=True)
            (run_dir / "generated-01.png").write_bytes(b"image")
            return SimpleNamespace(
                run_dir=run_dir,
                candidates=(
                    SimpleNamespace(
                        status="ready", file="generated-01.png"
                    ),
                ),
            )

    class FakeAnimationLab:
        def __init__(self, provider):
            seen["animation_provider"] = provider

        def run(self, **kwargs):
            seen["animation_args"] = kwargs
            run_dir = kwargs["output_root"] / "animation-run"
            run_dir.mkdir(parents=True)
            report = run_dir / "report.html"
            report.write_text("video review")
            manifest = run_dir / "manifest.json"
            manifest.write_text("{}")
            return AnimationLabResult(
                run_dir=run_dir,
                report_path=report,
                manifest_path=manifest,
                status="complete",
            )

    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.SceneLab", FakeSceneLab)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.AnimationLab", FakeAnimationLab)

    image_provider = ImageProvider()
    animation_provider = AnimationProvider()
    result = HookVideoLab(
        image_provider, animation_provider, transient_retries=3
    ).run(
        master=master,
        output_root=tmp_path / "hook-videos",
        scenario="bed-facepalm",
        model="gemini-3-pro-image",
        duration=3,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert result.status == "complete"
    assert result.report_path.read_text() == "video review"
    assert seen["scene_args"]["samples"] == 1
    assert seen["scene_args"]["dry_run"] is False
    assert seen["animation_args"]["image"].name == "generated-01.png"
    assert seen["animation_args"]["allow_hand_entry"] is True
    assert "she continues holding the front-facing phone herself" in seen["animation_args"][
        "motion_prompt"
    ].lower()
    assert manifest["internal_scene_run"].startswith("internal-scenes/")
    assert manifest["final_report"].endswith("report.html")
    assert manifest["handheld_required"] is True
    assert manifest["allow_hand_entry"] is True
    assert manifest["scene_review"]["self_shot"] is True
    assert manifest["internal_scene_attempts"][0]["status"] == "accepted"
    assert len(image_provider.review_calls) == 1
    assert image_provider.review_calls[0][2] == (
        "self-shot front-camera opening scene"
    )


def test_hook_video_lab_rejects_longer_untuned_clips(tmp_path, monkeypatch):
    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)

    with pytest.raises(ValueError, match="must be 3 or 4 seconds"):
        HookVideoLab(ImageProvider(), AnimationProvider()).run(
            master=tmp_path / "master.jpeg",
            output_root=tmp_path / "hook-videos",
            scenario="doorway-backout",
            model="gemini-3-pro-image",
            duration=5,
        )


def test_hook_video_lab_resumes_saved_animation_without_image_generation(
    tmp_path, monkeypatch
):
    root = tmp_path / "hook-run"
    animation_run = root / "animations" / "animation-run"
    animation_run.mkdir(parents=True)
    (animation_run / "manifest.json").write_text(
        json.dumps({"request": {"request_id": "already-paid"}})
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "status": "interrupted",
                "animation_run": "animations/animation-run",
                "final_report": None,
                "error": "polling interrupted",
            }
        )
    )
    seen = []

    class FakeAnimationLab:
        def __init__(self, provider):
            seen.append(("provider", provider))

        def resume(self, supplied_run):
            seen.append(("resume", supplied_run))
            report = supplied_run / "report.html"
            report.write_text("finished video")
            return AnimationLabResult(
                run_dir=supplied_run,
                report_path=report,
                manifest_path=supplied_run / "manifest.json",
                status="complete",
            )

    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.AnimationLab", FakeAnimationLab)
    animation_provider = AnimationProvider()

    result = HookVideoLab(None, animation_provider).resume(root)

    manifest = json.loads((root / "manifest.json").read_text())
    assert result.status == "complete"
    assert seen == [
        ("provider", animation_provider),
        ("resume", animation_run),
    ]
    assert manifest["status"] == "complete"
    assert manifest["final_report"] == "animations/animation-run/report.html"
    assert manifest["error"] is None


def test_hook_video_lab_regenerates_rejected_frame_before_animation(
    tmp_path, monkeypatch
):
    master = tmp_path / "master.jpeg"
    master.write_bytes(b"master")
    scene_calls = 0
    animation_inputs = []

    class ReviewingProvider:
        def __init__(self):
            self.calls = 0

        def review_visual(self, images, references, *, kind):
            self.calls += 1
            if self.calls == 1:
                raise QualityError("branched camera arm")
            return {
                "passed": True,
                "consistent": True,
                "defect_free": True,
                "self_shot": True,
                "reason": "ok",
            }

    class FakeSceneLab:
        def __init__(self, provider, *, transient_retries):
            pass

        def run(self, **kwargs):
            nonlocal scene_calls
            scene_calls += 1
            run_dir = kwargs["output_root"] / f"scene-{scene_calls}"
            run_dir.mkdir(parents=True)
            image = run_dir / "generated-01.png"
            image.write_bytes(f"image-{scene_calls}".encode())
            return SimpleNamespace(
                run_dir=run_dir,
                candidates=(SimpleNamespace(status="ready", file=image.name),),
            )

    class FakeAnimationLab:
        def __init__(self, provider):
            pass

        def run(self, **kwargs):
            animation_inputs.append(kwargs["image"])
            run_dir = kwargs["output_root"] / "animation-run"
            run_dir.mkdir(parents=True)
            report = run_dir / "report.html"
            report.write_text("review")
            return AnimationLabResult(
                run_dir=run_dir,
                report_path=report,
                manifest_path=run_dir / "manifest.json",
                status="complete",
            )

    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.SceneLab", FakeSceneLab)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.AnimationLab", FakeAnimationLab)
    reviewer = ReviewingProvider()

    result = HookVideoLab(
        reviewer,
        AnimationProvider(),
        quality_retries=2,
    ).run(
        master=master,
        output_root=tmp_path / "runs",
        scenario="bed-facepalm",
        model="gemini-3-pro-image",
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert scene_calls == 2
    assert reviewer.calls == 2
    assert len(animation_inputs) == 1
    assert animation_inputs[0].parent.name == "scene-2"
    assert [item["status"] for item in manifest["internal_scene_attempts"]] == [
        "quality-review-failed",
        "accepted",
    ]


def test_resume_reuses_saved_image_when_review_model_failed_before_animation(
    tmp_path, monkeypatch
):
    root = tmp_path / "hook-run"
    scene_run = root / "internal-scenes" / "scene-1"
    scene_run.mkdir(parents=True)
    (scene_run / "generated-01.png").write_bytes(b"saved-image")
    (scene_run / "master.jpeg").write_bytes(b"master")
    (scene_run / "manifest.json").write_text(
        json.dumps(
            {
                "master_file": "master.jpeg",
                "candidates": [
                    {"file": "generated-01.png", "status": "ready"}
                ],
            }
        )
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "status": "interrupted",
                "scenario": "bed-facepalm",
                "duration_seconds": 3,
                "internal_scene_run": None,
                "internal_scene_attempts": [
                    {
                        "attempt": 1,
                        "run": "internal-scenes/scene-1",
                        "status": "technical-validation-failed",
                        "error": None,
                    }
                ],
                "scene_review": None,
                "animation_run": None,
                "final_report": None,
                "error": "retired review model",
            }
        )
    )
    animation_inputs = []

    class FakeAnimationLab:
        def __init__(self, provider):
            pass

        def run(self, **kwargs):
            animation_inputs.append(kwargs["image"])
            run_dir = kwargs["output_root"] / "animation-run"
            run_dir.mkdir(parents=True)
            report = run_dir / "report.html"
            report.write_text("review")
            return AnimationLabResult(
                run_dir=run_dir,
                report_path=report,
                manifest_path=run_dir / "manifest.json",
                status="complete",
            )

    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.AnimationLab", FakeAnimationLab)
    reviewer = ImageProvider()

    result = HookVideoLab(reviewer, AnimationProvider()).resume(root)

    manifest = json.loads(result.manifest_path.read_text())
    assert result.status == "complete"
    assert animation_inputs == [scene_run / "generated-01.png"]
    assert reviewer.review_calls[0][0] == [scene_run / "generated-01.png"]
    assert manifest["internal_scene_run"] == "internal-scenes/scene-1"
    assert manifest["internal_scene_attempts"][0]["status"] == "accepted"
    assert manifest["animation_run"] == "animations/animation-run"


def test_resume_skips_repeated_review_when_saved_scene_already_passed(
    tmp_path, monkeypatch
):
    root = tmp_path / "hook-run"
    scene_run = root / "internal-scenes" / "scene-1"
    failed_animation = root / "animations" / "failed-submit"
    scene_run.mkdir(parents=True)
    failed_animation.mkdir(parents=True)
    (scene_run / "generated-01.png").write_bytes(b"saved-image")
    (scene_run / "master.jpeg").write_bytes(b"master")
    (scene_run / "manifest.json").write_text(
        json.dumps(
            {
                "master_file": "master.jpeg",
                "candidates": [{"file": "generated-01.png", "status": "ready"}],
            }
        )
    )
    (failed_animation / "manifest.json").write_text(
        json.dumps({"request": None, "status": "failed"})
    )
    passed_review = {
        "passed": True,
        "consistent": True,
        "defect_free": True,
        "self_shot": True,
        "reason": "ok",
    }
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "chair-confused-rise",
                "duration_seconds": 3,
                "internal_scene_attempts": [
                    {"attempt": 1, "run": "internal-scenes/scene-1"}
                ],
                "scene_review": passed_review,
                "animation_run": "animations/failed-submit",
            }
        )
    )

    class ReviewerMustNotRun:
        def review_visual(self, *args, **kwargs):
            raise AssertionError("accepted scene must not be reviewed twice")

    class FakeAnimationLab:
        def __init__(self, provider):
            pass

        def run(self, **kwargs):
            run_dir = kwargs["output_root"] / "successful-submit"
            run_dir.mkdir()
            report = run_dir / "report.html"
            report.write_text("review")
            return AnimationLabResult(
                run_dir=run_dir,
                report_path=report,
                manifest_path=run_dir / "manifest.json",
                status="complete",
            )

    monkeypatch.setattr("flagd_ugc.hook_video_lab.require_media_tools", lambda: None)
    monkeypatch.setattr("flagd_ugc.hook_video_lab.AnimationLab", FakeAnimationLab)

    result = HookVideoLab(ReviewerMustNotRun(), AnimationProvider()).resume(root)

    assert result.status == "complete"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["animation_run"] == "animations/successful-submit"

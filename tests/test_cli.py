import json
from pathlib import Path
from types import SimpleNamespace

from flagd_ugc import cli
from flagd_ugc.config import Settings
from flagd_ugc.errors import ProviderError


def test_help_exposes_video_first_hook_lab():
    assert "hook-video-lab" in cli._parser().format_help()


def test_compose_lab_exposes_all_approved_caption_formats():
    parser = cli._parser()
    args = parser.parse_args(
        [
            "compose-lab",
            "--opening",
            "opening.mp4",
            "--demo",
            "demo.mp4",
            "--hook",
            "i'm good at texting",
            "--hook-format",
            "quote-receipt",
            "--output",
            "output.mp4",
        ]
    )
    assert args.hook_format == "quote-receipt"


def test_help_exposes_four_video_batch_preview_lab():
    assert "batch-preview-lab" in cli._parser().format_help()


def test_character_samples_accepts_targeted_replacement():
    args = cli._parser().parse_args(
        ["character-samples", "--replace", "2", "--confirm-paid"]
    )

    assert args.replace == 2
    assert args.confirm_paid is True


def test_targeted_character_sample_replacement_preserves_other_results(
    tmp_path, monkeypatch
):
    root = tmp_path / "character-samples"
    root.mkdir()
    original = [
        {"file": f"sample-{index:02d}.png", "review": {"marker": index}}
        for index in range(1, 9)
    ]
    (root / "review.json").write_text(json.dumps(original), encoding="utf-8")
    (root / "APPROVED.json").write_text("{}", encoding="utf-8")
    generated = []

    class FakeGemini:
        def __init__(self, *args, **kwargs):
            pass

        def generate_scene(
            self, hook, description, references, output, *, direction=None
        ):
            generated.append(output.name)
            assert "free arm must not extend toward the lens" in direction
            output.write_bytes(b"replacement")

        def review_visual(self, images, references, *, kind):
            return {
                "passed": True,
                "consistent": True,
                "defect_free": True,
                "self_shot": True,
                "reason": "replacement passed",
            }

    monkeypatch.setattr(cli, "GeminiProvider", FakeGemini)
    monkeypatch.setattr(
        cli, "load_character_references", lambda directory: [Path("master.jpeg")]
    )
    monkeypatch.setattr(
        cli,
        "load_demos",
        lambda directory: [SimpleNamespace(description="demo")],
    )
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="secret",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )

    report_path = cli._character_samples(settings, 8, replace=2)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert generated == ["sample-02.png"]
    assert report[0] == original[0]
    assert report[2:] == original[2:]
    assert report[1]["review"]["reason"] == "replacement passed"
    assert not (root / "APPROVED.json").exists()


def test_hook_copy_lab_requires_an_explicit_text_call_choice():
    args = cli._parser().parse_args(["hook-copy-lab", "--dry-run"])
    assert args.dry_run is True
    assert args.confirm_paid is False


def test_hook_video_help_exposes_no_charge_resume(capsys):
    try:
        cli._parser().parse_args(["hook-video-lab", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "--resume" in capsys.readouterr().out


def test_animation_lab_accepts_explicit_kling_tier():
    args = cli._parser().parse_args(
        [
            "animation-lab",
            "--image",
            "opening.jpg",
            "--motion-prompt-file",
            "motion.txt",
            "--kling-tier",
            "pro",
            "--confirm-paid",
        ]
    )

    assert args.kling_tier == "pro"


def test_hook_video_lab_proxy_preflight_aborts_before_generation(
    tmp_path, monkeypatch
):
    settings = Settings(
        data_dir=tmp_path,
        gemini_api_key="gemini-secret",
        kling_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        fal_api_key="fal-secret",
    )
    events = []

    class FakeGemini:
        def __init__(self, *args, **kwargs):
            events.append("gemini-client-created")

    class FakeFal:
        def __init__(self, *args, **kwargs):
            events.append("fal-client-created")

        def check_queue_access(self):
            events.append("queue-preflight")
            raise ProviderError("denied by Bloomberg web policy")

    class NeverRunLab:
        def __init__(self, *args, **kwargs):
            raise AssertionError("generation must not start after failed preflight")

    monkeypatch.setattr(cli, "load_local_env", lambda: None)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "GeminiProvider", FakeGemini)
    monkeypatch.setattr(cli, "FalKlingProvider", FakeFal)
    monkeypatch.setattr(cli, "HookVideoLab", NeverRunLab)

    result = cli.main(
        [
            "hook-video-lab",
            "--master",
            str(tmp_path / "master.jpeg"),
            "--scenario",
            "doorway-backout",
            "--confirm-paid",
        ]
    )

    assert result == 2
    assert events == [
        "fal-client-created",
        "queue-preflight",
    ]

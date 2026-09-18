import json

from flagd_ugc.batch_preview import render_batch_preview
from flagd_ugc.media import ProbeResult


class FakeComposer:
    def __init__(self):
        self.formats = []

    def compose(self, opening, demo, music, hook, output, *, hook_format):
        assert music is None
        self.formats.append(hook_format)
        output.write_bytes(b"preview")


def test_four_video_preview_requires_distinct_valid_formats_and_writes_review(
    tmp_path, monkeypatch
):
    assets = tmp_path / "assets"
    demos_dir = assets / "demos"
    openings_dir = assets / "openings"
    demos_dir.mkdir(parents=True)
    openings_dir.mkdir()
    descriptions = [
        "The app gives feedback after a practice date.",
        "The app gives feedback after a practice date.",
        "The app gives feedback after a practice date.",
        "The Progress screen shows an overall conversation score of 43 out of 100.",
    ]
    demo_files = []
    opening_files = []
    for ordinal in range(1, 5):
        demo = demos_dir / f"demo-{ordinal}.mp4"
        opening = openings_dir / f"opening-{ordinal}.mp4"
        demo.write_bytes(b"demo")
        opening.write_bytes(b"opening")
        demo_files.append(demo)
        opening_files.append(opening)
    (demos_dir / "manifest.json").write_text(
        json.dumps(
            {
                "demos": [
                    {
                        "file": path.name,
                        "description": description,
                        "approved": True,
                    }
                    for path, description in zip(demo_files, descriptions, strict=True)
                ]
            }
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "asset_root": "assets",
                "demos_dir": "demos",
                "videos": [
                    {
                        "opening": f"openings/{opening_files[0].name}",
                        "demo": f"demos/{demo_files[0].name}",
                        "hook": "when i get the feedback",
                        "hook_format": "when",
                    },
                    {
                        "opening": f"openings/{opening_files[1].name}",
                        "demo": f"demos/{demo_files[1].name}",
                        "hook": "i did not expect that feedback",
                        "hook_format": "inner-thought",
                    },
                    {
                        "opening": f"openings/{opening_files[2].name}",
                        "demo": f"demos/{demo_files[2].name}",
                        "hook": "my face when i got the feedback",
                        "hook_format": "reaction-meme",
                    },
                    {
                        "opening": f"openings/{opening_files[3].name}",
                        "demo": f"demos/{demo_files[3].name}",
                        "hook": "rating the chat: 43/100",
                        "hook_format": "scorecard",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("flagd_ugc.batch_preview.validate_vertical_asset", lambda _: (1080, 1920))
    monkeypatch.setattr(
        "flagd_ugc.batch_preview.validate_output",
        lambda _path, require_audio: ProbeResult(1080, 1920, "h264", 30, None, 15),
    )
    composer = FakeComposer()

    result = render_batch_preview(
        plan_path, tmp_path / "output", tmp_path / "font.ttf", composer=composer
    )

    assert len(result.videos) == 4
    assert composer.formats == ["when", "inner-thought", "reaction-meme", "scorecard"]
    assert result.report_path.is_file()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["silent"] is True
    assert manifest["videos"][3]["visible_hook"] == "rating the chat: 43/100"

import json
from datetime import date

from flagd_ugc.errors import QualityError
from flagd_ugc.hook_copy_lab import HookCopyLab


def _demo_bank(root):
    root.mkdir()
    entries = []
    descriptions = (
        "The user browses practice partners.",
        "The user practises a text opener.",
        "The app gives feedback after a simulated date.",
        "The user browses drills.",
        "The app gives an immediate positive result.",
        "The app explains an opener score.",
        "The Progress screen shows an overall conversation score of 43 out of 100.",
        "The app gives feedback after a positive simulated date.",
    )
    for ordinal, description in enumerate(descriptions, start=1):
        path = root / f"demo-{ordinal}.mp4"
        path.write_bytes(b"demo")
        entries.append(
            {"file": path.name, "description": description, "approved": True}
        )
    (root / "manifest.json").write_text(
        json.dumps({"demos": entries}), encoding="utf-8"
    )


def test_hook_copy_dry_run_uses_four_distinct_formats_without_provider(tmp_path):
    demos_dir = tmp_path / "demos"
    _demo_bank(demos_dir)

    result = HookCopyLab().run(
        demos_dir, date(2026, 9, 18), tmp_path / "output", dry_run=True
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "dry-run"
    assert [item["hook_format"] for item in manifest["videos"]] == [
        "reaction-meme",
        "inner-thought",
        "direct-observation",
        "pov",
    ]
    assert manifest["videos"][0]["title"] == "the practice date feedback"
    assert result.report_path.is_file()


def test_hook_copy_lab_writes_generated_copy_in_assignment_order(tmp_path):
    demos_dir = tmp_path / "demos"
    _demo_bank(demos_dir)

    class Provider:
        def generate_hooks(self, demos, *, formats):
            samples = {
                "reaction-meme": "my face when i got the feedback",
                "inner-thought": "i did not expect that feedback",
                "direct-observation": "that feedback humbled me",
                "pov": "pov: the feedback explained it",
            }
            titles = {
                "reaction-meme": "the date feedback",
                "inner-thought": "the unexpected feedback",
                "direct-observation": "the conversation feedback",
                "pov": "the practice date result",
            }
            return list(
                reversed(
                    [
                        {
                            "demo_path": str(demo.path),
                            "hook_format": format_definition.key,
                            "hook": samples[format_definition.key],
                            "title": titles[format_definition.key],
                        }
                        for demo, format_definition in zip(demos, formats, strict=True)
                    ]
                )
            )

    result = HookCopyLab(Provider()).run(
        demos_dir, date(2026, 9, 18), tmp_path / "output"
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert [item["hook"] for item in manifest["videos"]] == [
        "my face when i got the feedback",
        "i did not expect that feedback",
        "that feedback humbled me",
        "pov: the feedback explained it",
    ]
    assert [item["title"] for item in manifest["videos"]] == [
        "the date feedback",
        "the unexpected feedback",
        "the conversation feedback",
        "the practice date result",
    ]


def test_hook_copy_lab_retries_a_format_quality_failure(tmp_path):
    demos_dir = tmp_path / "demos"
    _demo_bank(demos_dir)

    class Provider:
        calls = 0

        def generate_hooks(self, _demos, *, formats):
            self.calls += 1
            if self.calls == 1:
                raise QualityError("reaction does not fit the overlay")
            samples = {
                "reaction-meme": "my face when i got the feedback",
                "inner-thought": "i did not expect that feedback",
                "direct-observation": "that feedback humbled me",
                "pov": "pov: the feedback explained it",
            }
            titles = {
                "reaction-meme": "the date feedback",
                "inner-thought": "the unexpected feedback",
                "direct-observation": "the conversation feedback",
                "pov": "the practice date result",
            }
            return [
                {
                    "demo_path": str(demo.path),
                    "hook_format": format_definition.key,
                    "hook": samples[format_definition.key],
                    "title": titles[format_definition.key],
                }
                for demo, format_definition in zip(_demos, formats, strict=True)
            ]

    provider = Provider()
    result = HookCopyLab(provider, quality_retries=1).run(
        demos_dir, date(2026, 9, 18), tmp_path / "output"
    )

    assert provider.calls == 2
    assert result.status == "complete"

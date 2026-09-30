"""Render five silent task-hook-to-real-demo prototypes for local review."""
import html
import json
import shutil
import zipfile
from pathlib import Path

from flagd_ugc.config import default_font_file
from flagd_ugc.hook_formats import validate_formatted_hook
from flagd_ugc.media import MediaComposer, extract_review_frames, validate_output


ROOT = Path(__file__).resolve().parent
EXPERIMENT = ROOT.parent
PLAN = json.loads((ROOT / "plan.json").read_text())
DEMO_ROOT = Path("/data/demos")
OUT = ROOT / "clips"


def main() -> None:
    manifest = json.loads((DEMO_ROOT / "manifest.json").read_text())
    demos = {item["id"]: item for item in manifest["demos"] if item.get("approved")}
    composer = MediaComposer(default_font_file())
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    cards = []
    for item in PLAN:
        demo = demos[item["demo_id"]]
        validated_hook = validate_formatted_hook(
            item["hook"], item["hook_format"], demo_description=demo["description"]
        )
        opening = EXPERIMENT / "animation-v1" / "clips" / f"{item['key']}.mp4"
        demo_path = DEMO_ROOT / demo["file"]
        output = OUT / f"{item['key']}-full.mp4"
        composer.compose(
            opening,
            demo_path,
            None,
            validated_hook,
            output,
            hook_format=item["hook_format"],
        )
        probe = validate_output(output, require_audio=False)
        extract_review_frames(
            output,
            ROOT / "review-frames" / item["key"],
            count=6,
            fps=1,
        )
        card = {
            **item,
            "demo_file": demo["file"],
            "demo_description": demo["description"],
            "output": f"clips/{output.name}",
            "validation": probe.as_dict(),
        }
        summary.append(card)
        cards.append(
            "<article><h2>" + html.escape(item["key"]) + "</h2>"
            "<video controls playsinline preload=metadata src='clips/"
            + html.escape(output.name)
            + "'></video><p class='hook'>"
            + html.escape(validated_hook)
            + "</p><p><b>App demo:</b> "
            + html.escape(demo["description"])
            + "</p><p><b>Format:</b> "
            + html.escape(item["hook_format"])
            + " · <b>File:</b> "
            + html.escape(demo["file"])
            + "</p></article>"
        )

    (ROOT / "validation-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    page = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>Full-format UGC review</title>"
        "<style>body{font:16px system-ui;background:#202124;color:#eee;margin:24px}"
        "main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}"
        "article{background:#303134;padding:16px;border-radius:10px}"
        "video{width:100%;height:auto;display:block;background:#000}"
        ".hook{font-size:1.2rem;font-weight:700}p{line-height:1.45}</style>"
        "<h1>Task-driven hook → flagd demo</h1><p>Five silent 15-second prototypes. "
        "Each starts with a three-second task clip and TikTok-style text, then cuts directly "
        "to its matched approved app recording.</p><main>"
        + "\n".join(cards)
        + "</main>"
    )
    (ROOT / "review.html").write_text(page)
    with zipfile.ZipFile(ROOT / "five-full-format-prototypes.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.glob("*.mp4")):
            archive.write(path, arcname=f"clips/{path.name}")
        archive.write(ROOT / "review.html", arcname="review.html")
        archive.write(ROOT / "validation-summary.json", arcname="validation-summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

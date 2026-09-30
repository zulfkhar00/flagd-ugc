"""Build the six-context contact sheet and review page from selected stills."""
import html
import json
import shutil
import textwrap
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2] / "data" / "experiments" / "task-context-generalization-v1"
RECIPES = Path(__file__).with_name("recipes.json")


def main() -> None:
    recipes = json.loads(RECIPES.read_text())
    cols, thumb_w, thumb_h = 3, 360, 640
    label_h, gap, margin = 64, 20, 20
    rows = (len(recipes) + cols - 1) // cols
    tile_w, tile_h = thumb_w, thumb_h + label_h
    sheet = Image.new(
        "RGB",
        (margin * 2 + cols * tile_w + (cols - 1) * gap,
         margin * 2 + rows * tile_h + (rows - 1) * gap),
        "#202124",
    )
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("Arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    selected = ROOT / "selected-frames"
    selected.mkdir(exist_ok=True)
    cards = []
    with ZipFile(ROOT / "selected-frames.zip", "w", ZIP_DEFLATED) as archive:
        for idx, recipe in enumerate(recipes):
            key = recipe["key"]
            source_path = ROOT / f"{key}-replacement.png"
            if not source_path.is_file():
                source_path = ROOT / f"{key}.png"
            selected_path = selected / f"{key}.png"
            shutil.copy2(source_path, selected_path)
            archive.write(selected_path, arcname=f"{key}.png")
            with Image.open(source_path) as source:
                image = source.convert("RGB")
                image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            col, row = idx % cols, idx // cols
            x, y = margin + col * (tile_w + gap), margin + row * (tile_h + gap)
            sheet.paste(image, (x + (thumb_w - image.width) // 2, y))
            label = "\n".join(textwrap.wrap(recipe["label"], width=32))
            draw.multiline_text((x, y + thumb_h + 8), label, fill="white", font=font, spacing=3)
            cards.append(
                f'<article><img src="selected-frames/{html.escape(key)}.png" '
                f'alt="{html.escape(recipe["label"])}"><h2>{html.escape(recipe["label"])}</h2>'
                f'<p><code>{html.escape(key)}</code></p></article>'
            )

    sheet.save(ROOT / "contact-sheet.jpg", quality=92)
    page = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Task-context generalization review</title><style>
body{font:16px system-ui,sans-serif;background:#202124;color:#eee;margin:24px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}
article{background:#303134;padding:12px;border-radius:8px}img{width:100%;height:auto;display:block}
h2{font-size:1rem;margin:.7em 0 .2em}code{color:#bbb}</style>
<h1>Task-context generalization</h1><p>Six everyday settings, still frames only before motion review.</p>
<main>""" + "\n".join(cards) + "</main>"
    (ROOT / "review.html").write_text(page)
    print(ROOT / "contact-sheet.jpg")
    print(ROOT / "review.html")
    print(ROOT / "selected-frames.zip")


if __name__ == "__main__":
    main()

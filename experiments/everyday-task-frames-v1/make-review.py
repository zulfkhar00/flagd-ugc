"""Build a labeled contact sheet and a simple full-size review page."""
import html
import json
import shutil
import textwrap
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2] / "data" / "experiments" / "everyday-task-frames-v1"
RECIPES = Path(__file__).with_name("recipes.json")


def main() -> None:
    recipes = json.loads(RECIPES.read_text())
    columns = 4
    thumb_w, thumb_h = 300, 534
    label_h, gap, margin = 64, 20, 20
    rows = (len(recipes) + columns - 1) // columns
    tile_w, tile_h = thumb_w, thumb_h + label_h
    sheet = Image.new(
        "RGB",
        (margin * 2 + columns * tile_w + (columns - 1) * gap,
         margin * 2 + rows * tile_h + (rows - 1) * gap),
        "#202124",
    )
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("Arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    cards = []
    selected_dir = ROOT / "selected-frames"
    selected_dir.mkdir(exist_ok=True)
    zip_path = ROOT / "selected-frames.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as selected_zip:
        for index, recipe in enumerate(recipes):
            key = recipe["key"]
            image_path = ROOT / f"{key}-replacement-v2.png"
            if not image_path.is_file():
                image_path = ROOT / f"{key}-replacement.png"
            if not image_path.is_file():
                image_path = ROOT / f"{key}.png"
            selected_path = selected_dir / f"{key}.png"
            shutil.copy2(image_path, selected_path)
            selected_zip.write(selected_path, arcname=f"{key}.png")
            with Image.open(image_path) as source:
                image = source.convert("RGB")
                image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            col, row = index % columns, index // columns
            x = margin + col * (tile_w + gap)
            y = margin + row * (tile_h + gap)
            sheet.paste(image, (x + (thumb_w - image.width) // 2, y))
            label = "\n".join(textwrap.wrap(recipe["label"], width=31))
            draw.multiline_text((x, y + thumb_h + 8), label, fill="white", font=font, spacing=3)
            cards.append(
                f'<article><img src="selected-frames/{html.escape(key)}.png" alt="{html.escape(recipe["label"])}">'
                f'<h2>{html.escape(recipe["label"])}</h2>'
                f'<p><code>{html.escape(key)}</code></p></article>'
            )

    sheet.save(ROOT / "contact-sheet.jpg", quality=92)
    page = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Everyday task frame review</title><style>
body{font:16px system-ui,sans-serif;background:#202124;color:#eee;margin:24px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}
article{background:#303134;padding:12px;border-radius:8px}img{width:100%;height:auto;display:block}
h2{font-size:1rem;margin:.7em 0 .2em}code{color:#bbb}</style>
<h1>Everyday task frames</h1><p>Eight still candidates. No animation was generated in this pass.</p>
<main>""" + "\n".join(cards) + "</main>"
    (ROOT / "review.html").write_text(page)
    print(ROOT / "contact-sheet.jpg")
    print(ROOT / "review.html")
    print(zip_path)


if __name__ == "__main__":
    main()

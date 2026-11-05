#!/usr/bin/env python3
"""Build examples/gallery.png, examples/presets.png and examples/banner.png.

gallery.png  contact sheet of the end-to-end outputs in examples/
presets.png  every built-in preset rendering its reference technique (rendered locally, no model)
banner.png   2:1 catalog banner
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from procgen_studio.render import Program, run_program  # noqa: E402
from procgen_studio.style import load_preset  # noqa: E402

EX = ROOT / "examples"
TECH = ROOT / "procgen_studio" / "techniques"
BG = (14, 13, 20)
FG = (205, 203, 222)
MUTED = (128, 124, 156)


def font(size: int, bold: bool = False):
    names = ["Inter-SemiBold.ttf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf"] if bold else \
        ["Inter-Regular.ttf", "Inter.ttf", "DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def fit(im: Image.Image, w: int, h: int) -> Image.Image:
    im = im.convert("RGB")
    scale = min(w / im.width, h / im.height)
    return im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)


def sheet(items, cols: int, cell: tuple, out: Path, title: str = "") -> None:
    cw, ch = cell
    label_h = 30
    top = 56 if title else 0
    rows = (len(items) + cols - 1) // cols
    img = Image.new("RGB", (cols * cw + (cols + 1) * 12, top + rows * (ch + label_h) + (rows + 1) * 12), BG)
    d = ImageDraw.Draw(img)
    if title:
        d.text((14, 16), title, font=font(24, True), fill=FG)
    for i, (path, label) in enumerate(items):
        x = 12 + (i % cols) * (cw + 12)
        y = top + 12 + (i // cols) * (ch + label_h + 12)
        with Image.open(path) as im:
            t = fit(im, cw, ch)
        img.paste(t, (x + (cw - t.width) // 2, y + (ch - t.height) // 2))
        d.text((x, y + ch + 6), label, font=font(15), fill=MUTED)
    img.save(out, "PNG", optimize=True)


def gallery() -> None:
    items = [
        ("01-icons-ember.png", "1. Icon set (STYLE.md palette: ember)"),
        ("02-icons-frost.png", "2. Same prompt, one STYLE.md line changed (frost)"),
        ("03-scene.png", "3. Flat-illustration scene"),
        ("04-logo.png", "4. Logo + wordmark"),
        ("05-monster.png", "5. Low-poly dark-fantasy monster"),
        ("06-scene-reroll-seed7.png", "6. Scene re-rolled locally, seed 7 (no model call)"),
    ]
    present = [(EX / f, label) for f, label in items if (EX / f).is_file()]
    if present:
        sheet(present, 3, (400, 400), EX / "gallery.png")


def presets() -> None:
    pairs = [("ember", "icon_tiles.py"), ("frost", "icon_tiles.py"), ("flat-illustration", "layered_scene.py"),
             ("dark-fantasy-lowpoly", "lowpoly_creature.py"), ("brand-minimal", "brand_mark.py"),
             ("pixel-arcade", "pixel_sprite.pixel.json")]
    work = EX / ".preset-renders"
    items = []
    for preset, tech in pairs:
        style = load_preset(preset)
        kind = "pixel" if tech.endswith(".json") else "python"
        res = run_program(Program(kind, (TECH / tech).read_text()), width=768, height=768, seed=7,
                          palette=style.palette, style=style.rules, out_dir=work / preset)
        items.append((res.frames[0], preset))
    sheet(items, 6, (260, 260), EX / "presets.png")
    for p in work.rglob("*.png"):
        p.unlink()
    for d in sorted(work.glob("*"), reverse=True):
        d.rmdir()
    work.rmdir()


def banner() -> None:
    files = ["01-icons-ember.png", "02-icons-frost.png", "03-scene.png", "04-logo.png", "05-monster.png"]
    paths = [EX / f for f in files if (EX / f).is_file()]
    if len(paths) < 5:
        return
    W, H = 1200, 600
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 52), "Procgen Studio", font=font(54, True), fill=(244, 243, 255))
    d.text((50, 124), "Images your model draws as code, rendered locally.", font=font(24), fill=FG)
    d.text((50, 160), "No image API key  ·  editable source  ·  STYLE.md  ·  reproducible seeds", font=font(18),
           fill=MUTED)
    tile = 216
    xs = [48 + i * (tile + 9) for i in range(5)]
    for x, p in zip(xs, paths):
        with Image.open(p) as im:
            im = im.convert("RGB")
            side = min(im.size)
            im = im.crop(((im.width - side) // 2, (im.height - side) // 2, (im.width + side) // 2,
                          (im.height + side) // 2)).resize((tile, tile), Image.LANCZOS)
        img.paste(im, (x, 236))
    d.text((50, 486), "Hermes Agent image + video backend", font=font(16), fill=MUTED)
    img.save(EX / "banner.png", "PNG", optimize=True)


if __name__ == "__main__":
    gallery()
    presets()
    banner()
    print("wrote", ", ".join(p.name for p in (EX / "gallery.png", EX / "presets.png", EX / "banner.png") if p.exists()))

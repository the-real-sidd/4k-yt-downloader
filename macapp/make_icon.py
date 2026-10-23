"""Draws the YT Downloader app icon (red rounded square, white download arrow) as AppIcon.icns.

Run with any Python that has Pillow, e.g.:
  ~/.cache/onetake/venv/bin/python tools/macapp/make_icon.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SCALE = 2  # draw at 2048 and shrink, for smooth edges
SIZE = 1024 * SCALE


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # macOS icon grid: an 824 px rounded square centred on a 1024 canvas.
    inset, radius = 100 * SCALE, 185 * SCALE
    box = (inset, inset, SIZE - inset, SIZE - inset)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (box[0], box[1] + 12 * SCALE, box[2], box[3] + 12 * SCALE), radius, fill=(0, 0, 0, 90))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * SCALE)))

    top, bottom = (241, 86, 80), (206, 46, 43)
    gradient = Image.new("RGBA", canvas.size)
    pixels = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = min(max((y - box[1]) / (box[3] - box[1]), 0), 1)
        pixels.line([(0, y), (SIZE, y)], fill=lerp(top, bottom, t) + (255,))
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    canvas.paste(gradient, (0, 0), mask)

    # Download arrow on a 24-unit grid, like the page favicon: M12 4v11 m-5-5 5 5 5-5 M5 20h14
    unit = (box[2] - box[0]) / 24 * 0.78
    ox = SIZE / 2 - 12 * unit
    oy = SIZE / 2 - 12 * unit
    point = lambda x, y: (ox + x * unit, oy + y * unit)
    width = round(2.5 * unit)
    draw = ImageDraw.Draw(canvas)
    strokes = [
        [point(12, 4), point(12, 15)],
        [point(7, 10), point(12, 15), point(17, 10)],
        [point(5, 20), point(19, 20)],
    ]
    for stroke in strokes:
        draw.line(stroke, fill="white", width=width, joint="curve")
        for x, y in stroke:
            r = width / 2
            draw.ellipse((x - r, y - r, x + r, y + r), fill="white")

    return canvas.resize((1024, 1024), Image.LANCZOS)


def main() -> None:
    icon = draw_icon()
    iconset = Path(tempfile.mkdtemp()) / "AppIcon.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        icon.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        icon.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    out = HERE / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    icon.save(HERE / "AppIcon.png")
    shutil.rmtree(iconset.parent)
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())

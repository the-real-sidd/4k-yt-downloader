"""Makes the Windows icon (AppIcon.ico) from the Mac one (macapp/AppIcon.png).

The Mac icon has a margin around the rounded square, as macOS expects; Windows icons fill the
square, so this crops the margin first. Run with any Python that has Pillow, e.g.:
  ~/.cache/onetake/venv/bin/python tools/winapp/make_ico.py
"""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

icon = Image.open(HERE.parent / "macapp" / "AppIcon.png").convert("RGBA")
# The rounded square spans 100..924 of the 1024 canvas (see macapp/make_icon.py); keep a hair of
# its shadow so small sizes don't look clipped.
square = icon.crop((92, 92, 932, 932))
square.save(HERE / "AppIcon.ico", sizes=[(size, size) for size in SIZES])
print("wrote", HERE / "AppIcon.ico")

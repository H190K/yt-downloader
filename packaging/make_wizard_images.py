"""Regenerate the Inno Setup wizard bitmaps from assets/icon.ico.

Usage (from repo root):  python packaging/make_wizard_images.py
Outputs packaging/assets/wizard-large.bmp (welcome/finish side panel) and
packaging/assets/wizard-small.bmp (top-right header logo).
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "assets"

# Matches the app's dark sidebar / accent (ui/theme.py).
BG_TOP = (24, 24, 36)
BG_BOTTOM = (46, 38, 92)
WHITE = (255, 255, 255)


def load_icon() -> Image.Image:
    ico = Image.open(ROOT / "assets" / "icon.ico")
    sizes = sorted(ico.info.get("sizes", {ico.size}), key=lambda s: s[0] * s[1])
    ico.size = sizes[-1]
    return ico.convert("RGBA")


def gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return img


def paste_center(bg: Image.Image, icon: Image.Image, size: int, cy: int) -> None:
    ic = icon.resize((size, size), Image.LANCZOS)
    bg.paste(ic, ((bg.width - size) // 2, cy - size // 2), ic)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = load_icon()

    # Large: 2x of Inno's 164x314 so it stays crisp on high-DPI displays.
    large = gradient(328, 628)
    paste_center(large, icon, 176, 250)
    large.save(ASSETS / "wizard-large.bmp")

    # Small: 2x of 55x55, white background to blend with the modern wizard header.
    small = Image.new("RGB", (110, 110), WHITE)
    paste_center(small, icon, 96, 55)
    small.save(ASSETS / "wizard-small.bmp")
    print("wrote", ASSETS / "wizard-large.bmp", ASSETS / "wizard-small.bmp")


if __name__ == "__main__":
    main()

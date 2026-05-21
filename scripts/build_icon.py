#!/usr/bin/env python3
"""Render Charlie's SVG into a macOS .icns icon.

Input:  resources/icon-src/charlie.svg
Output: resources/icon.icns (+ resources/icon.png for the README + dev)

Background: dark CharLUFS panel color (#1C1D20) on a rounded square,
matching the design's window chrome. Charlie sits centered, with a small
warm accent glow behind him.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "resources" / "icon-src" / "charlie.svg"
PNG_OUT = ROOT / "resources" / "icon.png"
ICNS_OUT = ROOT / "resources" / "icon.icns"

BG = (28, 29, 32, 255)         # #1C1D20
GLOW = (201, 122, 63, 110)     # #C97A3F with soft alpha
BG_TOP = (42, 44, 50, 255)     # gentle vertical gradient peak
CANVAS = 1024
CORNER = 224                    # macOS Big Sur+ icon corner radius


def _gradient_bg(size: int) -> Image.Image:
    """Vertical dark gradient on a rounded square."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, size), 0)
    for y in range(size):
        # ease toward BG over the top third
        t = (y / size) ** 1.4
        r = int(BG_TOP[0] * (1 - t) + BG[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG[2] * t)
        grad.putpixel((0, y), (r, g, b))
    grad = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, size, size), radius=CORNER, fill=255)

    img.paste(grad.convert("RGBA"), (0, 0), mask)
    return img


def _glow(size: int) -> Image.Image:
    """Soft accent glow behind Charlie."""
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    r = int(size * 0.32)
    cx = size // 2
    cy = int(size * 0.55)
    gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GLOW)
    return glow.filter(ImageFilter.GaussianBlur(radius=size // 12))


def render_png(size: int = CANVAS) -> Image.Image:
    bg = _gradient_bg(size)
    glow = _glow(size)

    # Charlie at ~70% of canvas, centered slightly above middle
    charlie_size = int(size * 0.70)
    charlie_png = cairosvg.svg2png(
        url=str(SRC),
        output_width=charlie_size,
        output_height=int(charlie_size * (240 / 220)),
    )
    charlie = Image.open(io.BytesIO(charlie_png)).convert("RGBA")

    # paste glow first
    bg = Image.alpha_composite(bg, glow)

    # paste charlie, centered horizontally, slightly above center vertically
    cx = (size - charlie.width) // 2
    cy = int(size * 0.16)
    bg.paste(charlie, (cx, cy), charlie)
    return bg


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}", file=sys.stderr)
        return 1

    img = render_png(CANVAS)
    PNG_OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(PNG_OUT, "PNG")
    print(f"wrote {PNG_OUT}")

    # Pillow can save .icns directly from a stack of sizes.
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    img.save(ICNS_OUT, format="ICNS", sizes=[(s, s) for s in sizes])
    print(f"wrote {ICNS_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Rasterise PipeData's favicon SVG into the PNG/ICO variants plus the OG card.

Run after editing static/favicon.svg:  python make_assets.py
Needs cairosvg and Pillow (see requirements.txt).
"""

import io
import os

import cairosvg
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
SVG = os.path.join(STATIC, "favicon.svg")

STEEL = (43, 76, 126)
ORANGE = (211, 84, 0)
OFFWHITE = (247, 248, 250)
GRAYBLUE = (154, 172, 198)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render(size):
    png = cairosvg.svg2png(url=SVG, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def centered(draw, text, fnt, cx, y, fill):
    left, top, right, _ = draw.textbbox((0, 0), text, font=fnt)
    draw.text((cx - (right - left) / 2 - left, y), text, font=fnt, fill=fill)


def main():
    # apple-touch-icon: flattened onto the brand steel blue, since iOS drops alpha
    icon180 = render(180)
    flat = Image.new("RGB", (180, 180), STEEL)
    flat.paste(icon180, (0, 0), icon180)
    flat.save(os.path.join(STATIC, "apple-touch-icon.png"))

    render(192).save(os.path.join(STATIC, "favicon-192.png"))
    render(512).save(os.path.join(STATIC, "favicon-512.png"))
    render(32).save(os.path.join(STATIC, "favicon-32x32.png"))
    render(16).save(os.path.join(STATIC, "favicon-16x16.png"))

    ico = render(64)
    ico.save(os.path.join(STATIC, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])

    # ---- Open Graph card ----
    W, H = 1200, 630
    og = Image.new("RGB", (W, H), STEEL)
    d = ImageDraw.Draw(og)
    d.rectangle([0, 0, W, 10], fill=ORANGE)
    d.rectangle([0, H - 10, W, H], fill=ORANGE)

    logo = render(150)
    og.paste(logo, ((W - 150) // 2, 108), logo)

    centered(d, "PipeData.org", font(84), W / 2, 296, OFFWHITE)
    centered(d, "Pipe, Flange & Fitting Specifications", font(38), W / 2, 404, ORANGE)
    centered(d, "ASME B36.10  ·  B16.5  ·  B16.47  ·  B16.9",
             font(27), W / 2, 482, GRAYBLUE)

    og.save(os.path.join(STATIC, "og-default.png"), optimize=True)
    print("assets written to", STATIC)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the raster brand assets for VIPORDER.COM.VN.

Why a script instead of committed binaries alone: the assets can be regenerated
and reviewed as code, so a colour or wording change is a diff rather than an
opaque new blob.

Requirements: Pillow (development only — not a runtime dependency).

    pip install Pillow
    python tools/make_brand_images.py

Outputs (all deterministic):

    static/img/og-viporder.png        1200x630  Open Graph / Twitter card
    static/img/apple-touch-icon.png    180x180  iOS home screen
    static/img/favicon-32.png           32x32
    static/img/favicon-16.png           16x16

Only facts that are already true of the website appear in these images. No
phone number, address, customer count, award or certification is drawn — none
of those are verified, and an image is the easiest place for a fabricated claim
to hide.

Font: Arial Bold, falling back to any system font that covers Vietnamese
diacritics. The script fails loudly rather than silently drawing tofu boxes.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - developer convenience
    print("Pillow is required:  pip install Pillow", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "img"

# --- Brand tokens (mirrors static/css/style.css :root) ----------------------
RED = (215, 25, 32)
RED_DARK = (169, 15, 21)
DARK = (9, 13, 20)
DARK_2 = (17, 24, 39)
WHITE = (255, 255, 255)
MUTED = (152, 162, 179)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# Vietnamese text — the diacritics are the point of the font check.
WORDMARK = "VIPORDER"
TAGLINE = "Vận chuyển · Nhập khẩu · Hải quan"
DOMAIN = "viporder.com.vn"


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            font = ImageFont.truetype(path, size)
            if font_has_glyphs(font):
                return font
    raise SystemExit(
        "No usable font found. Install one of:\n  " + "\n  ".join(FONT_CANDIDATES)
    )


def font_has_glyphs(font: ImageFont.FreeTypeFont) -> bool:
    """Reject a font that cannot render Vietnamese, instead of drawing tofu."""
    probe = "Vận chuyển ậễộớụỹđĐ"
    try:
        mask = font.getmask(probe)
    except (OSError, ValueError):
        # Pillow raises OSError/ValueError for a font it cannot render with;
        # rejecting that font is the whole purpose of this probe.
        return False
    return mask.size[0] > 0


def vertical_gradient(width: int, height: int, top, bottom) -> Image.Image:
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(height - 1, 1)
        colour = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=colour)
    return img


def text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def make_og() -> Path:
    W, H = 1200, 630
    img = vertical_gradient(W, H, DARK, DARK_2)
    draw = ImageDraw.Draw(img)

    # Red accent block holding the VIP mark.
    pad = 88
    box = 190
    draw.rounded_rectangle([pad, pad, pad + box, pad + box], radius=38, fill=RED)
    mark_font = load_font(78)
    tw, th = text_size(draw, "VIP", mark_font)
    draw.text(
        (pad + (box - tw) / 2, pad + (box - th) / 2 - 10),
        "VIP",
        font=mark_font,
        fill=WHITE,
    )

    # Wordmark.
    word_font = load_font(104)
    draw.text((pad + box + 44, pad + 34), WORDMARK, font=word_font, fill=WHITE)

    # Tagline.
    tag_font = load_font(44)
    draw.text((pad, pad + box + 54), TAGLINE, font=tag_font, fill=MUTED)

    # Domain, bottom right, with a red rule above it.
    dom_font = load_font(40)
    dw, dh = text_size(draw, DOMAIN, dom_font)
    draw.line(
        [(W - pad - dw, H - pad - dh - 34), (W - pad, H - pad - dh - 34)],
        fill=RED,
        width=6,
    )
    draw.text((W - pad - dw, H - pad - dh), DOMAIN, font=dom_font, fill=WHITE)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "og-viporder.png"
    img.save(path, "PNG", optimize=True)
    return path


def make_icon(size: int, name: str, text: str, font_ratio: float) -> Path:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(round(size * 0.22), 2)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=RED)
    # Subtle depth without relying on a gradient at tiny sizes.
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=radius,
        outline=RED_DARK,
        width=max(size // 32, 1),
    )
    font = load_font(max(round(size * font_ratio), 6))
    tw, th = text_size(draw, text, font)
    draw.text(
        ((size - tw) / 2, (size - th) / 2 - size * 0.06), text, font=font, fill=WHITE
    )
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(path, "PNG", optimize=True)
    return path


def main() -> int:
    written = [
        make_og(),
        make_icon(180, "apple-touch-icon.png", "VIP", 0.34),
        make_icon(32, "favicon-32.png", "V", 0.62),
        make_icon(16, "favicon-16.png", "V", 0.62),
    ]
    for path in written:
        size = path.stat().st_size
        with Image.open(path) as im:
            print(
                f"  {path.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}  {size:,} bytes"
            )
    print(f"\nwrote {len(written)} asset(s) to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

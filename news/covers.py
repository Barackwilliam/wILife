"""
Generated cover images for articles.

    render(title, category, date_text, seed)  -> 1200x630 PNG  (Open Graph, Google Discover, article header)
    render(..., with_title=False)             -> the same art without the headline (cards show the title in text)
    thumbnail(png_bytes)                      -> 1200x630 WebP of the text-free art (site images — ~30x lighter)

Generated rather than taken from the source, so there is no image-copyright
question. Each cover varies its composition by `seed` so a page of cards does
not look like one image repeated.
"""

import io
import os
import random
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
THUMB = (1200, 630)

PALETTE = {
    "dunia": [((61, 123, 217), (123, 92, 214)), ((31, 155, 176), (61, 123, 217))],
    "afrika": [((233, 160, 32), (226, 96, 63)), ((226, 96, 63), (184, 71, 42))],
    "tanzania": [((47, 158, 122), (31, 155, 176)), ((47, 158, 122), (233, 160, 32))],
    "jamiitek": [((226, 96, 63), (217, 70, 107)), ((123, 92, 214), (226, 96, 63))],
}
LABELS = {"dunia": "DUNIA", "afrika": "AFRIKA", "tanzania": "TANZANIA", "jamiitek": "TEKNOLOJIA & BIASHARA"}
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def _font(bold, size):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        path = os.path.join(d, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render(title, category, date_text="", seed=0, with_title=True):
    rnd = random.Random(seed or hash(title))
    ink = (23, 21, 29)
    a, b = rnd.choice(PALETTE.get(category, PALETTE["tanzania"]))
    img = Image.new("RGB", (W, H), ink)

    glow = Image.new("RGB", (W, H), ink)
    gd = ImageDraw.Draw(glow)
    for color, (cx, cy, r) in ((a, (rnd.randint(700, 1150), rnd.randint(-120, 160), rnd.randint(330, 430))),
                               (b, (rnd.randint(-60, 360), rnd.randint(420, 700), rnd.randint(260, 360)))):
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    img = Image.blend(img, glow, 0.62)
    d = ImageDraw.Draw(img, "RGBA")

    # Subtle pattern: diagonal hairlines or dot grid
    if rnd.random() < 0.5:
        for x in range(-H, W, 28):
            d.line([(x, H), (x + H, 0)], fill=(255, 255, 255, 10), width=1)
    else:
        for x in range(20, W, 32):
            for y in range(20, H, 32):
                d.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, 22))

    # Big category word, on its own layer so it really is faint
    label = LABELS.get(category, category.upper())
    word = label.split()[0]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    big = _font(True, 230 if not with_title else 190)
    ld.text((W - ld.textlength(word, font=big) - 36, H - (290 if not with_title else 240)), word, font=big,
            fill=(255, 255, 255, 34 if not with_title else 16))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    if not with_title:
        d.rectangle((0, H - 10, W, H), fill=a)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    # Brand
    d.rounded_rectangle((70, 60, 116, 106), radius=13, fill=(226, 96, 63))
    d.line([(80, 74), (88, 96), (93, 85), (98, 96), (106, 74)], fill="white", width=5, joint="curve")
    d.text((130, 64), "wILife", font=_font(True, 30), fill=(243, 239, 232))
    if date_text:
        d.text((130, 98), date_text, font=_font(False, 17), fill=(185, 180, 192))

    # Category pill
    lf = _font(True, 19)
    tw = d.textlength(label, font=lf)
    d.rounded_rectangle((70, 160, 70 + tw + 36, 198), radius=19, fill=a)
    d.text((88, 168), label, font=lf, fill="white")

    # Title: largest size that fits in 4 lines
    for size in (68, 60, 54, 48, 42, 38):
        tf = _font(True, size)
        lines = _wrap(d, title, tf, W - 150)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    y = 226
    for line in lines:
        d.text((70, y), line, font=tf, fill=(255, 255, 255))
        y += int(size * 1.17)

    d.rectangle((0, H - 10, W, H), fill=a)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def thumbnail(png_bytes, quality=72):
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.size != THUMB:
        img = img.resize(THUMB, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()

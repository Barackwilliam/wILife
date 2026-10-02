"""
Generate a 1200x630 cover image (PNG) for each article.

Used for Open Graph / Twitter cards and the article header. Generated, not
taken from the source, so there is no image-copyright question.
"""

import io
import os
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630

PALETTE = {
    "dunia": ((61, 123, 217), (123, 92, 214)),
    "afrika": ((233, 160, 32), (226, 96, 63)),
    "tanzania": ((47, 158, 122), (31, 155, 176)),
    "jamiitek": ((226, 96, 63), (217, 70, 107)),
}
LABELS = {"dunia": "DUNIA", "afrika": "AFRIKA", "tanzania": "TANZANIA", "jamiitek": "JAMIITEK"}

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


def render(title, category, date_text=""):
    ink = (23, 21, 29)
    a, b = PALETTE.get(category, PALETTE["tanzania"])
    img = Image.new("RGB", (W, H), ink)

    glow = Image.new("RGB", (W, H), ink)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W - 560, -260, W + 200, 420), fill=a)
    gd.ellipse((-260, H - 300, 380, H + 260), fill=b)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.blend(img, glow, 0.55)
    d = ImageDraw.Draw(img)

    # Brand
    d.rounded_rectangle((70, 64, 118, 112), radius=14, fill=(226, 96, 63))
    d.line([(80, 79), (88, 101), (94, 89), (100, 101), (108, 79)], fill="white", width=5, joint="curve")
    d.text((134, 66), "wILife Habari", font=_font(True, 30), fill=(243, 239, 232))
    d.text((134, 100), date_text, font=_font(False, 18), fill=(169, 163, 177))

    # Category pill
    label = LABELS.get(category, category.upper())
    lf = _font(True, 20)
    tw = d.textlength(label, font=lf)
    d.rounded_rectangle((70, 170, 70 + tw + 40, 210), radius=20, fill=a)
    d.text((90, 178), label, font=lf, fill="white")

    # Title, shrinking until it fits in 4 lines
    for size in (66, 58, 52, 46, 40):
        tf = _font(True, size)
        chars = max(10, int(1040 / (size * 0.63)))
        lines = textwrap.wrap(title, width=chars)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    y = 240
    for line in lines:
        d.text((70, y), line, font=tf, fill=(255, 255, 255))
        y += int(size * 1.18)

    d.rectangle((0, H - 10, W, H), fill=a)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

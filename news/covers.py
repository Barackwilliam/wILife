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


MARK_PATH = os.path.join(os.path.dirname(__file__), "static", "news", "brand", "icon-192.png")


def _mark(size):
    """The logo icon (rounded tile) at `size` px, or None if the file is missing."""
    try:
        return Image.open(MARK_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
    except OSError:
        return None


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

    # Brand: the wILife logo mark, so shared links carry the logo
    mark = _mark(52)
    if mark:
        img.paste(mark, (70, 58), mark)
        d = ImageDraw.Draw(img, "RGBA")
    else:
        d.rounded_rectangle((70, 60, 116, 106), radius=13, fill=(37, 99, 235))
    d.text((134, 64), "wILife", font=_font(True, 30), fill=(243, 239, 232))
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


# ── Mechi: picha yenye nembo halisi za timu (news/football.py) ────────────────────────────

PITCH = ((11, 20, 48), (20, 36, 86))
LIVE_RED = (225, 29, 46)


def _logo(data, size):
    """Nembo ya timu kutoka bytes (PNG ya API), ikiwa na nafasi sawa pande zote; None ikishindikana."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    img.thumbnail((size, size), Image.LANCZOS)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return tile


def _initials_badge(name, size):
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.ellipse((0, 0, size - 1, size - 1), fill=(255, 255, 255, 235))
    letters = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    f = _font(True, int(size * .36))
    d.text(((size - d.textlength(letters, font=f)) / 2, size * .3), letters, font=f, fill=PITCH[0])
    return tile


def _fit(d, text, bold, start, max_width, minimum=22):
    size = start
    while size > minimum and d.textlength(text, font=_font(bold, size)) > max_width:
        size -= 2
    return _font(bold, size)


def render_match(home, away, home_logo=None, away_logo=None, competition="", comp_logo=None,
                 when_text="", score=None, live=False, with_text=True):
    """
    1200x630: uwanja wa giza, nembo za timu mbili, majina, mashindano na saa / matokeo.
    with_text=False -> toleo la kadi (nembo na VS/matokeo tu; kichwa kiko kwenye maandishi ya kadi).
    """
    img = Image.new("RGB", (W, H), PITCH[0])
    glow = Image.new("RGB", (W, H), PITCH[0])
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W / 2 - 520, -380, W / 2 + 520, 420), fill=PITCH[1])
    gd.ellipse((W - 260, -200, W + 160, 220), fill=(90, 20, 48) if live else PITCH[1])
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(120)), .85)
    d = ImageDraw.Draw(img, "RGBA")

    # Mistari ya uwanja: mstari wa kati na duara
    d.line([(W / 2, 0), (W / 2, H)], fill=(255, 255, 255, 18), width=2)
    d.ellipse((W / 2 - 120, H / 2 - 120, W / 2 + 120, H / 2 + 120), outline=(255, 255, 255, 18), width=2)
    d.rectangle((0, 0, 150, H), outline=(255, 255, 255, 10), width=2)
    d.rectangle((W - 150, 0, W, H), outline=(255, 255, 255, 10), width=2)

    crest = 210 if with_text else 250
    cy = 150 if with_text else (H - crest) // 2 - 10
    for name, data, cx in ((home, home_logo, W * .25), (away, away_logo, W * .75)):
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(halo).ellipse((cx - crest * .72, cy + crest / 2 - crest * .72,
                                      cx + crest * .72, cy + crest / 2 + crest * .72), fill=(255, 255, 255, 30))
        img = Image.alpha_composite(img.convert("RGBA"), halo.filter(ImageFilter.GaussianBlur(30)))
        tile = _logo(data, crest) or _initials_badge(name, crest)
        img.paste(tile, (int(cx - crest / 2), int(cy)), tile)
        d = ImageDraw.Draw(img, "RGBA")
        if with_text:
            f = _fit(d, name, True, 44, W * .42)
            d.text((cx - d.textlength(name, font=f) / 2, cy + crest + 26), name, font=f, fill=(255, 255, 255))

    img = img.convert("RGB")                 # rangi zenye uwazi zichanganyike, si kufuta pikseli
    d = ImageDraw.Draw(img, "RGBA")

    # Katikati: matokeo au VS
    mid = score if score else "VS"
    mf = _font(True, 112 if score else 72)
    my = (cy + crest / 2) - (64 if score else 44)
    d.text((W / 2 - d.textlength(mid, font=mf) / 2, my), mid, font=mf,
           fill=(255, 255, 255) if score else (255, 255, 255, 150))

    if not with_text:
        d.rectangle((0, H - 10, W, H), fill=LIVE_RED if live else (226, 96, 63))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        return buf.getvalue()

    # Juu: chapa ya wILife kushoto, LIVE / MATOKEO kulia
    mark = _mark(46)
    if mark:
        img.paste(mark, (56, 40), mark)
        img = img.convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
    d.text((114, 46), "wILife", font=_font(True, 28), fill=(243, 239, 232))
    tag = "LIVE" if live else ("MATOKEO" if score else "MECHI")
    tf = _font(True, 24)
    tw = d.textlength(tag, font=tf)
    x0 = W - 56 - tw - (58 if live else 36)
    d.rounded_rectangle((x0, 40, W - 56, 86), radius=10, fill=LIVE_RED if live else (255, 255, 255, 40))
    if live:
        d.ellipse((x0 + 18, 56, x0 + 32, 70), fill=(255, 255, 255))
    d.text((x0 + (44 if live else 18), 50), tag, font=tf, fill=(255, 255, 255))

    # Chini: mashindano (na nembo yake) na saa
    line = competition.upper()
    cf = _fit(d, line, True, 26, W - 260)
    cw = d.textlength(line, font=cf)
    clogo = _logo(comp_logo, 40)
    total = cw + (52 if clogo else 0)
    x = W / 2 - total / 2
    if clogo:
        plate = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        ImageDraw.Draw(plate).rounded_rectangle((0, 0, 47, 47), radius=8, fill=(255, 255, 255, 240))
        plate.paste(clogo, (4, 4), clogo)
        img.paste(plate, (int(x), H - 124), plate)
        img = img.convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
        x += 60
    d.text((x, H - 118), line, font=cf, fill=(255, 255, 255, 220))
    if when_text:
        wf = _font(False, 22)
        d.text((W / 2 - d.textlength(when_text, font=wf) / 2, H - 70), when_text, font=wf, fill=(255, 255, 255, 160))
    d.rectangle((0, H - 10, W, H), fill=LIVE_RED if live else (226, 96, 63))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()

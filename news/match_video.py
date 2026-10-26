"""
Video fupi ASILI ya matokeo ya mechi (wima 720x1280, sekunde ~12, bila sauti) kwa WhatsApp Channel ya Soka360.

    render(match)  -> bytes za MP4 (H.264), au MatchVideoError

Imetengenezwa kutoka data yetu wenyewe (majina, nembo za API, matokeo, wafungaji) kwa michoro ya
covers.py — si kipande cha video ya mtu mwingine, kwa hiyo hakuna swali la hati miliki.
Inahitaji `ffmpeg` (Dockerfile inaisakinisha).

Sehemu: 1) mashindano + "MATOKEO" 2) nembo, majina na matokeo 3) wafungaji (kama wapo) 4) Soka360.
"""
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from news import covers

W, H = 720, 1280
FPS = 20
FADE = 0.5
ACCENT = (226, 96, 63)
SOKA_LOGO = os.path.join(os.path.dirname(__file__), "static", "news", "brand", "soka360-96.png")


class MatchVideoError(RuntimeError):
    pass


def _base():
    img = Image.new("RGB", (W, H), covers.PITCH[0])
    glow = Image.new("RGB", (W, H), covers.PITCH[0])
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-200, -260, W + 200, 620), fill=covers.PITCH[1])
    gd.ellipse((-160, H - 420, W + 160, H + 300), fill=covers.PITCH[1])
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(110)), .85)
    d = ImageDraw.Draw(img, "RGBA")
    d.line([(0, H / 2), (W, H / 2)], fill=(255, 255, 255, 18), width=2)
    d.ellipse((W / 2 - 150, H / 2 - 150, W / 2 + 150, H / 2 + 150), outline=(255, 255, 255, 18), width=2)
    d.rectangle((0, H - 14, W, H), fill=ACCENT)
    return img


def _brand(img, y=60):
    """Nembo ya Soka360 + jina juu katikati."""
    d = ImageDraw.Draw(img, "RGBA")
    f = covers._font(True, 34)
    text = "Soka360"
    try:
        logo = Image.open(SOKA_LOGO).convert("RGBA").resize((64, 64), Image.LANCZOS)
    except OSError:
        logo = None
    total = d.textlength(text, font=f) + (78 if logo else 0)
    x = (W - total) / 2
    if logo:
        img.paste(logo, (int(x), y), logo)
        x += 78
    d.text((x, y + 12), text, font=f, fill=(243, 239, 232))
    return img


def _center(d, text, y, font, fill=(255, 255, 255)):
    d.text(((W - d.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _lines(d, text, font, width, limit):
    out = covers._wrap(d, text, font, width)
    return out[:limit]


def _crest(data, name, size):
    return covers._logo(data, size) or covers._initials_badge(name, size)


def _intro(m, comp_logo):
    img = _brand(_base())
    d = ImageDraw.Draw(img, "RGBA")
    clogo = covers._logo(comp_logo, 150)
    y = 380
    if clogo:
        plate = Image.new("RGBA", (190, 190), (0, 0, 0, 0))
        ImageDraw.Draw(plate).rounded_rectangle((0, 0, 189, 189), radius=28, fill=(255, 255, 255, 240))
        plate.paste(clogo, (20, 20), clogo)
        img.paste(plate, ((W - 190) // 2, y), plate)
        d = ImageDraw.Draw(img, "RGBA")
        y += 240
    f = covers._font(True, 44)
    for line in _lines(d, (m.competition or "Soka").upper(), f, W - 100, 3):
        _center(d, line, y, f)
        y += 58
    tag = covers._font(True, 40)
    label = "MATOKEO YA MWISHO"
    tw = d.textlength(label, font=tag)
    d.rounded_rectangle(((W - tw) / 2 - 30, y + 40, (W + tw) / 2 + 30, y + 112), radius=16, fill=ACCENT)
    _center(d, label, y + 54, tag)
    return img


def _score(m, home_logo, away_logo):
    img = _brand(_base())
    crest = 200
    for name, data, top, label_y in ((m.home, home_logo, 200, 418), (m.away, away_logo, 840, 770)):
        tile = _crest(data, name, crest)
        img.paste(tile, ((W - crest) // 2, top), tile)
        d = ImageDraw.Draw(img, "RGBA")
        _center(d, name, label_y, covers._fit(d, name, True, 44, W - 80))
    d = ImageDraw.Draw(img, "RGBA")
    _center(d, f"{m.home_goals} - {m.away_goals}", 505, covers._font(True, 150))
    _center(d, "FT", 690, covers._font(True, 30), fill=(255, 255, 255, 170))
    return img


def _scorers(m, goals):
    img = _brand(_base())
    d = ImageDraw.Draw(img, "RGBA")
    _center(d, "MAGOLI", 200, covers._font(True, 56))
    d.rectangle((W / 2 - 50, 280, W / 2 + 50, 286), fill=ACCENT)
    f = covers._font(False, 32)
    y = 340
    for label, text in goals[:6]:
        lines = _lines(d, text, f, W - 230, 3)
        d.rounded_rectangle((50, y - 14, W - 50, y + 44 * len(lines) + 10), radius=18, fill=(255, 255, 255, 22))
        d.text((72, y), label, font=covers._fit(d, label, True, 30, 100), fill=ACCENT)
        for i, line in enumerate(lines):
            d.text((190, y + i * 44), line, font=f, fill=(255, 255, 255))
        y += 44 * len(lines) + 46
        if y > H - 260:
            break
    line = f"{m.home} {m.home_goals}-{m.away_goals} {m.away}"
    _center(d, line, H - 180, covers._fit(d, line, True, 34, W - 80))
    return img


def _outro(site):
    img = _base()
    d = ImageDraw.Draw(img, "RGBA")
    try:
        logo = Image.open(SOKA_LOGO).convert("RGBA").resize((220, 220), Image.LANCZOS)
        img.paste(logo, ((W - 220) // 2, 340), logo)
        d = ImageDraw.Draw(img, "RGBA")
    except OSError:
        pass
    _center(d, "Soka360", 600, covers._font(True, 64))
    f = covers._font(False, 34)
    for i, line in enumerate(("Matokeo na video ya kila mechi", "hapa hapa WhatsApp")):
        _center(d, line, 700 + i * 48, f, fill=(255, 255, 255, 220))
    tag = covers._font(True, 40)
    label = "FOLLOW Soka360"
    tw = d.textlength(label, font=tag)
    d.rounded_rectangle(((W - tw) / 2 - 34, 850, (W + tw) / 2 + 34, 924), radius=37, fill=ACCENT)
    _center(d, label, 864, tag)
    _center(d, "Mshirikishe rafiki yako", 960, covers._font(False, 30),
            fill=(255, 255, 255, 200))
    if site:
        _center(d, site, 1060, covers._font(True, 32), fill=(255, 255, 255, 170))
    return img


def goals_of(m):
    """[(dakika, maelezo)] ya magoli: LiveMatch (masasisho ya LIVE) au Fixture (extra['events'])."""
    raw = []
    article = getattr(m, "article", None)
    if article is not None and hasattr(article, "live_updates"):
        raw = [(u.label, u.text) for u in article.live_updates.filter(kind="goal").order_by("created_at", "id")]
    else:
        raw = [(e.get("label", ""), e.get("text", "")) for e in (m.extra or {}).get("events") or []
               if e.get("kind") == "goal"]
    out = []
    for label, text in raw:
        text = re.sub(r"\s*\([^()]*\d+\s*-\s*\d+[^()]*\)\s*$", "", (text or "").replace("⚽", "")).strip()
        text = re.sub(r"^GOLI!\s*", "", text).strip()
        if text:
            out.append(((label or "").strip()[:6], text))
    return out


def scenes(m, home_logo=None, away_logo=None, comp_logo=None, site=""):
    """[(picha, sekunde)] za video."""
    out = [(_intro(m, comp_logo), 2.5), (_score(m, home_logo, away_logo), 4.0)]
    goals = goals_of(m)
    if goals:
        out.append((_scorers(m, goals), 4.0))
    out.append((_outro(site), 2.5))
    return out


def render(m, home_logo=None, away_logo=None, comp_logo=None, site=""):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MatchVideoError("ffmpeg haipo kwenye server")
    if m.home_goals is None or m.away_goals is None:
        raise MatchVideoError("mechi haina matokeo")
    parts = scenes(m, home_logo, away_logo, comp_logo, site)
    with tempfile.TemporaryDirectory() as tmp:
        args = [ffmpeg, "-y", "-loglevel", "error"]
        for i, (img, secs) in enumerate(parts):
            path = os.path.join(tmp, f"s{i}.png")
            img.convert("RGB").save(path, "PNG")
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{secs}", "-i", path]
        # Mpito wa kufifia kati ya sehemu (xfade); offset = mwisho wa sehemu iliyotangulia ukiondoa FADE
        chain, last, offset = [], "[0:v]", 0.0
        for i in range(1, len(parts)):
            offset += parts[i - 1][1] - FADE
            out = f"[v{i}]"
            chain.append(f"{last}[{i}:v]xfade=transition=fade:duration={FADE}:offset={offset:.2f}{out}")
            last = out
        chain.append(f"{last}format=yuv420p[out]")
        dest = os.path.join(tmp, "out.mp4")
        args += ["-filter_complex", ";".join(chain), "-map", "[out]", "-r", str(FPS), "-c:v", "libx264",
                 "-preset", "ultrafast", "-tune", "stillimage", "-crf", "26", "-profile:v", "main", "-movflags", "+faststart", dest]
        try:
            subprocess.run(args, check=True, capture_output=True, timeout=60)
        except (subprocess.SubprocessError, OSError) as exc:
            err = getattr(exc, "stderr", b"") or b""
            raise MatchVideoError(f"ffmpeg: {err.decode(errors='ignore')[:200] or exc}") from exc
        with open(dest, "rb") as fh:
            return fh.read()

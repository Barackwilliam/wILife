"""
Picha za habari zinazopakiwa na mhariri kupitia admin.

Kila picha iliyopakiwa inachakatwa mara moja (na tick inakagua zilizobaki):

  * mzunguko sahihi (EXIF ya simu), rangi RGB
  * cover_art   — 1200px WebP ya ubora wa juu (simu na kadi)
  * cover_hd    — hadi 2400px WebP (skrini kubwa/retina, kupitia srcset) —
                  inatengenezwa tu kama picha asili ni kubwa vya kutosha
  * cover_image — 1200×630 JPEG ya kushirikisha (WhatsApp, Facebook, Google)
  * cover_original — picha asili haiguswi, ili kuchakata upya kusipoteze ubora

Picha ndogo kuliko 1200px haiwezi kuwa HD halisi (pikseli hazipo) — inakuzwa kwa
uangalifu na kunolewa, na admin inaonyesha onyo la kupakia kubwa zaidi.
"""

import io
import logging
import os

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageFilter, ImageOps

log = logging.getLogger("news")

SITE_W = 1200          # cover_art
HD_W = 2400            # cover_hd (2× kwa retina)
HD_MIN = 1800          # chini ya hapa hakuna toleo la HD
OG = (1200, 630)
MAX_PIXELS = 60_000_000

QUALITY_LABELS = {"hd": "HD", "sd": "Kawaida", "low": "Ndogo", "generated": "Ya mfumo"}


def _generated(article, fieldfile):
    """Picha iliyotengenezwa na covers.py (si iliyopakiwa na mhariri)?"""
    name = os.path.basename(getattr(fieldfile, "name", "") or "")
    if not name:
        return True
    base = f"{(article.slug or 'habari')[:80]}-{article.pk or 0}"
    return name.startswith(base) and name.rsplit(".", 1)[-1].lower() in ("png", "webp")


def uploaded_field(article, prefer=None):
    """Uga wenye picha iliyopakiwa na mhariri (cover_art kwanza), au None."""
    order = [prefer] if prefer else []
    order += [f for f in ("cover_original", "cover_art", "cover_image") if f not in order]
    for name in order:
        f = getattr(article, name)
        if f and (name == "cover_original" or not _generated(article, f)):
            return name
    return None


def _open(data):
    img = Image.open(io.BytesIO(data))
    if img.width * img.height > MAX_PIXELS:
        raise ValueError("picha ni kubwa mno")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    return img.convert("RGB")


def _resize(img, width):
    """Badili upana; kunoa kidogo baada ya kupunguza, zaidi baada ya kukuza."""
    if img.width == width:
        return img
    height = round(img.height * width / img.width)
    out = img.resize((width, height), Image.LANCZOS)
    if width > img.width:   # kukuza: noa zaidi ili isionekane na ukungu
        return out.filter(ImageFilter.UnsharpMask(radius=2, percent=90, threshold=2))
    return out.filter(ImageFilter.UnsharpMask(radius=1, percent=50, threshold=3))


def _og(img):
    """1200×630 — kata katikati (juu kidogo, nyuso huwa juu)."""
    ratio = OG[0] / OG[1]
    w, h = img.size
    if w / h > ratio:
        nw = round(h * ratio)
        box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    else:
        nh = round(w / ratio)
        top = int((h - nh) * 0.35)
        box = (0, top, w, top + nh)
    out = img.crop(box).resize(OG, Image.LANCZOS)
    return out.filter(ImageFilter.UnsharpMask(radius=1, percent=40, threshold=3))


def _encode(img, fmt, quality):
    buf = io.BytesIO()
    if fmt == "WEBP":
        img.save(buf, "WEBP", quality=quality, method=6)
    else:
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def quality_for(width):
    return "hd" if width >= HD_MIN else ("sd" if width >= SITE_W else "low")


def process(article, field=None):
    """
    Chakata picha iliyopakiwa ya `article` (haihifadhi — anayeita anaita save()).
    Inarudisha ubora ('hd' / 'sd' / 'low' / 'generated').
    """
    field = uploaded_field(article, prefer=field)
    now = timezone.now()
    if field is None:
        article.photo_quality, article.photo_checked_at = "generated", now
        return "generated"
    src = getattr(article, field)
    src.open("rb")
    try:
        data = src.read()
    finally:
        src.close()
    img = _open(data)
    base = f"{(article.slug or 'habari')[:70]}-{article.pk or 0}-p"

    if field != "cover_original":
        ext = os.path.splitext(src.name)[1].lower() or ".jpg"
        article.cover_original.save(f"{base}-asili{ext}", ContentFile(data), save=False)

    article.cover_art.save(f"{base}.webp", ContentFile(_encode(_resize(img, SITE_W), "WEBP", 84)), save=False)
    if img.width >= HD_MIN:
        hd = _resize(img, min(HD_W, img.width))
        article.cover_hd.save(f"{base}-hd.webp", ContentFile(_encode(hd, "WEBP", 82)), save=False)
        article.photo_hd_width = hd.width
    else:
        article.cover_hd = None
        article.photo_hd_width = 0
    article.cover_image.save(f"{base}-og.jpg", ContentFile(_encode(_og(img), "JPEG", 88)), save=False)
    article.cover_png = article.cover_thumb = None   # picha za mfumo hazitumiki tena

    article.photo_quality = quality_for(img.width)
    article.photo_size = f"{img.width}×{img.height}"
    article.photo_checked_at = now
    return article.photo_quality


FIELDS = ["cover_original", "cover_art", "cover_hd", "cover_image", "cover_png", "cover_thumb",
          "photo_quality", "photo_size", "photo_hd_width", "photo_checked_at"]


def check_pending(limit=5, dry_run=False):
    """Tick: kagua habari ambazo picha zake hazijakaguliwa bado."""
    from news.models import Article
    pending = Article.objects.filter(photo_checked_at=None).exclude(cover_art="", cover_image="") \
        .order_by("-published_at", "-pk")
    total = pending.count()
    done = failed = 0
    for article in pending[:limit] if not dry_run else []:
        try:
            process(article)
        except Exception as exc:  # picha mbovu isizuie nyingine
            log.warning("photo %s failed: %s", article.pk, exc)
            article.photo_quality, article.photo_checked_at = "error", timezone.now()
            failed += 1
        article.save(update_fields=FIELDS)
        done += 1
    left = total - done
    return {"job": "news_photos", "sent": done - failed, "failed": failed,
            "detail": f"processed {done}, left {left}" if total else "none"}

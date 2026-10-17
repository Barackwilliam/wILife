"""
"Weka habari" — chapisho la Instagram au Facebook -> rasimu ya wILife.

Mhariri analeta chapisho mwenyewe (dashboard au picha kwa Telegram):
picha + maelezo ya chapisho (caption) + link + akaunti/ukurasa. Mfumo:

  1. unaunda rasimu (haichapishwi) yenye picha iliyochakatwa HD (news/photos.py)
  2. AI inapanga maelezo kuwa habari safi ya Kiswahili — bila kubuni bei,
     tarehe wala mawasiliano; AI ikishindwa, maelezo asili yanatumika
  3. chanzo (akaunti + link ya chapisho) kinahifadhiwa kwenye makala

Mhariri anahariri kwenye admin kisha anachapisha (au "OK <code>" kwa Telegram).
"""

import logging
import re

from django.core.files.base import ContentFile

from news import photos, writer
from news.models import Article, Category, EditorSettings

log = logging.getLogger("news")

PLATFORMS = {"instagram": "Instagram", "facebook": "Facebook"}
POST_LINK = re.compile(
    r"https?://(?:(?:www|m|web|business)\.)?(?:instagram\.com|instagr\.am|facebook\.com|fb\.com|fb\.watch)/[^\s]+",
    re.IGNORECASE)
HANDLE = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})")
MAX_IMAGE = 20 * 1024 * 1024


def platform_of(link, default="instagram"):
    host = (link or "").lower()
    if "facebook.com" in host or "fb.com" in host or "fb.watch" in host:
        return "facebook"
    if "instagram.com" in host or "instagr.am" in host:
        return "instagram"
    return default


def parse_caption(text):
    """'Ofa! ... @duka_letu https://instagram.com/p/X' -> (caption, link, '@duka_letu')."""
    text = (text or "").strip()
    link = ""
    m = POST_LINK.search(text)
    if m:
        link = m.group(0).rstrip(").,")
        text = (text[:m.start()] + text[m.end():]).strip()
    handle = HANDLE.search(text)
    return text, link, (f"@{handle.group(1)}" if handle else "")


def _fallback(caption, account):
    """Bila AI: kichwa = sentensi za mwanzo (bila #hashtag/@akaunti), body = maelezo yote."""
    stripped = HANDLE.sub("", re.sub(r"#\w+", "", caption))
    clean = re.sub(r"\s+", " ", stripped).strip(" -–—:")
    title = ""
    for sentence in re.split(r"(?<=[.!?])[ \t]+|\n+", stripped):
        sentence = re.sub(r"\s+", " ", sentence).strip(" -–—:")
        if not sentence:
            continue
        if title and len(title) >= 12:
            break
        title = f"{title} {sentence}".strip()
        if len(title) >= 25:
            break
    if len(title) > 90:
        title = title[:87].rsplit(" ", 1)[0] + "…"
    title = title or "Habari"
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    body = "\n\n".join(lines) or caption
    return {"title": title, "excerpt": re.sub(r"\s+", " ", clean)[:155], "body": body,
            "keywords": ", ".join(t.lstrip("#") for t in re.findall(r"#\w+", caption)[:5])}


def create_draft(image, filename, caption, link="", account="", category=Category.TANZANIA, rewrite=True,
                 platform=""):
    """
    Unda rasimu ya habari kutoka chapisho la Instagram/Facebook. `image` ni bytes (au None).
    Inarudisha (Article, maelezo).
    """
    caption = (caption or "").strip()
    if not caption and not image:
        raise ValueError("Weka picha au maelezo ya chapisho")
    if image and len(image) > MAX_IMAGE:
        raise ValueError("Picha ni kubwa mno (zaidi ya MB 20)")
    # Link ndiyo ushahidi: ikionyesha mtandao, inashinda chaguo la fomu
    platform = platform_of(link, default=platform if platform in PLATFORMS else "instagram")
    account = account.strip()
    # Instagram: @jina. Facebook: jina la ukurasa kama lilivyo (lina nafasi mara nyingi)
    if account and platform == "instagram" and not account.startswith("@"):
        account = f"@{account}"
    label = f"{PLATFORMS[platform]} {account}".strip()

    note = ""
    data = None
    if rewrite and caption and writer.enabled():
        try:
            data = writer.write_promo(caption, account, notes=EditorSettings.load().editor_notes,
                                      platform=PLATFORMS[platform])
        except writer.WriterError as exc:
            note = f"AI haikupanga maandishi ({exc}) — maelezo asili yametumika."
            log.warning("promo rewrite failed: %s", exc)
    if data is None:
        data = _fallback(caption or "Habari", account)

    taken = bool(link) and Article.objects.filter(source_url=link).exists()
    article = Article(
        title=data["title"], excerpt=data["excerpt"], body=data["body"], keywords=data.get("keywords", ""),
        category=category if category in Category.values else Category.TANZANIA,
        sources=[{"title": label, "url": link or f"https://www.{platform}.com/", "publisher": label}],
        source_url=None if (taken or not link) else link,
        status="draft", origin=platform,
    )
    article.save()
    if image:
        try:
            article.cover_art.save(filename or "habari.jpg", ContentFile(image), save=False)
            quality = photos.process(article, field="cover_art")
            article.save()
            if quality == "low":
                note = (note + " " if note else "") + \
                    f"Picha ni ndogo ({article.photo_size}) — haitaonekana HD."
        except Exception as exc:  # picha mbovu isizuie rasimu
            log.warning("promo photo failed: %s", exc)
            note = (note + " " if note else "") + f"Picha haikusomeka: {exc}"
            article.render_cover()
            article.save()
    else:
        article.render_cover()
        article.save()
    return article, note

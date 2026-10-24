"""
Habari za LIVE — makala inayosasishwa moja kwa moja (mechi, tukio, tangazo kubwa).

Mhariri anafungua makala yoyote iliyochapishwa, anabonyeza "Anza LIVE", kisha anaongeza
masasisho mafupi (goli, kadi, tamko) kutoka kwenye simu — kila moja na muda wake. Wasomaji
wanaona masasisho mapya bila kurefresh (news/templates/news/_live.html).

Kwa Google, makala ya LIVE inatoa `LiveBlogPosting` yenye `liveBlogUpdate`
(news_tags.article_jsonld). Hiyo ndiyo inayoweza kuipa alama ya "LIVE" kwenye Top stories —
Google ndiyo inaamua kuionyesha, hatuwezi kuilazimisha.
"""
from django.utils import timezone
from django.utils.html import escape, linebreaks

from news import indexnow
from news.models import LiveUpdate

MAX_UPDATES = 200   # ukurasa na JSON-LD visiwe vizito mno


def start(article):
    article.live_started_at = article.live_started_at or timezone.now()
    article.live_ended_at = None
    article.save(update_fields=["live_started_at", "live_ended_at", "updated_at"])
    indexnow.submit([article.get_absolute_url()])


def end(article):
    if article.live_started_at and not article.live_ended_at:
        article.live_ended_at = timezone.now()
        article.save(update_fields=["live_ended_at", "updated_at"])
        indexnow.submit([article.get_absolute_url()])


def add(article, text, label="", score=None):
    """Sasisho jipya. `score` (kama limetolewa) linabadilisha matokeo ya juu."""
    update = LiveUpdate.objects.create(article=article, text=text.strip()[:1000], label=label.strip()[:20])
    fields = ["updated_at"]   # sitemap na dateModified zionyeshe kuwa makala imebadilika
    if score is not None:
        article.live_score = score.strip()[:80]
        fields.append("live_score")
    article.save(update_fields=fields)
    return update


def add_score(article, score):
    article.live_score = (score or "").strip()[:80]
    article.save(update_fields=["live_score", "updated_at"])


def as_json(update):
    return {
        "id": update.id,
        "label": update.label,
        "time": timezone.localtime(update.created_at).strftime("%H:%M"),
        "iso": update.created_at.isoformat(),
        "html": linebreaks(escape(update.text)),
    }


def feed(article, after=0):
    """JSON kwa wasomaji walio kwenye ukurasa: masasisho mapya kuliko `after`."""
    qs = article.live_updates.all()
    if after:
        qs = qs.filter(id__gt=after)
    return {
        "live": article.is_live,
        "ended": bool(article.live_ended_at),
        "score": article.live_score,
        "updates": [as_json(u) for u in qs[:50]],
    }

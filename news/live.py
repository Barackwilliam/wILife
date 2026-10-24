"""
Habari za LIVE — makala inayosasishwa moja kwa moja (mechi, tukio, tangazo kubwa).

Mhariri anafungua makala yoyote iliyochapishwa, anabonyeza "Anza LIVE", kisha anaongeza
masasisho mafupi (goli, kadi, tamko) kutoka kwenye simu — kila moja na muda wake. Wasomaji
wanaona masasisho mapya bila kurefresh (news/templates/news/_live.html).

Kwa Google, makala ya LIVE inatoa `LiveBlogPosting` yenye `liveBlogUpdate`
(news_tags.article_jsonld). Hiyo ndiyo inayoweza kuipa alama ya "LIVE" kwenye Top stories —
Google ndiyo inaamua kuionyesha, hatuwezi kuilazimisha.
"""
import hashlib
import re

from django.utils import timezone
from django.utils.html import escape, linebreaks

from news import indexnow
from news.models import LiveMatch, LiveUpdate

_VS = re.compile(r"^\s*(?P<home>.+?)\s+(?:vs\.?|v\.?|dhidi ya)\s+(?P<away>.+?)\s*$", re.I)
_SCORE = re.compile(r"(?P<h>\d{1,2})\s*[-–:]\s*(?P<a>\d{1,2})")
KINDS = {"goal": "⚽", "card": "🟨", "red": "🟥", "sub": "🔁", "var": "📺", "whistle": "📣", "miss": "❌"}

MAX_UPDATES = 200   # ukurasa na JSON-LD visiwe vizito mno


def start(article):
    article.live_started_at = article.live_started_at or timezone.now()
    article.live_ended_at = None
    article.save(update_fields=["live_started_at", "live_ended_at", "updated_at"])
    match = getattr(article, "match", None)
    if match and match.fixture_id is None and match.status in ("NS", "FT"):
        match.status = "LIVE"
        match.save(update_fields=["status"])
    indexnow.submit([article.get_absolute_url()])


def end(article):
    if article.live_started_at and not article.live_ended_at:
        article.live_ended_at = timezone.now()
        article.save(update_fields=["live_ended_at", "updated_at"])
        match = getattr(article, "match", None)
        if match and match.fixture_id is None and not match.is_finished:
            match.status = "FT"
            match.save(update_fields=["status"])
        indexnow.submit([article.get_absolute_url()])


def teams(title):
    """'Simba vs Yanga' -> ('Simba', 'Yanga'); None kama kichwa si cha mechi."""
    m = _VS.match((title or "").split("|")[0].split(":")[0])
    if not m:
        return None
    home, away = m.group("home").strip(" -")[:80], m.group("away").strip(" -")[:80]
    return (home, away) if home and away else None


def attach_match(article, **fields):
    """Mechi ya mhariri: kichwa 'Simba vs Yanga' kinapata ubao wa matokeo."""
    pair = teams(article.title)
    if not pair or hasattr(article, "match"):
        return getattr(article, "match", None)
    return LiveMatch.objects.create(article=article, home=pair[0], away=pair[1],
                                    kickoff=article.live_started_at or timezone.now(), **fields)


def _sync_match_score(article):
    """Matokeo ya maandishi ('Simba 1-0 Yanga') yanajaza magoli ya ubao wa mechi."""
    match = getattr(article, "match", None)
    m = _SCORE.search(article.live_score or "")
    if not match or not m:
        return
    match.home_goals, match.away_goals = int(m.group("h")), int(m.group("a"))
    if match.status == "NS":
        match.status = "LIVE"
    match.save(update_fields=["home_goals", "away_goals", "status"])


def add(article, text, label="", score=None, kind=""):
    """Sasisho jipya. `score` (kama limetolewa) linabadilisha matokeo ya juu."""
    update = LiveUpdate.objects.create(article=article, text=text.strip()[:1000], label=label.strip()[:20],
                                       kind=kind if kind in KINDS else "")
    fields = ["updated_at"]   # sitemap na dateModified zionyeshe kuwa makala imebadilika
    if score is not None:
        article.live_score = score.strip()[:80]
        fields.append("live_score")
    article.save(update_fields=fields)
    if score is not None:
        _sync_match_score(article)
    return update


def add_score(article, score):
    article.live_score = (score or "").strip()[:80]
    article.save(update_fields=["live_score", "updated_at"])
    _sync_match_score(article)


def remove(article):
    """
    Ondoa LIVE nzima kwenye tovuti (ya mhariri au ya API-Football). Makala inafichwa (status
    'rejected') badala ya kufutwa: mechi ya automatic ikifutwa kabisa, ratiba ya API ingeiunda
    upya tick inayofuata. Google inaambiwa (IndexNow) kuwa anwani imeondoka.
    """
    if article.live_started_at and not article.live_ended_at:
        article.live_ended_at = timezone.now()
    article.status = "rejected"
    article.save(update_fields=["status", "live_ended_at", "updated_at"])
    indexnow.submit([article.get_absolute_url()])


def delete_update(update):
    """Futa sasisho moja lililokosewa."""
    article = update.article
    update.delete()
    article.save(update_fields=["updated_at"])


def as_json(update):
    return {
        "id": update.id,
        "label": update.label,
        "kind": update.kind,
        "icon": KINDS.get(update.kind, ""),
        "time": timezone.localtime(update.created_at).strftime("%H:%M"),
        "iso": update.created_at.isoformat(),
        "html": linebreaks(escape(update.text)),
    }


def version(article):
    """
    Alama ya hali ya ukurasa: ikibadilika (mechi imeanza/imeisha, vikosi, video, uchambuzi, kichwa),
    ukurasa unajibadilisha wenyewe bila kurefresh (news/static/news/match.js).
    """
    m = getattr(article, "match", None)
    parts = [article.title, article.status, bool(article.live_started_at), bool(article.live_ended_at)]
    if m:
        x = m.extra or {}
        parts += [m.status, bool(x.get("stats")), bool(x.get("lineups")), bool(x.get("h2h")),
                  bool(x.get("form_home")), len(x.get("highlights") or []), x.get("referee"), m.venue, m.insights]
    return hashlib.md5(repr(parts).encode()).hexdigest()[:12]


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
        "match": article.match.as_json() if hasattr(article, "match") else None,
        "v": version(article),
    }

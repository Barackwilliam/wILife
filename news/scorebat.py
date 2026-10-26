"""
Video za magoli na highlights kutoka ScoreBat (scorebat.com/video-api) — bure, halali kwa embed.

    SCOREBAT_TOKEN          token kutoka scorebat.com/video-api (tab ya "access"). Bila hiyo kipengele kimezimwa.
    SCOREBAT_FEED           free-feed (mpango wa bure: ligi chache, mechi za zamani kidogo, matangazo + nembo
                            ya ScoreBat kwenye video) au featured-feed (mipango ya kulipia).
    SCOREBAT_EVERY_MINUTES  kila ngapi kusoma feed. Kwa masharti ya ScoreBat kila ombi la API = credits 5 na kila
                            video inayotazamwa = credit 1 (Starter: 5,000 kwa mwezi) — kwa hiyo si mara nyingi.

Kila mzunguko wa wakala (core/agent/jobs.py): feed inasomwa mara moja kwa kipindi, kila mechi inahifadhiwa
kama `Highlight`, na kurasa (mechi, /video/) zinasoma database — si ScoreBat — kila msomaji. Player
anapakia iframe msomaji akibonyeza tu, ili credits zitumike kwa video zinazotazamwa kweli.

Usalama: HTML ya embed kutoka API haiwekwi kwenye ukurasa. Tunachukua `src` ya iframe tu, na tu kama ni
https://www.scorebat.com/... — kisha iframe yetu wenyewe ndiyo inaonyeshwa (news/_video_player.html).
Video hazipakuliwi wala kuhifadhiwa: zinabaki kwa mtoaji na wenye haki zake.
"""
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

log = logging.getLogger("news.scorebat")

DEFAULT_FEED = "https://www.scorebat.com/video-api/v3/free-feed/"
KEEP_DAYS = 60
ALLOWED_HOSTS = {"www.scorebat.com", "scorebat.com"}
_SRC = re.compile(r"""src\s*=\s*['"]([^'"]+)['"]""", re.I)
_SUFFIX = re.compile(r"\b(fc|sc|cf|afc|ac|s\.c\.|f\.c\.)\b\.?", re.I)


def token():
    return getattr(settings, "SCOREBAT_TOKEN", "")


def team_key(name):
    """'Manchester United FC' / 'Atlético Madrid' -> 'manchester united' / 'atletico madrid'."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    t = _SUFFIX.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _safe_src(embed):
    """`src` ya iframe kutoka HTML ya embed — https na host ya ScoreBat tu, vinginevyo None."""
    m = _SRC.search(str(embed or ""))
    url = m.group(1).strip() if m else str(embed or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc.lower() not in ALLOWED_HOSTS:
        return None
    return url


def _https(url):
    url = str(url or "").strip()
    return url if url.startswith("https://") else ""


def _teams(raw):
    """(wenyeji, wageni): homeTeam/awayTeam (v3), side1/side2 (v1), au kichwa "A - B"."""
    for a, b in (("homeTeam", "awayTeam"), ("side1", "side2")):
        s1, s2 = raw.get(a) or {}, raw.get(b) or {}
        if isinstance(s1, dict) and isinstance(s2, dict) and s1.get("name") and s2.get("name"):
            return str(s1["name"]).strip(), str(s2["name"]).strip()
    title = str(raw.get("title") or "")
    if " - " in title:
        home, _, away = title.partition(" - ")
        return home.strip(), away.strip()
    return "", ""


def _date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if timezone.is_aware(dt) else dt.replace(tzinfo=dt_timezone.utc)


def parse(data):
    """Jibu la feed -> [dict] tayari kwa Highlight (vitu visivyoeleweka vinarukwa)."""
    rows = data.get("response") if isinstance(data, dict) else data
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        comp = raw.get("competition")
        comp = (comp.get("name") if isinstance(comp, dict) else comp) or ""
        videos = []
        for v in raw.get("videos") or []:
            src = _safe_src(v.get("embed")) if isinstance(v, dict) else None
            if src:
                videos.append({"title": str(v.get("title") or "Highlights")[:120], "src": src})
        if not videos:
            src = _safe_src(raw.get("embed"))               # v1: embed moja kwa mechi
            if src:
                videos.append({"title": "Highlights", "src": src})
        when = _date(raw.get("date"))
        home, away = _teams(raw)
        key = _https(raw.get("matchviewUrl") or raw.get("url")) or f"{raw.get('title')}|{raw.get('date')}"
        if not (videos and when and raw.get("title")):
            continue
        out.append({"key": key[:200], "title": str(raw["title"])[:200], "competition": str(comp)[:120],
                    "home": home[:80], "away": away[:80], "home_key": team_key(home)[:80],
                    "away_key": team_key(away)[:80], "date": when, "thumbnail": _https(raw.get("thumbnail"))[:500],
                    "source_url": _https(raw.get("matchviewUrl") or raw.get("url"))[:500], "videos": videos[:8]})
    return out


def _slug(item):
    base = slugify(f"{item['home']} vs {item['away']}" if item["home"] else item["title"])[:120] or "video"
    return f"{base}-{timezone.localtime(item['date']):%Y-%m-%d}"


def store(items):
    """Hifadhi/sasisha Highlight. Inarudisha (mpya, zilizosasishwa). Kurasa mpya zinatumwa IndexNow."""
    from news import indexnow
    from news.models import Highlight
    new = updated = 0
    fresh = []
    for item in items:
        h = Highlight.objects.filter(key=item["key"]).first()
        if h is None:
            slug, n = _slug(item), 2
            while Highlight.objects.filter(slug=slug).exists():
                slug, n = f"{_slug(item)}-{n}", n + 1
            fresh.append(Highlight.objects.create(slug=slug, **item).get_absolute_url())
            new += 1
        elif (h.videos, h.thumbnail, h.title) != (item["videos"], item["thumbnail"], item["title"]):
            for k, v in item.items():
                setattr(h, k, v)
            h.save()
            updated += 1
    if fresh:
        try:
            indexnow.submit(fresh + ["/video/"])      # Bing/Yandex wanaziona papo hapo; Google kupitia video-sitemap
        except Exception as exc:
            log.warning("indexnow (video) failed: %s", exc)
    return new, updated


def fetch():
    url = getattr(settings, "SCOREBAT_FEED", "") or DEFAULT_FEED
    r = requests.get(url, params={"token": token()}, timeout=20)
    r.raise_for_status()
    return r.json()


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick: soma feed mara moja kila SCOREBAT_EVERY_MINUTES, hifadhi video mpya, futa za zamani."""
    from news.models import DailyCount, Highlight
    out = {"job": "football_video", "sent": 0, "failed": 0, "detail": ""}
    if not token():
        out["detail"] = "SCOREBAT_TOKEN haijawekwa"
        return out
    if dry_run:
        out["detail"] = "dry run"
        return out
    now = now or timezone.now()
    utc = now.astimezone(dt_timezone.utc)
    every = max(15, int(getattr(settings, "SCOREBAT_EVERY_MINUTES", 120)))
    slot = f"scorebat:{(utc.hour * 60 + utc.minute) // every}"   # database, si cache: kila process inaiona
    if DailyCount.get(slot, day=utc.date()):
        out["detail"] = "imeshasomwa kipindi hiki"
        return out
    DailyCount.bump(slot, day=utc.date())
    try:
        data = fetch()
    except Exception as exc:
        log.warning("scorebat feed failed: %s", exc)
        out.update(failed=1, detail=f"kosa: {str(exc)[:200]}")
        return out
    items = parse(data)
    if not items:
        sample = json.dumps(data, ensure_ascii=False, default=str)[:400]
        out["detail"] = f"feed haikuwa na video zinazoeleweka — mfano: {sample}"
        return out
    new, updated = store(items)
    Highlight.objects.filter(date__lt=now - timedelta(days=KEEP_DAYS)).delete()
    out["sent"] = new
    out["detail"] = f"video mpya {new}, zilizosasishwa {updated}, feed {len(items)}"
    return out


def for_match(home, away, kickoff):
    """Highlight ya mechi hii (majina ya timu + siku ±1), au None."""
    from news.models import Highlight
    if not (home and away and kickoff):
        return None
    hk, ak = team_key(home), team_key(away)
    qs = Highlight.objects.filter(date__gte=kickoff - timedelta(days=1), date__lte=kickoff + timedelta(days=2))
    for h in qs.filter(home_key=hk, away_key=ak)[:1]:
        return h
    for h in qs:                                      # majina yanaweza kutofautiana kidogo (Man Utd / Manchester United)
        if _close(hk, h.home_key) and _close(ak, h.away_key):
            return h
    return None


def _close(a, b):
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    return bool(wa & wb) and len(wa & wb) >= min(len(wa), len(wb))


def match_page(h):
    """
    Ukurasa wetu wa mechi ya video hii: {'url', 'score', 'goals'} kutoka LIVE au ratiba, au None.
    goals = ["23' B. Saka (Arsenal)", ...] — maandishi ya kipekee kwa ukurasa wa video (SEO).
    """
    from news.models import Fixture, LiveMatch
    start, end = h.date - timedelta(days=1), h.date + timedelta(days=1)
    for model, qs in ((LiveMatch, LiveMatch.objects.select_related("article").filter(article__status="published")),
                      (Fixture, Fixture.objects.all())):
        for m in qs.filter(kickoff__gte=start, kickoff__lte=end):
            if _close(team_key(m.home), h.home_key) and _close(team_key(m.away), h.away_key):
                if model is LiveMatch:
                    url = m.article.get_absolute_url()
                    goals = [f"{u.label} {u.text.lstrip('⚽ ')}" for u in
                             m.article.live_updates.filter(kind="goal").order_by("created_at", "id")]
                else:
                    url = m.get_absolute_url()
                    goals = [f"{e.get('label', '')} {e.get('text', '').lstrip('⚽ ')}".strip()
                             for e in (m.extra or {}).get("events") or [] if e.get("kind") == "goal"]
                score = f"{m.home_goals}-{m.away_goals}" if m.has_score and m.is_finished else ""
                return {"url": url, "score": score, "score_text": f"{m.home} {score} {m.away}" if score else "",
                        "goals": goals[:12]}
    return None


def title(h, match=None):
    """Kichwa chenye matokeo na maneno yanayotafutwa: 'Arsenal vs Chelsea 2-1 Highlights: Video za Magoli Yote'."""
    if not (h.home and h.away):
        return h.title[:120]                  # mf. video ya YouTube isiyo ya mechi moja: kichwa chake halisi
    match = match if match is not None else match_page(h)
    score = f" {match['score']}" if match and match.get("score") else ""
    return f"{h.name}{score} Highlights: Video za Magoli Yote"


def description(h, match=None):
    """Maelezo ya kipekee ya video (ukurasa, JSON-LD, video-sitemap) kwa Kiswahili."""
    from news.templatetags.news_tags import sw_date
    match = match if match is not None else match_page(h)
    day = sw_date(h.date)
    parts = [f"Tazama video za magoli na highlights za {h.name}"
             + (f" ({h.league})" if h.league else "") + f", mechi ya {day}."]
    if match and match.get("score_text"):
        parts.append(f"Matokeo ya mwisho: {match['score_text']}.")
    if match and match.get("goals"):
        parts.append("Magoli: " + "; ".join(match["goals"][:6]) + ".")
    if len(h.videos) > 1:
        parts.append(f"Video {len(h.videos)}: " + ", ".join(v["title"] for v in h.videos[:6]) + ".")
    return " ".join(parts)

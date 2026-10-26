"""
Video za chaneli RASMI za YouTube (klabu, ligi, shirikisho) — chanzo halali cha highlights za Tanzania.

    YOUTUBE_API_KEY          ufunguo wa YouTube Data API v3 (Google Cloud, bure). Tupu = kimezimwa.
    YOUTUBE_EVERY_MINUTES    kila ngapi kusoma chaneli (default 60).

Chaneli zinaongezwa kwenye Admin -> Video channels (VideoChannel) kwa kubandika kiungo; resolve()
inazithibitisha. Kila mzunguko: kwa kila chaneli ombi 1 (orodha ya video mpya, unit 1) + ombi 1 (hali
na urefu wa video hizo, unit 1) — chaneli 10 kila saa = units ~480 kati ya 10,000 za bure kwa siku.

Video zinahifadhiwa kama `Highlight` (provider='youtube') na kuonyeshwa kupitia youtube-nocookie.com
(hakuna cookies za matangazo kwa msomaji). Video zisizoruhusiwa kupachikwa (embeddable=false), za
binafsi, au LIVE zinazoendelea zinarukwa.
"""
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

log = logging.getLogger("news.youtube")

API = "https://www.googleapis.com/youtube/v3"
EMBED = "https://www.youtube-nocookie.com/embed/{id}"
WATCH = "https://www.youtube.com/watch?v={id}"
PER_CHANNEL = 15
KEEP_DAYS = 60
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID = re.compile(r"(UC[A-Za-z0-9_-]{22})")
_HANDLE = re.compile(r"(?:youtube\.com/)?(@[A-Za-z0-9._-]{3,100})")
HIGHLIGHT_WORDS = ("highlight", "magoli", "mabao", "goals", "goal", "bao", "goli", "extended", "all goals",
                   "matokeo", "full match", "muhtasari")
_VS = re.compile(r"^\s*(.+?)\s+(?:vs\.?|v|dhidi ya|-|–|x)\s+(.+?)\s*(?:[|:(\[]|\s\d+\s*-\s*\d+|$)", re.I)
_SCORE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})")


def key():
    return getattr(settings, "YOUTUBE_API_KEY", "")


class YouTubeError(RuntimeError):
    pass


def _get(path, params):
    r = requests.get(f"{API}/{path}", params={**params, "key": key()}, timeout=15)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        msg = ((data.get("error") or {}).get("message") or r.text or "")[:200]
        raise YouTubeError(f"YouTube {r.status_code}: {msg}")
    return data


def parse_link(link):
    """Kiungo/handle/namba -> {'id': 'UC…'} au {'forHandle': '@jina'}, au None."""
    link = (link or "").strip()
    m = _CHANNEL_ID.search(link)
    if m:
        return {"id": m.group(1)}
    m = _HANDLE.search(link)
    if m:
        return {"forHandle": m.group(1)}
    return None


def resolve(link):
    """(channel_id, jina) kutoka kiungo — ombi 1. YouTubeError kama chaneli haipo / kiungo si sahihi."""
    q = parse_link(link)
    if not q:
        raise YouTubeError("Kiungo si cha chaneli ya YouTube. Mfano: https://www.youtube.com/@jina")
    data = _get("channels", {"part": "snippet", **q})
    items = data.get("items") or []
    if not items:
        raise YouTubeError("Chaneli haikupatikana kwenye YouTube — hakikisha kiungo ni sahihi.")
    return items[0]["id"], items[0]["snippet"]["title"][:120]


def _seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def teams_from_title(title):
    """'Simba SC vs Yanga | Highlights' -> ('Simba SC', 'Yanga'); vinginevyo ('', '')."""
    t = re.sub(r"(?i)\b(highlights?|all goals|magoli yote|full match|extended)\b", " ", title or "")
    t = re.sub(r"\s(\d{1,2})\s*[-–]\s*(\d{1,2})\s", " vs ", f" {t} ", count=1)   # 'Simba 2-0 Yanga' -> 'Simba vs Yanga'
    t = t.strip(" |:-–")
    m = _VS.match(t)
    if not m:
        return "", ""
    home, away = (re.sub(r"\s+", " ", x).strip(" -|:") for x in m.groups())
    if not home or not away or len(home) > 60 or len(away) > 60:
        return "", ""
    return home, re.sub(_SCORE, "", away).strip(" -|:")


def _wanted(title, channel):
    if not channel.highlights_only:
        return True
    low = (title or "").lower()
    return any(w in low for w in HIGHLIGHT_WORDS)


def _thumb(snippet):
    th = snippet.get("thumbnails") or {}
    for k in ("maxres", "standard", "high", "medium", "default"):
        url = (th.get(k) or {}).get("url") or ""
        if url.startswith("https://"):
            return url
    return ""


def channel_videos(channel):
    """Video mpya za chaneli zinazofaa (2 maombi). [dict tayari kwa Highlight]."""
    from news.scorebat import team_key
    items = _get("playlistItems", {"part": "snippet,contentDetails", "playlistId": channel.uploads,
                                   "maxResults": PER_CHANNEL}).get("items") or []
    picked = {}
    for it in items:
        vid = (it.get("contentDetails") or {}).get("videoId") or ""
        sn = it.get("snippet") or {}
        if _ID.match(vid) and _wanted(sn.get("title"), channel):
            picked[vid] = sn
    if not picked:
        return []
    details = {v["id"]: v for v in _get("videos", {"part": "status,contentDetails,snippet",
                                                   "id": ",".join(picked)}).get("items") or []}
    out = []
    for vid, sn in picked.items():
        d = details.get(vid) or {}
        st, cd, ds = d.get("status") or {}, d.get("contentDetails") or {}, d.get("snippet") or {}
        if not st.get("embeddable") or st.get("privacyStatus") != "public" \
                or ds.get("liveBroadcastContent", "none") != "none":
            continue                       # haiwezi kupachikwa / si ya umma / LIVE inayoendelea
        title = (ds.get("title") or sn.get("title") or "")[:200]
        try:
            when = datetime.fromisoformat((ds.get("publishedAt") or sn.get("publishedAt")).replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        home, away = teams_from_title(title)
        out.append({"key": f"yt:{vid}", "title": title, "competition": channel.name[:120], "home": home[:80],
                    "away": away[:80], "home_key": team_key(home)[:80], "away_key": team_key(away)[:80],
                    "date": when.astimezone(dt_timezone.utc), "thumbnail": _thumb(ds or sn)[:500],
                    "source_url": WATCH.format(id=vid), "videos": [{"title": "Video", "src": EMBED.format(id=vid)}],
                    "channel": channel.name[:120], "team": channel.team, "duration": _seconds(cd.get("duration")),
                    "provider": "youtube"})
    return out


def _slug(item):
    from news.models import Highlight
    name = f"{item['home']} vs {item['away']}" if item["home"] and item["away"] else item["title"]
    base = f"{slugify(name)[:110] or 'video'}-{timezone.localtime(item['date']):%Y-%m-%d}"
    slug, n = base, 2
    while Highlight.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def store(items):
    from news import indexnow
    from news.models import Highlight
    new, fresh = 0, []
    for item in items:
        if Highlight.objects.filter(key=item["key"]).exists():
            continue
        fresh.append(Highlight.objects.create(slug=_slug(item), **item).get_absolute_url())
        new += 1
    if fresh:
        try:
            indexnow.submit(fresh + ["/video/"])
        except Exception as exc:
            log.warning("indexnow (youtube) failed: %s", exc)
    return new


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick: kila chaneli rasmi inasomwa mara moja kila YOUTUBE_EVERY_MINUTES."""
    from news.models import VideoChannel
    out = {"job": "football_youtube", "sent": 0, "failed": 0, "detail": ""}
    if not key():
        out["detail"] = "YOUTUBE_API_KEY haijawekwa"
        return out
    if dry_run:
        out["detail"] = "dry run"
        return out
    now = now or timezone.now()
    every = timedelta(minutes=max(15, int(getattr(settings, "YOUTUBE_EVERY_MINUTES", 60))))
    notes = []
    for ch in VideoChannel.objects.filter(active=True, channel_id=None):
        try:                                   # chaneli zilizowekwa kwenye code (data migration): zithibitishwe
            cid, name = resolve(ch.link)
        except Exception as exc:
            VideoChannel.objects.filter(pk=ch.pk).update(error=str(exc)[:300])
            notes.append(f"{ch.link}: haikuthibitishwa")
            continue
        if VideoChannel.objects.filter(channel_id=cid).exclude(pk=ch.pk).exists():
            VideoChannel.objects.filter(pk=ch.pk).update(active=False, error="Chaneli hii tayari ipo")
            continue
        VideoChannel.objects.filter(pk=ch.pk).update(channel_id=cid, name=name, error="")
        notes.append(f"{name}: imethibitishwa")
    for ch in VideoChannel.objects.filter(active=True).exclude(channel_id=None):
        if ch.checked_at and now - ch.checked_at < every:
            continue
        VideoChannel.objects.filter(pk=ch.pk).update(checked_at=now)   # muda wa jaribio — kosa lisirudiwe kila dakika
        try:
            n = store(channel_videos(ch))
            VideoChannel.objects.filter(pk=ch.pk).update(error="")
        except Exception as exc:
            log.warning("youtube %s failed: %s", ch, exc)
            VideoChannel.objects.filter(pk=ch.pk).update(error=str(exc)[:300])
            out["failed"] += 1
            notes.append(f"{ch}: kosa")
            continue
        out["sent"] += n
        if n:
            notes.append(f"{ch}: video mpya {n}")
    from news.models import Highlight
    Highlight.objects.filter(provider="youtube", date__lt=now - timedelta(days=KEEP_DAYS)).delete()
    out["detail"] = "; ".join(notes) or "hakuna video mpya"
    return out

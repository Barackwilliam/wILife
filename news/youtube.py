"""
Video za chaneli RASMI za YouTube (klabu, ligi, shirikisho) — chanzo halali cha highlights za Tanzania.

    YOUTUBE_API_KEY          ufunguo wa YouTube Data API v3 (Google Cloud, bure). Tupu = kimezimwa.
    YOUTUBE_EVERY_MINUTES    kila ngapi kusoma chaneli (default 60).
    YOUTUBE_SEARCHES_PER_DAY utafutaji wa video kwa mechi (unit 100 kila mmoja; default 80 = units 8,000).
    YOUTUBE_MIN_SUBSCRIBERS  chaneli isiyo kwenye Admin inaaminika tu ikiwa na wanachama hawa (default 100,000).

Chaneli zinaongezwa kwenye Admin -> Video channels (VideoChannel) kwa kubandika kiungo; resolve()
inazithibitisha. Kila mzunguko: kwa kila chaneli ombi 1 (orodha ya video mpya, unit 1) + ombi 1 (hali
na urefu wa video hizo, unit 1) — chaneli 10 kila saa = units ~480 kati ya 10,000 za bure kwa siku.

Pia, kwa KILA mechi iliyokwisha kwenye mfumo (Fixture/LiveMatch, Tanzania na dunia) isiyo na video,
YouTube inatafutwa ("Home vs Away highlights") baada ya saa 2 na tena baada ya saa 12. Video inachukuliwa tu
ikiwa kichwa chake kinataja timu zote mbili na ni highlights, na chaneli ni RASMI: iko kwenye Admin, au ina
wanachama wengi na jina lake ni la timu husika / ligi / mtangazaji anayejulikana (TRUSTED_NAMES) — si
chaneli za mashabiki, michezo ya video au "reaction".

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
                   "matokeo", "full match", "muhtasari",
                   "resumen", "goles", "melhores momentos", "gols", "résumé", "resume", "buts", "sintesi",
                   "alle tore", "zusammenfassung", "samenvatting", "ملخص", "أهداف")
REGION = "TZ"                  # wasomaji wengi wako Tanzania: video iliyozuiwa hapa hairuhusiwi
_VS = re.compile(r"^\s*(.+?)\s+(?:vs\.?|v|dhidi ya|-|–|x)\s+(.+?)\s*(?:[|:(\[]|\s\d+\s*-\s*\d+|$)", re.I)
_SCORE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})")


# Majina ya ligi, mashirikisho na watangazaji wenye haki za highlights (kwa team_key: herufi ndogo, bila FC/SC)
TRUSTED_NAMES = ("premier league", "laliga", "la liga", "serie a", "bundesliga", "ligue 1", "eredivisie", "liga portugal",
                 "saudi pro league", "mls", "uefa", "caf", "fifa", "concacaf", "conmebol", "the fa", "azam",
                 "supersport", "bein sports", "cbs sports", "espn", "tnt sports", "dazn", "nbc sports", "sky sports",
                 "fox sports", "sportsmax", "onefootball")
BLOCKED_WORDS = ("fan", "fans", "reaction", "react", "efootball", "pes", "gaming", "gameplay", "simulation",
                 "prediction", "watchalong", "fancast", "fanzone", "supporters", "podcast", "talk", "fc 24", "fc 25", "fc 26", "fc 27", "fifa 23", "ps5", "xbox", "news 24")
SEARCH_AFTER = (timedelta(hours=2), timedelta(hours=12))     # majaribio: saa 2 na saa 12 baada ya kuanza
SEARCH_WINDOW = timedelta(hours=36)
SEARCH_PER_RUN = 8
MIN_SECONDS = 40               # chini ya hapa ni "shorts" — mara nyingi vipande vilivyopakiwa upya


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
    t = re.sub(r"(?i)^\W*(magoli|mabao|goals?)\b", " ", t)               # 'Magoli | Namungo 2-1 Coastal | …'
    t = re.sub(r"^[\s|:\-–]+", "", t)
    t = re.sub(r"\s(\d{1,2})\s*[-–]\s*(\d{1,2})\s", " vs ", f" {t} ", count=1)   # 'Simba 2-0 Yanga' -> 'Simba vs Yanga'
    t = t.strip(" |:-–")
    m = _VS.match(t)
    if not m:
        return "", ""
    home, away = (re.sub(r"\s+", " ", x).strip(" -|:") for x in m.groups())
    if not home or not away or len(home) > 60 or len(away) > 60:
        return "", ""
    return home, re.sub(_SCORE, "", away).strip(" -|:")


def _is_highlight(title):
    low = (title or "").lower()
    return any(w in low for w in HIGHLIGHT_WORDS)


def _wanted(title, channel):
    return not channel.highlights_only or _is_highlight(title)


def _thumb(snippet):
    th = snippet.get("thumbnails") or {}
    for k in ("maxres", "standard", "high", "medium", "default"):
        url = (th.get(k) or {}).get("url") or ""
        if url.startswith("https://"):
            return url
    return ""


def _playable(d):
    """Video inaweza kuchezwa kwenye wILife na msomaji wa Tanzania?"""
    st, ds = d.get("status") or {}, d.get("snippet") or {}
    if not st.get("embeddable") or st.get("privacyStatus") != "public" \
            or ds.get("liveBroadcastContent", "none") != "none":
        return False
    rr = (d.get("contentDetails") or {}).get("regionRestriction") or {}
    if "allowed" in rr and REGION not in rr["allowed"]:
        return False
    return REGION not in (rr.get("blocked") or [])


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
        if not _playable(d):
            continue                       # haiwezi kupachikwa / si ya umma / LIVE / imezuiwa Tanzania
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
    try:
        found, searched = search_matches(now, notes)
        out["sent"] += found
    except YouTubeError as exc:                # mf. units za siku zimekwisha — jaribu kesho
        log.warning("youtube search failed: %s", exc)
        out["failed"] += 1
        notes.append(f"utafutaji: {str(exc)[:120]}")
    from news.models import Highlight
    Highlight.objects.filter(provider="youtube", date__lt=now - timedelta(days=KEEP_DAYS)).delete()
    out["detail"] = "; ".join(notes) or "hakuna video mpya"
    return out


# ── Video kwa kila mechi iliyokwisha (Tanzania na dunia) ───────────────────────

def _aliases(name):
    """Majina yote ya timu kwa team_key: 'Young Africans' -> {'young africans', 'yanga', ...} (Admin -> Teams)."""
    from news.models import Team
    from news.scorebat import team_key
    k = team_key(name)
    out = {k} if k else set()
    for t in Team.objects.filter(active=True):
        keys = {team_key(n) for n in t.match_names.split(",") if n.strip()} | {team_key(t.name)}
        if k in keys:
            out |= keys
    return {x for x in out if x}


def _mentions(text, keys):
    """Kichwa/jina linataja timu hii? (jina lote, au maneno yake yote yenye herufi 3+)."""
    from news.scorebat import team_key
    t = f" {team_key(text)} "
    for k in keys:
        words = [w for w in k.split() if len(w) >= 3] or k.split()
        if f" {k} " in t or (words and all(f" {w} " in t for w in words)):
            return True
    return False


def _trusted_channel(ch, team_keys, min_subs):
    """Chaneli isiyo kwenye Admin: yenye wanachama wengi, si ya mashabiki/michezo, na jina la timu/ligi/mtangazaji."""
    from news.scorebat import team_key
    sn, stats = ch.get("snippet") or {}, ch.get("statistics") or {}
    if stats.get("hiddenSubscriberCount") or int(stats.get("subscriberCount") or 0) < min_subs:
        return False
    raw = f" {re.sub(r'[^a-z0-9]+', ' ', (sn.get('title') or '').lower())} "
    if any(f" {w} " in raw for w in BLOCKED_WORDS):
        return False
    name = f" {team_key(sn.get('title'))} "
    return any(f" {n} " in name for n in TRUSTED_NAMES) or _mentions(sn.get("title"), team_keys)


def _iso(dt):
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_match(match, min_subs=None):
    """Video rasmi ya highlights ya mechi hii kutoka YouTube (unit ~102), au None."""
    from news.models import VideoChannel
    min_subs = min_subs if min_subs is not None else int(getattr(settings, "YOUTUBE_MIN_SUBSCRIBERS", 100000))
    home_keys, away_keys = _aliases(match.home), _aliases(match.away)
    res = _get("search", {"part": "snippet", "type": "video", "videoEmbeddable": "true", "maxResults": 15,
                          "q": f"{match.home} vs {match.away} highlights", "regionCode": REGION,
                          "publishedAfter": _iso(match.kickoff - timedelta(hours=1)),
                          "publishedBefore": _iso(match.kickoff + timedelta(days=4))}).get("items") or []
    order = []
    for it in res:
        vid, title = (it.get("id") or {}).get("videoId") or "", (it.get("snippet") or {}).get("title") or ""
        if _ID.match(vid) and _is_highlight(title) and _mentions(title, home_keys) and _mentions(title, away_keys):
            order.append(vid)
    if not order:
        return None
    details = {v["id"]: v for v in _get("videos", {"part": "status,contentDetails,snippet",
                                                   "id": ",".join(order)}).get("items") or []}
    ok = [v for v in order if v in details and _playable(details[v])
          and (_seconds((details[v].get("contentDetails") or {}).get("duration")) or 0) >= MIN_SECONDS]
    if not ok:
        return None
    cids = {details[v]["snippet"].get("channelId") for v in ok} - {None}
    known = {c.channel_id: c for c in VideoChannel.objects.filter(active=True, channel_id__in=cids)}
    others = {}
    if cids - set(known):
        others = {c["id"]: c for c in _get("channels", {"part": "snippet,statistics",
                                                        "id": ",".join(sorted(cids - set(known)))}).get("items") or []}

    def rank(v):
        cid = details[v]["snippet"].get("channelId")
        if cid in known:
            return (0, 0, order.index(v))
        subs = int(((others.get(cid) or {}).get("statistics") or {}).get("subscriberCount") or 0)
        return (1, -subs, order.index(v))
    good = [v for v in ok if details[v]["snippet"].get("channelId") in known
            or (details[v]["snippet"].get("channelId") in others
                and _trusted_channel(others[details[v]["snippet"]["channelId"]], home_keys | away_keys, min_subs))]
    if not good:
        return None
    vid = min(good, key=rank)
    d = details[vid]
    ds, cid = d["snippet"], d["snippet"].get("channelId")
    ch = known.get(cid)
    return {"vid": vid, "title": (ds.get("title") or "")[:200], "date": ds.get("publishedAt"),
            "thumbnail": _thumb(ds), "duration": _seconds((d.get("contentDetails") or {}).get("duration")),
            "channel": (ch.name if ch else ((others.get(cid) or {}).get("snippet") or {}).get("title")
                        or ds.get("channelTitle") or "")[:120], "team": ch.team if ch else None}


def _due_matches(now):
    """Mechi zilizokwisha ndani ya saa 36, zilizopewa kipaumbele kwanza (LiveMatch, kisha nafasi ya ligi)."""
    from news.models import Fixture, LiveMatch
    from news.scorebat import team_key
    rows = [(0, m) for m in LiveMatch.objects.filter(
        status__in=LiveMatch.FINISHED, kickoff__gte=now - SEARCH_WINDOW, kickoff__lte=now - SEARCH_AFTER[0])]
    rows += [(m.rank + 1, m) for m in Fixture.objects.filter(
        status__in=LiveMatch.FINISHED, kickoff__gte=now - SEARCH_WINDOW, kickoff__lte=now - SEARCH_AFTER[0])]
    rows.sort(key=lambda r: (r[0], -r[1].kickoff.timestamp()))
    seen = set()
    for _, m in rows:
        ident = (team_key(m.home), team_key(m.away), m.kickoff.astimezone(dt_timezone.utc).date())
        if not ident[0] or not ident[1] or ident in seen:
            continue
        seen.add(ident)
        yield m, f"yts:{ident[0][:25]}:{ident[1][:25]}", ident[2]


def search_matches(now, notes):
    """Tafuta video za mechi zilizokwisha zisizo na video — ndani ya kikomo cha siku. -> (zilizopatikana, maombi)."""
    from news.models import DailyCount, Highlight
    from news.scorebat import for_match, team_key
    limit = int(getattr(settings, "YOUTUBE_SEARCHES_PER_DAY", 80))
    today = now.astimezone(dt_timezone.utc).date()
    found = searched = 0
    for m, tag, day in _due_matches(now):
        if searched >= SEARCH_PER_RUN or DailyCount.get("yt:search", day=today) >= limit:
            break
        tries = DailyCount.get(tag, day=day)
        if tries >= len(SEARCH_AFTER) or now - m.kickoff < SEARCH_AFTER[tries] \
                or for_match(m.home, m.away, m.kickoff) is not None:
            continue
        DailyCount.bump(tag, day=day)
        DailyCount.bump("yt:search", day=today)
        searched += 1
        hit = search_match(m)
        if not hit:
            continue
        try:
            when = datetime.fromisoformat(hit["date"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            when = m.kickoff
        names = {"home": m.home[:80], "away": m.away[:80],
                 "home_key": team_key(m.home)[:80], "away_key": team_key(m.away)[:80]}
        old = Highlight.objects.filter(key=f"yt:{hit['vid']}").first()
        if old:                             # tayari ipo (kutoka chaneli) ila kichwa hakikusomeka: iunganishe na mechi
            Highlight.objects.filter(pk=old.pk).update(**names)
            continue
        found += store([{"key": f"yt:{hit['vid']}", "title": hit["title"], "competition": (m.competition or "")[:120],
                         **names, "date": when.astimezone(dt_timezone.utc), "thumbnail": hit["thumbnail"][:500],
                         "source_url": WATCH.format(id=hit["vid"]),
                         "videos": [{"title": "Video", "src": EMBED.format(id=hit["vid"])}],
                         "channel": hit["channel"], "team": hit["team"], "duration": hit["duration"],
                         "provider": "youtube"}])
        notes.append(f"{m.home} vs {m.away}: video ({hit['channel']})")
    if searched:
        notes.append(f"utafutaji {searched}, video {found}")
    return found, searched

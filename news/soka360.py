"""
WhatsApp Channel ya Soka360: video zinazochezwa NDANI ya WhatsApp, zenyewe, kila mechi ikiisha.

    1. Video ya matokeo (yetu wenyewe, news/match_video.py): kila mechi iliyokwisha kwenye mfumo —
       MP4 inapakiwa kwenye channel, kwa hiyo inachezwa bila kutoka WhatsApp.
    2. Highlights RASMI (Highlight za YouTube, news/youtube.py): link ya YouTube yenye kadi (picha + kichwa).
       Video ya mmiliki haipakuliwi wala haipakiwi upya. ZIMEZIMWA kwa default (SOKA360_HIGHLIGHTS_PER_DAY=0):
       kwenye channel WhatsApp hufungua YouTube, kwa hiyo msomaji hutoka kwenye channel.

Kila video ya matokeo inamhimiza msomaji kufuata channel (SOKA360_FOLLOW_URL).

Posti moja kwa kila tick, kwa mpangilio wa muda (iliyokuwa tayari kwanza), angalau SOKA360_GAP_MINUTES
kati ya posti, na kikomo cha siku kwa kila aina. Mechi/video za saa SOKA360_WINDOW_HOURS zilizopita tu
— baada ya kuwasha, za jana zinaingia kwanza.

    WHATSAPP_CHANNEL                link au JID ya channel (namba ya bridge lazima iwe admin)
    SOKA360_POSTS                   true/false (default true)
    SOKA360_GAP_MINUTES             default 10
    SOKA360_RESULTS_PER_DAY         video za matokeo kwa siku (default 15)
    SOKA360_HIGHLIGHTS_PER_DAY      highlights kwa siku (default 0 = zimezimwa)
    SOKA360_FOLLOW_URL              link ya kufuata channel (kwenye maelezo ya kila video)
    SOKA360_WINDOW_HOURS            default 48
"""
import logging
import time
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

log = logging.getLogger("news.soka360")

MAX_TRIES = 3
RESULT_DELAY = timedelta(minutes=15)      # mechi ikiisha: subiri takwimu za mwisho kabla ya video
VIDEO_SECONDS = 15                        # muda unaohitajika kwenye tick kutengeneza na kutuma video


def _cfg(name, default):
    return int(getattr(settings, name, default))


def _site():
    return getattr(settings, "SITE_URL", "").rstrip("/")


def _host():
    return _site().split("://", 1)[-1]


def _usable(key):
    from news.models import ChannelPost
    p = ChannelPost.objects.filter(key=key).first()
    return p is None or (p.posted_at is None and p.tries < MAX_TRIES)


def _posted_today(kind, now):
    from news.models import ChannelPost
    start = now.astimezone(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return ChannelPost.objects.filter(kind=kind, posted_at__gte=start).count()


def _match_url(m):
    article = getattr(m, "article", None)
    url = article.get_absolute_url() if article is not None else m.get_absolute_url()
    return _site() + url


# ── Video za matokeo ───────────────────────────────────────────────────────────

def _results(now):
    """[(tayari_saa, key, mechi)] za mechi zilizokwisha zenye matokeo: LiveMatch (zina wafungaji) kwanza."""
    from news.models import Fixture, LiveMatch
    from news.scorebat import team_key
    window = now - timedelta(hours=_cfg("SOKA360_WINDOW_HOURS", 48))
    finished = dict(status__in=LiveMatch.FINISHED, kickoff__gte=window, home_goals__isnull=False,
                    away_goals__isnull=False)
    rows = [(0, m) for m in LiveMatch.objects.filter(**finished).select_related("article")]
    rows += [(f.rank + 1, f) for f in Fixture.objects.filter(**finished)]
    rows.sort(key=lambda r: (r[0], r[1].kickoff))
    seen, out = set(), []
    for _, m in rows:
        day = m.kickoff.astimezone(dt_timezone.utc).date()
        ident = (team_key(m.home)[:40], team_key(m.away)[:40], day)
        if ident in seen or not ident[0] or not ident[1]:
            continue
        seen.add(ident)
        ready = m.kickoff + timedelta(minutes=110) + RESULT_DELAY
        key = f"result:{ident[0]}:{ident[1]}:{day}"
        if ready <= now and _usable(key):
            out.append((ready, key, m))
    return out


def follow_url():
    from news.digest import channel_link
    return (getattr(settings, "SOKA360_FOLLOW_URL", "") or channel_link()).strip()


def result_caption(m):
    from news.match_video import goals_of
    lines = [f"⚽ *MATOKEO* | {m.home} {m.home_goals}-{m.away_goals} {m.away}"]
    if m.competition:
        lines.append(f"🏆 {m.competition}")
    goals = goals_of(m)
    if goals:
        lines += ["", "*Magoli:*"] + [f"{label} {text}".strip() for label, text in goals[:8]]
    lines += ["", f"📊 Takwimu kamili: {_match_url(m)}"]
    if follow_url():
        lines += ["", "📲 *Fuata Soka360* upate matokeo na video ya kila mechi hapa hapa WhatsApp 👇", follow_url(),
                  "Mshirikishe rafiki yako mpenda soka ⚽"]
    return "\n".join(lines)


def _post_result(channel, m):
    from core.agent import whatsapp
    from news import match_video
    from news.football import _image
    data = match_video.render(m, home_logo=_image(m.home_logo), away_logo=_image(m.away_logo),
                              comp_logo=_image(m.competition_logo), site=_host())
    whatsapp.send_channel_video(channel, data, result_caption(m))


# ── Highlights rasmi (YouTube) ─────────────────────────────────────────────────

def _highlights(now):
    from news.models import Highlight
    window = now - timedelta(hours=_cfg("SOKA360_WINDOW_HOURS", 48))
    out = []
    for h in Highlight.objects.filter(provider="youtube", date__gte=window).exclude(home="").exclude(away="") \
            .order_by("date"):
        key = f"highlight:{h.key}"
        if h.main and _usable(key):
            out.append((h.date, key, h))
    return out


def highlight_text(h):
    from news import scorebat
    match = scorebat.match_page(h)
    score = f" {match['score']}" if match and match.get("score") else ""
    lines = [f"🎬 *HIGHLIGHTS* | {h.home} vs {h.away}{score}"]
    if h.competition:
        lines.append(f"🏆 {h.competition}")
    lines += ["", f"▶️ {h.source_url}", ""]
    if h.channel:
        lines.append(f"📺 Video rasmi ya {h.channel}")
    lines.append(f"📊 Matokeo na takwimu: {_site()}{match['url'] if match and match.get('url') else h.get_absolute_url()}")
    return "\n".join(lines)


def _post_highlight(channel, h):
    from core.agent import whatsapp
    whatsapp.send_channel(channel, highlight_text(h), preview={
        "url": h.source_url, "title": h.title[:200], "description": f"Video rasmi: {h.channel}" if h.channel else "",
        "thumbnail": h.thumbnail})


# ── Kazi ya tick ───────────────────────────────────────────────────────────────

def run(now=None, deadline=None, dry_run=False):
    from news.models import ChannelPost
    out = {"job": "soka360", "sent": 0, "failed": 0, "detail": ""}
    channel = getattr(settings, "WHATSAPP_CHANNEL", "").strip()
    if not channel or not getattr(settings, "SOKA360_POSTS", True):
        out["detail"] = "WHATSAPP_CHANNEL haijawekwa" if not channel else "SOKA360_POSTS imezimwa"
        return out
    if not (settings.AGENT_ENABLED and settings.WHATSAPP_ENABLED):
        out["detail"] = "WhatsApp imezimwa"            # isihesabiwe kama imetumwa
        return out
    now = now or timezone.now()
    last = ChannelPost.objects.exclude(posted_at=None).order_by("-posted_at").values_list("posted_at", flat=True).first()
    if last and now - last < timedelta(minutes=_cfg("SOKA360_GAP_MINUTES", 10)):
        out["detail"] = "nafasi kati ya posti"
        return out
    queue = []
    if _posted_today("result", now) < _cfg("SOKA360_RESULTS_PER_DAY", 15) \
            and not (deadline and deadline - time.monotonic() < VIDEO_SECONDS):
        queue += [(r[0], 1, "result", r[1], r[2]) for r in _results(now)[:1]]
    if _posted_today("highlight", now) < _cfg("SOKA360_HIGHLIGHTS_PER_DAY", 0):
        queue += [(r[0], 0, "highlight", r[1], r[2]) for r in _highlights(now)[:1]]
    if not queue:
        out["detail"] = "hakuna jipya"
        return out
    _, _, kind, key, obj = min(queue, key=lambda q: (q[0], q[1]))
    title = (f"{obj.home} vs {obj.away}")[:200]
    if dry_run:
        out["detail"] = f"dry run: {kind} {title}"
        return out
    post, _ = ChannelPost.objects.get_or_create(key=key, defaults={"kind": kind, "title": title})
    ChannelPost.objects.filter(pk=post.pk).update(tries=post.tries + 1)
    try:
        (_post_result if kind == "result" else _post_highlight)(channel, obj)
    except Exception as exc:
        log.warning("soka360 %s failed: %s", key, exc)
        ChannelPost.objects.filter(pk=post.pk).update(error=str(exc)[:300])
        out.update(failed=1, detail=f"{kind} {title}: {str(exc)[:150]}")
        return out
    ChannelPost.objects.filter(pk=post.pk).update(posted_at=now, error="")
    out.update(sent=1, detail=f"{kind}: {title}")
    return out

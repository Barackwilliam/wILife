"""
WhatsApp Channel ya Soka360: video zinazochezwa NDANI ya WhatsApp, zenyewe, kila mechi ikiisha.

    1. Video ya matokeo (yetu wenyewe, news/match_video.py): kila mechi iliyokwisha kwenye mfumo —
       MP4 inapakiwa kwenye channel, kwa hiyo inachezwa bila kutoka WhatsApp.
    2. Highlights RASMI (Highlight za YouTube, news/youtube.py): link ya YouTube yenye kadi (picha + kichwa).
       Video ya mmiliki haipakuliwi wala haipakiwi upya. Kwenye channel WhatsApp hufungua YouTube (si ndani ya
       WhatsApp), kwa hiyo kadi ya link ni KUBWA (picha ya video iliyopakiwa — bridge) ili ivutie kubonyeza.

Kila video ya matokeo inamhimiza msomaji kufuata channel (SOKA360_FOLLOW_URL).

Posti moja kwa kila tick, kwa mpangilio wa muda (iliyokuwa tayari kwanza), angalau SOKA360_GAP_MINUTES
kati ya posti, na kikomo cha siku kwa kila aina. Mechi/video za saa SOKA360_WINDOW_HOURS zilizopita tu
— baada ya kuwasha, za jana zinaingia kwanza.

    WHATSAPP_CHANNEL                link au JID ya channel (namba ya bridge lazima iwe admin)
    SOKA360_POSTS                   true/false (default true)
    SOKA360_GAP_MINUTES             default 10
    SOKA360_RESULTS_PER_DAY         video za matokeo kwa siku (default 15)
    SOKA360_HIGHLIGHTS_PER_DAY      highlights kwa siku (default 20; 0 = zimezimwa)
    SOKA360_FOLLOW_URL              link ya kufuata channel (kwenye maelezo ya kila video)
    SOKA360_WINDOW_HOURS            default 48
"""
import logging
import re
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

def result_key(m):
    from news.scorebat import team_key
    day = m.kickoff.astimezone(dt_timezone.utc).date()
    return f"result:{team_key(m.home)[:40]}:{team_key(m.away)[:40]}:{day}"


def highlight_key(h):
    from news.scorebat import team_key
    return f"highlight:{team_key(h.home)[:40]}:{team_key(h.away)[:40]}:{timezone.localtime(h.date):%Y-%m-%d}"


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
        key = result_key(m)
        if ready <= now and _usable(key):
            out.append((ready, key, m))
    return out


def follow_url():
    from news.digest import channel_link
    return (getattr(settings, "SOKA360_FOLLOW_URL", "") or channel_link()).strip()


# Mechi zenye majina yanayojulikana (kichwa cha posti) na rangi za timu — kwa team_key, maneno kamili
DERBIES = (
    ("EL CLÁSICO", ("barcelona",), ("real madrid",)),
    ("KARIAKOO DERBY", ("simba",), ("young africans", "yanga")),
    ("MANCHESTER DERBY", ("manchester city", "man city"), ("manchester united", "man united", "man utd")),
    ("NORTH LONDON DERBY", ("arsenal",), ("tottenham", "tottenham hotspur", "spurs")),
    ("MERSEYSIDE DERBY", ("liverpool",), ("everton",)),
    ("MADRID DERBY", ("atletico madrid", "atletico"), ("real madrid",)),
    ("DER KLASSIKER", ("bayern munich", "bayern munchen", "bayern"), ("borussia dortmund", "dortmund")),
    ("LE CLASSIQUE", ("paris saint germain", "psg"), ("marseille", "olympique marseille")),
    ("DERBY D'ITALIA", ("juventus",), ("inter", "internazionale", "inter milan")),
    ("DERBY DELLA MADONNINA", ("inter", "internazionale", "inter milan"), ("milan",)),
)
COLORS = (
    (("barcelona",), "🔵🔴"), (("real madrid",), "⚪👑"), (("simba",), "🔴⚪"), (("young africans", "yanga"), "🟢🟡"),
    (("azam",), "🔵⚪"), (("manchester city", "man city"), "🩵"), (("manchester united", "man united", "man utd"), "🔴"),
    (("arsenal",), "🔴⚪"), (("chelsea",), "🔵"), (("liverpool",), "🔴"), (("tottenham", "tottenham hotspur", "spurs"), "⚪"),
    (("everton",), "🔵"), (("atletico madrid", "atletico"), "🔴⚪"), (("bayern munich", "bayern munchen", "bayern"), "🔴"),
    (("borussia dortmund", "dortmund"), "🟡⚫"), (("juventus",), "⚪⚫"), (("inter", "internazionale", "inter milan"), "🔵⚫"),
    (("milan",), "🔴⚫"), (("paris saint germain", "psg"), "🔵🔴"), (("marseille", "olympique marseille"), "⚪🔵"),
)


def _is(name, aliases):
    """Jina lote (bila FC/SC/AC) ni mojawapo — si sehemu tu: 'Inter Miami' si Inter, 'Simba Queens' si Simba."""
    from news.scorebat import team_key
    return team_key(name) in aliases


def derby(home, away):
    for name, a, b in DERBIES:
        if (_is(home, a) and _is(away, b)) or (_is(home, b) and _is(away, a)):
            return name
    return ""


def _color(name):
    return next((c for aliases, c in COLORS if _is(name, aliases)), "")


def headline(home, away, competition, score=None):
    """'🔥 EL CLÁSICO | FULL-TIME 🔥' na '🔵🔴 Barcelona 1–2 Real Madrid ⚪👑'."""
    title = derby(home, away) or (competition or "SOKA").upper()
    top = f"🔥 {title} | {'FULL-TIME' if score else 'HIGHLIGHTS'} 🔥"
    mid = f"{score[0]}–{score[1]}" if score else "vs"
    teams = f"{_color(home)} {home} {mid} {away} {_color(away)}".strip()
    return top, teams


def _footer(kind):
    lines = ["", f"⚽ Usipitwe na {kind} za mechi nyingine!"]
    if follow_url():
        lines += ["", "📲 FOLLOW Soka360 kwa updates za soka kila siku:", follow_url()]
    return lines


def result_caption(m):
    from news.match_video import goals_of
    top, teams = headline(m.home, m.away, m.competition, (m.home_goals, m.away_goals))
    lines = [top, "", teams, ""]
    if m.competition:
        lines.append(f"🏆 Competition: {m.competition}")
    lines.append("🎥 Video: Muhtasari wa mechi — matokeo na wafungaji.")
    goals = goals_of(m)
    if goals:
        lines += ["", "⚽ MAGOLI:"] + [f"{label} {text}".strip() for label, text in goals[:8]]
    lines += ["", "📊 MATOKEO NA TAKWIMU:", _match_url(m)]
    return "\n".join(lines + _footer("matokeo"))


def _post_result(channel, m):
    from core.agent import whatsapp
    from news import match_video
    from news.football import _image
    data = match_video.render(m, home_logo=_image(m.home_crest), away_logo=_image(m.away_crest),
                              comp_logo=_image(m.competition_logo), site=_host())
    whatsapp.send_channel_video(channel, data, result_caption(m))


# ── Highlights rasmi (YouTube) ─────────────────────────────────────────────────

def _highlights(now):
    """Video moja kwa kila mechi (chaneli moja inaweza kupakia 'Magoli' na 'Highlights' za mechi ileile)."""
    from news.models import ChannelPost, Highlight
    from news.scorebat import team_key
    window = now - timedelta(hours=_cfg("SOKA360_WINDOW_HOURS", 48))
    out, seen = [], set()
    for h in Highlight.objects.filter(provider="youtube", date__gte=window).exclude(home="").exclude(away="") \
            .order_by("date"):
        key = highlight_key(h)
        if key in seen or not h.main:
            continue
        seen.add(key)
        if ChannelPost.objects.filter(key=f"highlight:{h.key}").exists():   # posti za kabla ya mpangilio huu
            continue
        if _usable(key):
            out.append((h.date, key, h))
    return out


_TITLE_SCORE = re.compile(r"\s(\d{1,2})\s*[-–]\s*(\d{1,2})\s")
_COMP_WORDS = ("league", "ligi", "cup", "kombe", "premier", "championship", "liga", "serie", "bundesliga",
               "ligue", "confederation", "caf", "uefa", "fifa", "shield", "ngao")


def from_title(title):
    """
    ('0', '0'), 'NBC Premier League' kutoka kichwa rasmi kama
    'Highlights | Dodoma Jiji 0-0 JKT Tanzania | NBC Premier League 27/10/2026' — (None, '') kama havipo.
    """
    m = _TITLE_SCORE.search(f" {title or ''} ")
    score = (m.group(1), m.group(2)) if m else None
    comp = ""
    for part in (title or "").split("|"):
        p = re.sub(r"\s*[-–]?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$", "", part).strip(" -–:")
        if p and not _TITLE_SCORE.search(f" {p} ") and any(w in p.lower() for w in _COMP_WORDS):
            comp = p[:80]
            break
    return score, comp


def highlight_text(h):
    from news import scorebat
    match = scorebat.match_page(h)
    score = None
    if match and match.get("score") and "-" in match["score"]:
        score = tuple(match["score"].split("-", 1))
    competition = (match or {}).get("competition") or (h.competition if h.competition != h.channel else "")
    if not score or not competition:            # mechi isiyo kwenye ratiba yetu: kichwa rasmi cha video kina vyote
        t_score, t_comp = from_title(h.title)
        score, competition = score or t_score, competition or t_comp
    top, teams = headline(h.home, h.away, competition, score)
    lines = [top, "", teams, ""]
    if competition:
        lines.append(f"🏆 Competition: {competition}")
    credit = f" (video rasmi ya {h.channel})" if h.channel else ""
    lines += [f"🎬 Highlights: Tazama matukio muhimu ya mechi hapa chini{credit}.",
              "", "▶️ TAZAMA HIGHLIGHTS:", h.source_url,
              "", "📊 MATOKEO NA TAKWIMU:",
              f"{_site()}{match['url'] if match and match.get('url') else h.get_absolute_url()}"]
    return "\n".join(lines + _footer("highlights"))


def _post_highlight(channel, h):
    from core.agent import whatsapp
    whatsapp.send_channel(channel, highlight_text(h), preview={
        "url": h.source_url, "title": h.title[:200], "description": f"Video rasmi: {h.channel}" if h.channel else "",
        "thumbnail": h.thumbnail})


# ── Kutuma sasa hivi (Admin: kujaribu channel) ─────────────────────────────────

def send_now(obj, kind):
    """
    Tuma posti moja sasa hivi, bila kusubiri tick wala nafasi/kikomo cha siku (vitufe vya Admin).
    Inarekodiwa kama ChannelPost ileile ambayo kazi ya automatic ingetumia, ili isitumwe tena.
    Inarudisha (imefaulu, ujumbe wa kuonyesha).
    """
    from news.models import ChannelPost
    channel = getattr(settings, "WHATSAPP_CHANNEL", "").strip()
    if not channel:
        return False, "WHATSAPP_CHANNEL haijawekwa kwenye Render"
    if not (settings.AGENT_ENABLED and settings.WHATSAPP_ENABLED):
        return False, "WhatsApp imezimwa (AGENT_ENABLED / WHATSAPP_ENABLED)"
    if kind == "result" and (obj.home_goals is None or obj.away_goals is None or not obj.kickoff):
        return False, f"{obj.home} vs {obj.away}: mechi haina matokeo bado"
    key = result_key(obj) if kind == "result" else highlight_key(obj)
    title = f"{obj.home} vs {obj.away}"[:200]
    post, _ = ChannelPost.objects.get_or_create(key=key, defaults={"kind": kind, "title": title})
    ChannelPost.objects.filter(pk=post.pk).update(tries=post.tries + 1)
    try:
        (_post_result if kind == "result" else _post_highlight)(channel, obj)
    except Exception as exc:
        log.warning("soka360 send_now %s failed: %s", key, exc)
        ChannelPost.objects.filter(pk=post.pk).update(error=str(exc)[:300])
        return False, f"{title}: imeshindwa — {str(exc)[:200]}"
    ChannelPost.objects.filter(pk=post.pk).update(posted_at=timezone.now(), error="")
    return True, f"{title}: imetumwa kwenye channel"


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
    if _posted_today("highlight", now) < _cfg("SOKA360_HIGHLIGHTS_PER_DAY", 20):
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

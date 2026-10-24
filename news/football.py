"""
LIVE ya kujiendesha: mechi za kimataifa kutoka API-Football (api-sports.io).

Kila tick ya wakala (core/agent/jobs.py) inaita `run()`:

  1. Ratiba — mara moja kila saa API_FOOTBALL_SCHEDULE_HOURS (ombi 1): mechi za leo za ligi
     zilizochaguliwa (API_FOOTBALL_LEAGUES). Kila mechi inapata makala yake mapema ("Arsenal vs
     Chelsea LIVE: …") ili Google iijue kabla ya mechi — watu wanatafuta kabla ya filimbi.
  2. Moja kwa moja — mechi ikiwa uwanjani, kila API_FOOTBALL_LIVE_SECONDS (ombi 1 kwa mechi
     zote, hadi 20): magoli, kadi, mabadiliko na VAR yanakuwa masasisho ya Kiswahili, matokeo
     yanabadilika, na mechi ikiisha kichwa kinakuwa "Arsenal 2-1 Chelsea: Matokeo…".

Kikomo cha maombi kwa siku (API_FOOTBALL_DAILY_LIMIT) kinalindwa hapa: ufunguo wa bure una
maombi 100 tu kwa siku. Hali yote inaonekana /agent/channels/ na kwenye FootballState (Admin).
"""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from news import indexnow, live
from news.models import Article, Category, FootballState, LiveMatch

log = logging.getLogger("news.football")

API = "https://v3.football.api-sports.io"
MAX_IDS = 20              # API inakubali hadi mechi 20 kwa ombi moja la `ids`
RESERVE = 2               # maombi ya akiba yasiyotumika kamwe
AFRICA = {"Algeria", "Angola", "Botswana", "Burundi", "Cameroon", "Congo", "Congo-DR", "DR Congo", "Egypt",
          "Ethiopia", "Ghana", "Ivory-Coast", "Kenya", "Libya", "Malawi", "Mali", "Morocco", "Mozambique",
          "Namibia", "Nigeria", "Rwanda", "Senegal", "South-Africa", "Sudan", "Tunisia", "Uganda", "Zambia",
          "Zimbabwe", "Africa"}


class QuotaExhausted(Exception):
    pass


def key():
    return getattr(settings, "API_FOOTBALL_KEY", "")


def leagues():
    return [int(x) for x in getattr(settings, "API_FOOTBALL_LEAGUES", []) if str(x).strip().isdigit()]


def _state(now=None):
    day = (now or timezone.now()).astimezone(dt_timezone.utc).date()
    state, _ = FootballState.objects.get_or_create(day=day)
    return state


def _get(path, params, state):
    """Ombi moja kwa API, likihesabiwa. Linarudisha orodha ya `response`."""
    limit = getattr(settings, "API_FOOTBALL_DAILY_LIMIT", 100)
    if state.requests >= limit - RESERVE or (state.remaining is not None and state.remaining <= RESERVE):
        raise QuotaExhausted(f"maombi {state.requests}/{limit} ya leo yameisha")
    FootballState.objects.filter(pk=state.pk).update(requests=state.requests + 1)
    state.requests += 1
    r = requests.get(f"{API}{path}", params=params, headers={"x-apisports-key": key()}, timeout=15)
    remaining = r.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None and remaining.lstrip("-").isdigit():
        state.remaining = int(remaining)
    r.raise_for_status()
    data = r.json()
    errors = data.get("errors")
    if errors:   # API inarudisha 200 hata kwa kosa (ufunguo, mpango, msimu) — kosa liko hapa
        msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if isinstance(errors, dict) else str(errors)
        raise RuntimeError(msg[:300])
    return data.get("response") or []


# ── Makala ─────────────────────────────────────────────────────────────────────

def _category(league):
    country = (league.get("country") or "").strip()
    name = (league.get("name") or "").lower()
    if country == "Tanzania":
        return Category.TANZANIA
    if country in AFRICA or "caf" in name or "africa" in name:
        return Category.AFRIKA
    return Category.DUNIA


def _local(dt):
    return timezone.localtime(dt) if dt else None


SW_DAYS = ["Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa", "Jumamosi", "Jumapili"]


def _when(dt):
    d = _local(dt)
    return f"{SW_DAYS[d.weekday()]}, {d:%d/%m/%Y} saa {d:%H:%M}"


def preview_title(m):
    return f"{m.home} vs {m.away} LIVE: Matokeo na Matukio Yote — {m.competition}"[:200]


def final_title(m):
    return f"{m.home} {m.home_goals}-{m.away_goals} {m.away}: Matokeo, Magoli na Matukio Yote — {m.competition}"[:200]


def _preview_body(m):
    where = f" kwenye uwanja wa {m.venue}" + (f", {m.city}" if m.city else "") if m.venue else ""
    rnd = f" ({m.round})" if m.round else ""
    return (
        f"{m.home} watakuwa wenyeji wa {m.away} katika mchezo wa {m.competition}{rnd}. "
        f"Mechi imepangwa kuanza {_when(m.kickoff)} kwa saa za Afrika Mashariki{where}.\n\n"
        "## Jinsi ya kufuatilia mechi hii\n\n"
        "Ukurasa huu unasasishwa wenyewe mechi ikianza: kila goli, kadi, mabadiliko ya wachezaji na uamuzi "
        "wa VAR unaonekana hapa dakika kwa dakika bila kurefresh. Matokeo ya mwisho na matukio yote "
        "yanabaki hapa baada ya filimbi ya mwisho.\n\n"
        f"Mechi zote zinazoendelea sasa ziko kwenye ukurasa wa LIVE wa wILife."
    )


def _final_body(m, article):
    goals = [u for u in article.live_updates.filter(kind="goal").order_by("created_at", "id")]
    lines = [f"Mechi ya {m.competition} kati ya {m.home} na {m.away} imemalizika kwa matokeo ya "
             f"{m.home} {m.home_goals}-{m.away_goals} {m.away}."]
    if goals:
        lines.append("## Magoli\n\n" + "\n\n".join(f"{u.label} {u.text.lstrip('⚽ ')}" for u in goals))
    lines.append(_preview_body(m).split("\n\n", 1)[1])
    return "\n\n".join(lines)


def _create_article(m, league):
    article = Article.objects.create(
        title=preview_title(m), category=_category(league), origin="football",
        excerpt=(f"Fuatilia {m.home} dhidi ya {m.away} moja kwa moja: magoli, kadi na matokeo ya "
                 f"{m.competition} dakika kwa dakika kwenye wILife.")[:300],
        body=_preview_body(m),
        keywords=", ".join([m.home, m.away, f"{m.home} vs {m.away}", m.competition, "matokeo", "live"])[:300],
        sources=[{"title": "Takwimu za mechi", "url": "https://www.api-football.com/", "publisher": "API-Football"}],
        status="published", published_at=timezone.now())
    try:
        article.render_cover()
        article.save()
    except Exception:
        pass
    return article


# ── Ratiba ─────────────────────────────────────────────────────────────────────

def _fill(m, item):
    """Data ya API -> LiveMatch (bila kuhifadhi). Inarudisha hali ya zamani."""
    fx, lg, tm, gl = item["fixture"], item.get("league", {}), item.get("teams", {}), item.get("goals", {})
    old = m.status
    m.fixture_id = fx["id"]
    m.competition = (lg.get("name") or "")[:80]
    m.competition_logo = (lg.get("logo") or "")[:300]
    m.round = (lg.get("round") or "")[:80]
    m.home = (tm.get("home", {}).get("name") or "Wenyeji")[:80]
    m.away = (tm.get("away", {}).get("name") or "Wageni")[:80]
    m.home_logo = (tm.get("home", {}).get("logo") or "")[:300]
    m.away_logo = (tm.get("away", {}).get("logo") or "")[:300]
    m.home_goals, m.away_goals = gl.get("home"), gl.get("away")
    st = fx.get("status", {})
    m.status = (st.get("short") or "NS")[:5]
    m.minute = st.get("elapsed")
    if fx.get("date"):
        m.kickoff = datetime.fromisoformat(fx["date"].replace("Z", "+00:00"))
    venue = fx.get("venue") or {}
    m.venue, m.city = (venue.get("name") or "")[:120], (venue.get("city") or "")[:80]
    return old


def sync_schedule(state, now):
    today = _local(now).date()
    items = _get("/fixtures", {"date": today.isoformat(), "timezone": settings.TIME_ZONE}, state)
    wanted = leagues()
    rank = {lid: i for i, lid in enumerate(wanted)}
    items = sorted((i for i in items if i.get("league", {}).get("id") in rank),
                   key=lambda i: (rank[i["league"]["id"]], i["fixture"].get("date", "")))
    cap = getattr(settings, "API_FOOTBALL_MAX_PER_DAY", 12)
    start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    made = Article.objects.filter(origin="football", created_at__gte=start).count()
    created = 0
    for item in items:
        m = LiveMatch.objects.filter(fixture_id=item["fixture"]["id"]).select_related("article").first()
        if m:
            if m.article.status == "published":     # LIVE iliyoondolewa na mhariri haiguswi tena
                apply(item, m)
            continue
        if made >= cap:
            continue
        m = LiveMatch(article=None)
        _fill(m, item)
        if m.status in LiveMatch.FINISHED | LiveMatch.OFF:
            continue                  # mechi iliyokwisha kabla hatujaiona haina LIVE ya kuonyesha
        with transaction.atomic():
            m.article = _create_article(m, item.get("league", {}))
            m.save()
        indexnow.submit([m.article.get_absolute_url()])
        made += 1
        created += 1
    state.schedule_at = now
    state.save(update_fields=["schedule_at", "requests", "remaining"])
    return created


# ── Moja kwa moja ──────────────────────────────────────────────────────────────

def _label(ev):
    t = ev.get("time") or {}
    el, ex = t.get("elapsed"), t.get("extra")
    return f"{el}+{ex}'" if el and ex else (f"{el}'" if el else "")


def event_key(ev):
    t, team, player = ev.get("time") or {}, ev.get("team") or {}, ev.get("player") or {}
    return "|".join(str(x) for x in (t.get("elapsed"), t.get("extra"), ev.get("type"), ev.get("detail"),
                                     team.get("id"), player.get("id") or player.get("name")))


def event_text(ev, m):
    """Tukio la API -> (aina, maandishi ya Kiswahili), au None kama halina maana kwa msomaji."""
    typ, detail = (ev.get("type") or "").lower(), ev.get("detail") or ""
    team = (ev.get("team") or {}).get("name") or ""
    player = (ev.get("player") or {}).get("name") or "Mchezaji"
    assist = (ev.get("assist") or {}).get("name") or ""
    score = f" ({m.home} {m.home_goals}-{m.away_goals} {m.away})" if m.has_score else ""
    if typ == "goal":
        if detail == "Missed Penalty":
            return "miss", f"❌ {player} ({team}) akosa penalti."
        if detail == "Own Goal":
            return "goal", f"⚽ GOLI! {player} anajifunga — goli kwa {team}.{score}"
        how = " kwa penalti" if detail == "Penalty" else ""
        helper = f" Pasi: {assist}." if assist and not how else ""
        return "goal", f"⚽ GOLI! {player} anaifungia {team}{how}.{helper}{score}"
    if typ == "card":
        if "Red" in detail or "Second Yellow" in detail:
            extra = " (njano ya pili)" if "Second Yellow" in detail else ""
            return "red", f"🟥 Kadi nyekundu kwa {player} ({team}){extra}."
        return "card", f"🟨 Kadi ya njano kwa {player} ({team})."
    if typ == "subst":
        return "sub", f"🔁 Mabadiliko {team}: {player} ↔ {assist}." if assist else f"🔁 Mabadiliko {team}: {player}."
    if typ == "var":
        return "var", f"📺 VAR: {detail} ({team}, {player})."
    return None


def _whistle(article, m, old):
    """Masasisho ya hali ya mechi: imeanza, mapumziko, kipindi cha pili, imeisha."""
    if old == m.status:
        return
    if old == "NS" and m.status in LiveMatch.PLAYING:
        live.start(article)
        live.add(article, f"📣 Mechi imeanza! {m.home} dhidi ya {m.away}.", "1'", kind="whistle")
    elif m.status == "HT":
        live.add(article, f"📣 Mapumziko: {m.score_text}.", "HT", kind="whistle")
    elif old == "HT" and m.status == "2H":
        live.add(article, "📣 Kipindi cha pili kimeanza.", "46'", kind="whistle")
    elif m.status in ("ET",) and old != "ET":
        live.add(article, "📣 Muda wa ziada umeanza.", "90'", kind="whistle")
    elif m.status == "P" and old != "P":
        live.add(article, "📣 Mikwaju ya penalti inaanza.", "120'", kind="whistle")


def _finish(article, m):
    if m.status in LiveMatch.FINISHED:
        live.add(article, f"🏁 Mechi imeisha: {m.score_text}.", "FT", kind="whistle")
        article.title = final_title(m)
        article.excerpt = (f"{m.score_text} ({m.competition}). Magoli, kadi na matukio yote ya mechi "
                           f"dakika kwa dakika kwenye wILife.")[:300]
        article.body = _final_body(m, article)
        article.save(update_fields=["title", "excerpt", "body", "updated_at"])
    else:
        live.add(article, f"⚠️ Mechi {m.get_status_display().lower()}.", "", kind="whistle")
    live.end(article)


def apply(item, m=None):
    """Data moja ya mechi kutoka API -> ubao, masasisho mapya na hali. Inarudisha masasisho mapya."""
    m = m or LiveMatch.objects.select_related("article").get(fixture_id=item["fixture"]["id"])
    article = m.article
    old = _fill(m, item)
    m.synced_at = timezone.now()
    m.save()
    _whistle(article, m, old)
    if article.live_started_at and not article.live_ended_at:
        score = m.score_text
        if score != article.live_score:
            article.live_score = score
            article.save(update_fields=["live_score", "updated_at"])
    done = m.status in LiveMatch.FINISHED | LiveMatch.OFF
    if done and not article.live_started_at and m.is_finished:
        live.start(article)          # tick haikuiona ikichezwa — bado ipate magoli na matokeo ya mwisho
    seen, new = set(m.seen_events), 0
    for ev in sorted(item.get("events") or [], key=lambda e: ((e.get("time") or {}).get("elapsed") or 0,
                                                              (e.get("time") or {}).get("extra") or 0)):
        k = event_key(ev)
        if k in seen:
            continue
        seen.add(k)
        out = event_text(ev, m)
        if out and article.live_started_at:
            live.add(article, out[1], _label(ev), kind=out[0])
            new += 1
    m.seen_events = sorted(seen)
    m.save(update_fields=["seen_events"])
    if done and article.live_started_at and not article.live_ended_at:
        _finish(article, m)
    return new


def active(now):
    """Mechi zinazohitaji kuangaliwa sasa: zimeanza (au zinakaribia) na bado hazijaisha."""
    return list(LiveMatch.objects.select_related("article").filter(
        fixture_id__isnull=False, article__status="published", kickoff__lte=now + timedelta(minutes=3), kickoff__gte=now - timedelta(hours=4))
        .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF).order_by("kickoff")[:MAX_IDS])


def sync_live(state, now):
    matches = active(now)
    if not matches:
        return 0
    items = _get("/fixtures", {"ids": "-".join(str(m.fixture_id) for m in matches),
                               "timezone": settings.TIME_ZONE}, state)
    by_id = {m.fixture_id: m for m in matches}
    new = 0
    for item in items:
        m = by_id.get(item.get("fixture", {}).get("id"))
        if m:
            new += apply(item, m)
    state.live_at = now
    state.save(update_fields=["live_at", "requests", "remaining"])
    return new


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick ya wakala."""
    out = {"job": "football", "sent": 0, "failed": 0, "detail": ""}
    if not key() or not leagues():
        out["detail"] = "API_FOOTBALL_KEY haijawekwa"
        return out
    if dry_run:
        out["detail"] = "dry run"
        return out
    now = now or timezone.now()
    state = _state(now)
    notes = []
    try:
        every = timedelta(hours=getattr(settings, "API_FOOTBALL_SCHEDULE_HOURS", 6))
        if not state.schedule_at or now - state.schedule_at >= every:
            notes.append(f"ratiba: mpya {sync_schedule(state, now)}")
        live_every = timedelta(seconds=getattr(settings, "API_FOOTBALL_LIVE_SECONDS", 120))
        if active(now) and (not state.live_at or now - state.live_at >= live_every):
            n = sync_live(state, now)
            out["sent"] = n
            notes.append(f"live: masasisho {n}")
        if state.error:
            state.error = ""
            state.save(update_fields=["error"])
    except QuotaExhausted as exc:
        notes.append(str(exc))
    except Exception as exc:          # API au mtandao — tick iendelee, kosa lionekane
        log.warning("football sync failed: %s", exc)
        state.error = str(exc)[:300]
        state.save(update_fields=["error", "requests", "remaining"])
        out["failed"] = 1
        notes.append(f"kosa: {exc}")
    out["detail"] = "; ".join(notes) or "hakuna kazi"
    return out

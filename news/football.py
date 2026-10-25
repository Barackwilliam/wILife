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
from django.db.models import Q
from django.utils import timezone

from news import covers, indexnow, live
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


class PlanError(RuntimeError):
    """Mpango wa API hauruhusu njia hii (mf. mpango wa bure hauruhusu `ids`)."""


def key():
    return getattr(settings, "API_FOOTBALL_KEY", "")


def leagues():
    return [int(x) for x in getattr(settings, "API_FOOTBALL_LEAGUES", []) if str(x).strip().isdigit()]


def _state(now=None):
    day = (now or timezone.now()).astimezone(dt_timezone.utc).date()
    state, created = FootballState.objects.get_or_create(day=day)
    if created:   # njia iliyofanya kazi jana (mf. mpango wa bure: 'live') — usipoteze ombi kuijaribu upya
        prev = FootballState.objects.filter(day__lt=day).exclude(live_mode="").order_by("-day").first()
        if prev:
            state.live_mode = prev.live_mode
            state.save(update_fields=["live_mode"])
    return state


def provider():
    """Mtoaji wa data anayetumika: Highlightly (ukiwa na ufunguo wake), vinginevyo API-Football."""
    from news import highlightly
    if highlightly.key() and highlightly.rules():
        return "highlightly"
    if key() and leagues():
        return "api-football"
    return ""


def daily_limit():
    if provider() == "highlightly":
        return getattr(settings, "HIGHLIGHTLY_DAILY_LIMIT", 100)
    return getattr(settings, "API_FOOTBALL_DAILY_LIMIT", 100)


def spend(state):
    """Hesabu ombi moja kabla ya kulituma — kikomo cha siku hakivukwi kamwe."""
    limit = daily_limit()
    if state.requests >= limit - RESERVE or (state.remaining is not None and state.remaining <= RESERVE):
        raise QuotaExhausted(f"maombi {state.requests}/{limit} ya leo yameisha")
    FootballState.objects.filter(pk=state.pk).update(requests=state.requests + 1)
    state.requests += 1


def _get(path, params, state):
    """Ombi moja kwa API-Football, likihesabiwa. Linarudisha orodha ya `response`."""
    spend(state)
    r = requests.get(f"{API}{path}", params=params, headers={"x-apisports-key": key()}, timeout=15)
    remaining = r.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None and remaining.lstrip("-").isdigit():
        state.remaining = int(remaining)
    r.raise_for_status()
    data = r.json()
    errors = data.get("errors")
    if errors:   # API inarudisha 200 hata kwa kosa (ufunguo, mpango, msimu) — kosa liko hapa
        msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if isinstance(errors, dict) else str(errors)
        if isinstance(errors, dict) and "plan" in errors:
            raise PlanError(msg[:300])
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
    return f"{m.home} dhidi ya {m.away} LIVE: Matokeo, Kikosi na Takwimu — {m.competition}"[:200]


def final_title(m):
    return f"{m.home} {m.home_goals}-{m.away_goals} {m.away}: Matokeo, Magoli na Takwimu — {m.competition}"[:200]


HOWTO_SOON = ("Ukurasa huu hujisasisha mechi inapoanza. Kila goli, kadi, mabadiliko ya wachezaji na uamuzi "
              "wa VAR utaonekana hapa dakika baada ya dakika bila kupakia ukurasa upya. Matokeo ya mwisho na "
              "matukio yote yanabaki hapa baada ya filimbi ya mwisho.")
HOWTO_LIVE = ("Mechi inaendelea sasa na ukurasa huu unajisasisha wenyewe: kila goli, kadi, mabadiliko ya wachezaji "
              "na uamuzi wa VAR unaonekana hapa dakika baada ya dakika bila kupakia ukurasa upya.")


def _where(m):
    return f", kwenye uwanja wa {m.venue}" + (f", {m.city}" if m.city else "") if m.venue else ""


def _preview_intro(m):
    rnd = f" ({m.round})" if m.round else ""
    return (f"{m.home} watakuwa wenyeji wa {m.away} katika mchezo wa {m.competition}{rnd}. "
            f"Mechi itaanza {_when(m.kickoff)} kwa saa za Afrika Mashariki{_where(m)}.")


def _live_intro(m):
    rnd = f" ({m.round})" if m.round else ""
    return (f"{m.home} ni wenyeji wa {m.away} katika mchezo wa {m.competition}{rnd} unaoendelea sasa. "
            f"Mechi ilianza {_when(m.kickoff)} kwa saa za Afrika Mashariki{_where(m)}.")


def _preview_body(m):
    return (
        f"{_preview_intro(m)}\n\n"
        "## Jinsi ya kufuatilia mechi hii\n\n"
        f"{HOWTO_SOON}\n\n"
        f"Mechi zote zinazoendelea sasa ziko kwenye ukurasa wa LIVE wa wILife."
    )


def _live_body(m, body):
    """Mechi imeanza: maneno ya "itaanza" yanakuwa "inaendelea" (uchambuzi wa kabla ya mechi unabaki)."""
    return body.replace(_preview_intro(m), _live_intro(m), 1).replace(HOWTO_SOON, HOWTO_LIVE, 1)


def _final_body(m, article):
    goals = [u for u in article.live_updates.filter(kind="goal").order_by("created_at", "id")]
    lines = [f"Mechi ya {m.competition} kati ya {m.home} na {m.away} imekamilika kwa matokeo ya "
             f"{m.home} {m.home_goals}-{m.away_goals} {m.away}."]
    rnd = f" ({m.round})" if m.round else ""
    lines.append(f"Mchezo huo wa {m.competition}{rnd} ulichezwa {_when(m.kickoff)} kwa saa za Afrika "
                 f"Mashariki{_where(m)}.")
    if goals:
        lines.append("## Magoli\n\n" + "\n\n".join(f"{u.label} {u.text.lstrip('⚽ ')}" for u in goals))
    lines.append("## Matukio ya mechi\n\nMatukio yote ya mechi hii — magoli, kadi, mabadiliko ya wachezaji na "
                 "maamuzi ya VAR — yameorodheshwa hapa juu kwa mpangilio wa dakika, pamoja na takwimu za mechi.")
    return "\n\n".join(lines)


def _image(url):
    """Nembo kutoka API (media.api-sports.io) — bytes, au None ikishindikana (picha inatumia herufi za mwanzo)."""
    if not (url or "").startswith("https://"):
        return None
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def match_cover(m):
    """(png ya kushirikisha, webp ya kadi) zenye nembo halisi za timu, mashindano na saa / matokeo."""
    home, away, comp = _image(m.home_logo), _image(m.away_logo), _image(m.competition_logo)
    score = f"{m.home_goals} - {m.away_goals}" if m.has_score and (m.is_playing or m.is_finished) else None
    when = "" if m.is_finished else (_when(m.kickoff) if m.kickoff else "")
    kw = dict(home=m.home, away=m.away, home_logo=home, away_logo=away, competition=m.competition,
              comp_logo=comp, when_text=when, score=score, live=m.is_playing)
    png = covers.render_match(**kw)
    art = covers.thumbnail(covers.render_match(**kw, with_text=False))
    return png, art


def refresh_cover(m):
    """Tengeneza upya picha ya mechi (imeundwa, imeanza, imeisha) na uihifadhi."""
    article = m.article
    if article.cover_original:
        return
    try:
        article.render_cover()
    except Exception as exc:          # picha isizuie LIVE
        log.warning("match cover failed for %s: %s", m, exc)
        return
    article.save(update_fields=["cover_image", "cover_art", "cover_png", "cover_thumb", "updated_at"])
    LiveMatch.objects.filter(pk=m.pk).update(cover_ok=True)
    m.cover_ok = True


SOURCES = {
    "api-football": {"title": "Takwimu za mechi", "url": "https://www.api-football.com/", "publisher": "API-Football"},
    "highlightly": {"title": "Takwimu za mechi", "url": "https://highlightly.net/", "publisher": "Highlightly"},
}


def _create_article(m, league):
    article = Article.objects.create(
        title=preview_title(m), category=_category(league), origin="football",
        excerpt=(f"Fuatilia {m.home} dhidi ya {m.away} moja kwa moja: magoli, kadi na matokeo ya "
                 f"{m.competition} dakika kwa dakika kwenye wILife.")[:300],
        body=_preview_body(m),
        keywords=", ".join([m.home, m.away, f"{m.home} vs {m.away}", m.competition, "matokeo", "live"])[:300],
        sources=[SOURCES.get(provider(), SOURCES["api-football"])],
        status="published", published_at=timezone.now())
    return article          # picha (nembo za timu) inatengenezwa mechi ikishahifadhiwa — refresh_cover


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
    if venue.get("name"):                   # orodha ya ratiba haina uwanja; maelezo ya mechi yanayo
        m.venue, m.city = (venue.get("name") or "")[:120], (venue.get("city") or "")[:80]
    if item.get("_extra"):
        m.extra = {**(m.extra or {}), **item["_extra"]}
    return old


def _favourite(item):
    from news import highlightly
    tm = item.get("teams") or {}
    return highlightly.is_favourite((tm.get("home") or {}).get("name"), (tm.get("away") or {}).get("name"))


def _item_from_fixture(f):
    """Fixture iliyohifadhiwa -> muundo wa API (bila ombi jipya)."""
    return {"fixture": {"id": f.fixture_id, "date": f.kickoff.isoformat() if f.kickoff else None,
                        "status": {"short": f.status, "elapsed": f.minute}, "venue": {}},
            "league": {"id": f.competition_id, "name": f.competition, "logo": f.competition_logo,
                       "round": f.round, "country": f.country},
            "teams": {"home": {"name": f.home, "logo": f.home_logo}, "away": {"name": f.away, "logo": f.away_logo}},
            "goals": {"home": f.home_goals, "away": f.away_goals}}


def promote_favourites(now):
    """
    Mechi ya timu zetu iliyo kwenye ratiba bila LIVE (mf. ratiba ilisomwa kabla timu haijawa kwenye orodha,
    au nafasi 5 za siku zilikuwa zimejaa): ipate LIVE sasa, kutoka data iliyopo — hakuna ombi la API.
    Inarudisha idadi ya mechi zilizopata LIVE.
    """
    from news import highlightly
    from news.models import Fixture
    rows = Fixture.objects.filter(provider=provider(), kickoff__gte=now - timedelta(hours=3),
                                  kickoff__lte=now + timedelta(hours=36)).exclude(
        status__in=LiveMatch.FINISHED | LiveMatch.OFF)
    made = 0
    for f in rows:
        if not highlightly.is_favourite(f.home, f.away):
            continue
        if f.rank != 0:
            Fixture.objects.filter(pk=f.pk).update(rank=0)     # juu kabisa kwenye ukurasa wa siku
        if LiveMatch.objects.filter(provider=provider(), fixture_id=f.fixture_id).exists():
            continue
        item = _item_from_fixture(f)
        m = LiveMatch(article=None, provider=provider())
        _fill(m, item)
        with transaction.atomic():
            m.article = _create_article(m, item["league"])
            m.save()
        refresh_cover(m)
        indexnow.submit([m.article.get_absolute_url()])
        made += 1
    return made


def max_per_day():
    """Mechi ngapi kwa siku. Mpango wa bure (maombi 100) unatosha mechi chache tu zenye matukio kamili."""
    cap = getattr(settings, "API_FOOTBALL_MAX_PER_DAY", None)
    if cap:
        return cap
    return 5 if daily_limit() <= 100 else 20


def sync_schedule(state, now):
    today = _local(now).date()
    if provider() == "highlightly":
        from news import highlightly
        items = highlightly.schedule(state, today)        # tayari zimechujwa na kupangwa
    else:
        items = _get("/fixtures", {"date": today.isoformat(), "timezone": settings.TIME_ZONE}, state)
        rank = {lid: i for i, lid in enumerate(leagues())}
        items = sorted((i for i in items if i.get("league", {}).get("id") in rank),
                       key=lambda i: (rank[i["league"]["id"]], i["fixture"].get("date", "")))
        for i in items:
            i["_rank"] = rank[i["league"]["id"]]
    from news import fixtures
    fixtures.store(items, today)                       # ratiba nzima ya siku: "Mechi za leo" (hakuna ombi la ziada)
    cap = max_per_day()
    start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    made = Article.objects.filter(origin="football", created_at__gte=start).count()
    created = 0
    for item in items:
        m = (LiveMatch.objects.filter(fixture_id=item["fixture"]["id"], provider=provider())
             .select_related("article").first())
        if m:
            if m.article.status == "published":     # LIVE iliyoondolewa na mhariri haiguswi tena
                apply(item, m)
            continue
        if made >= cap and not _favourite(item):
            continue                  # mechi za timu zetu (Simba, Yanga...) zinapata LIVE hata kikomo kikifika
        m = LiveMatch(article=None, provider=provider())
        _fill(m, item)
        if m.status in LiveMatch.FINISHED | LiveMatch.OFF:
            continue                  # mechi iliyokwisha kabla hatujaiona haina LIVE ya kuonyesha
        with transaction.atomic():
            m.article = _create_article(m, item.get("league", {}))
            m.save()
        refresh_cover(m)
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
            return "miss", f"❌ {player} wa {team} amekosa penalti."
        if detail == "Own Goal":
            return "goal", f"⚽ GOLI! {player} amejifunga na kuipa {team} goli.{score}"
        how = " kwa mkwaju wa penalti" if detail == "Penalty" else ""
        helper = f" Pasi ya mwisho: {assist}." if assist and not how else ""
        return "goal", f"⚽ GOLI! {player} ameifungia {team}{how}.{helper}{score}"
    if typ == "card":
        if "Red" in detail or "Second Yellow" in detail:
            extra = " baada ya kupata kadi ya pili ya njano" if "Second Yellow" in detail else ""
            return "red", f"🟥 Kadi nyekundu ameonyeshwa {player} ({team}){extra}."
        return "card", f"🟨 Kadi ya njano ameonyeshwa {player} ({team})."
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
        body = _live_body(m, article.body)
        if body != article.body:
            article.body = body
            article.save(update_fields=["body", "updated_at"])
        live.start(article)
        live.add(article, f"📣 Mechi imeanza! {m.home} dhidi ya {m.away}.", "1'", kind="whistle")
        refresh_cover(m)                       # picha inapata alama ya LIVE
    elif m.status == "HT":
        live.add(article, f"📣 Mapumziko ya kipindi cha kwanza: {m.score_text}.", "HT", kind="whistle")
    elif old == "HT" and m.status == "2H":
        live.add(article, "📣 Kipindi cha pili kimeanza.", "46'", kind="whistle")
    elif m.status in ("ET",) and old != "ET":
        live.add(article, "📣 Muda wa nyongeza umeanza.", "90'", kind="whistle")
    elif m.status == "P" and old != "P":
        live.add(article, "📣 Mikwaju ya penalti inaanza.", "120'", kind="whistle")


def _finish(article, m):
    if m.status in LiveMatch.FINISHED:
        live.add(article, f"🏁 Mechi imekamilika: {m.score_text}.", "FT", kind="whistle")
        article.title = final_title(m)
        article.excerpt = (f"{m.score_text} ({m.competition}). Magoli, kadi na matukio yote ya mechi "
                           f"dakika kwa dakika kwenye wILife.")[:300]
        article.body = _final_body(m, article)
        article.save(update_fields=["title", "excerpt", "body", "updated_at"])
        refresh_cover(m)                       # picha inaonyesha matokeo ya mwisho
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
    if m.is_finished:
        from news import predict
        predict.settle(m)                     # makadirio yalipatia? (rekodi ya wazi kwenye /utabiri/)
    return new


def active(now):
    """Mechi zinazohitaji kuangaliwa sasa: zimeanza (au zinakaribia) na bado hazijaisha."""
    return list(LiveMatch.objects.select_related("article").filter(
        fixture_id__isnull=False, provider=provider(), article__status="published", kickoff__lte=now + timedelta(minutes=3), kickoff__gte=now - timedelta(hours=4))
        .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF).order_by("kickoff")[:MAX_IDS])


def _by_ids(state, matches):
    """Ombi 1 kwa mechi zote (hadi 20). Mipango ya kulipia tu."""
    return _get("/fixtures", {"ids": "-".join(str(m.fixture_id) for m in matches),
                              "timezone": settings.TIME_ZONE}, state)


def _by_id(state, m):
    return _get("/fixtures", {"id": m.fixture_id, "timezone": settings.TIME_ZONE}, state)


def _by_live(state, matches):
    """
    Ombi 1: mechi zinazochezwa sasa kwenye ligi zetu. Mechi iliyokuwa uwanjani na sasa haipo
    kwenye orodha imeisha (au imesimamishwa) — inaulizwa peke yake mara moja kupata matokeo ya mwisho.
    """
    items = _get("/fixtures", {"live": "-".join(str(x) for x in leagues()), "timezone": settings.TIME_ZONE}, state)
    seen = {i.get("fixture", {}).get("id") for i in items}
    by_id = {m.fixture_id: m for m in matches}
    now = timezone.now()
    # Orodha ya 'live' haina matukio (wafungaji, kadi). Mechi ambayo magoli au hali yake imebadilika
    # inaulizwa kwa id — ombi 1 linaleta matukio yote yaliyotokea tangu mara ya mwisho.
    for i, item in enumerate(list(items)):
        m = by_id.get(item.get("fixture", {}).get("id"))
        if not m or item.get("events"):
            continue
        goals = item.get("goals") or {}
        status = ((item.get("fixture") or {}).get("status") or {}).get("short")
        if (goals.get("home"), goals.get("away")) != (m.home_goals, m.away_goals) or status != m.status:
            full = _by_id(state, m)
            if full:
                items[i] = full[0]
    for m in matches:
        if m.fixture_id in seen:
            continue
        gone = m.status in LiveMatch.PLAYING
        late = m.status == "NS" and m.kickoff and now - m.kickoff > timedelta(minutes=30) and (
            not m.synced_at or now - m.synced_at > timedelta(minutes=20))   # imeahirishwa? angalia mara chache
        if gone or late:
            items += _by_id(state, m)
    return items


def _one_by_one(state, matches):
    """Njia ya mwisho: ombi 1 kwa kila mechi."""
    items = []
    for m in matches:
        items += _by_id(state, m)
    return items


MODES = [("ids", _by_ids), ("live", _by_live), ("id", _one_by_one)]


def sync_live(state, now):
    matches = active(now)
    if not matches:
        return 0
    state.live_at = now                       # muda wa jaribio — kosa lisirudiwe kila dakika
    state.save(update_fields=["live_at"])
    modes = MODES
    if provider() == "highlightly":
        from news import highlightly
        modes = [("hl", lambda st, ms: [i for m in ms for i in highlightly.match(st, m)])]
    names = [n for n, _ in modes]
    start = names.index(state.live_mode) if state.live_mode in names else 0
    for name, fetch in modes[start:]:
        try:
            items = fetch(state, matches)
        except PlanError:
            continue                          # mpango hauruhusu njia hii — jaribu inayofuata
        if state.live_mode != name:
            state.live_mode = name
        break
    else:
        raise RuntimeError("mpango wa API hauruhusu njia yoyote ya kufuatilia mechi zinazochezwa")
    by_id = {m.fixture_id: m for m in matches}
    new = 0
    for item in items:
        m = by_id.get(item.get("fixture", {}).get("id"))
        if m:
            new += apply(item, m)
    state.save(update_fields=["live_at", "live_mode", "requests", "remaining"])
    return new


def finalize(state, now, limit=3):
    """
    Mechi zilizopita muda wa kawaida (zaidi ya saa 4 tangu kuanza) bila matokeo ya mwisho — mf. maombi ya
    siku yaliisha mechi ikiendelea. active() haiziangalii tena, kwa hiyo hapa zinaombwa mara moja moja
    (ombi 1 kila mechi, kila dakika 30) hadi API iseme zimeisha. Inarudisha maombi yaliyotumika.
    """
    from news import highlightly
    if provider() != "highlightly" or daily_limit() - state.requests <= 8:
        return 0
    due = list(LiveMatch.objects.select_related("article").filter(
        fixture_id__isnull=False, provider=provider(), article__status="published",
        kickoff__lt=now - LiveMatch.STALE_AFTER, kickoff__gte=now - timedelta(hours=48))
        .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF)
        .filter(Q(synced_at__isnull=True) | Q(synced_at__lt=now - timedelta(minutes=30))).order_by("kickoff")[:limit])
    spent = 0
    for m in due:
        LiveMatch.objects.filter(pk=m.pk).update(synced_at=now)      # muda wa jaribio — kosa lisirudiwe kila dakika
        spent += 1
        for item in highlightly.match(state, m):
            apply(item, m)
        if not m.is_finished and now - m.kickoff > timedelta(hours=24) and m.article.live_started_at \
                and not m.article.live_ended_at:
            live.end(m.article)        # API bado haina matokeo ya mwisho baada ya siku nzima: LIVE isiendelee
    if spent:
        state.save(update_fields=["requests", "remaining"])
    return spent


def live_interval(state, now):
    """
    Kila ngapi kuangalia mechi zinazochezwa. Si chini ya API_FOOTBALL_LIVE_SECONDS, na maombi
    yaliyobaki leo yanagawanywa mpaka mechi ya mwisho ya leo iishe — mpango wa bure (100)
    usiishe kabla ya mechi za jioni.
    """
    base = getattr(settings, "API_FOOTBALL_LIVE_SECONDS", 120)
    from news.fixtures import OFFLIVE_RESERVE
    # 2: ratiba. OFFLIVE_RESERVE: matokeo ya mechi zisizo na LIVE na kurasa za mechi wasomaji wanazofungua —
    # bila akiba hii LIVE ilitumia maombi yote na mechi nyingine zilibaki "Inasubiri matokeo".
    left = daily_limit() - RESERVE - state.requests - 2 - OFFLIVE_RESERVE
    last = (LiveMatch.objects.filter(fixture_id__isnull=False, provider=provider(), article__status="published",
                                     kickoff__gte=now - timedelta(hours=4))
            .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF)
            .order_by("-kickoff").values_list("kickoff", flat=True).first())
    if not last or left <= 0:
        return timedelta(seconds=base)
    horizon = (last + timedelta(minutes=150) - now).total_seconds()
    per_poll = len(active(now)) if state.live_mode in ("id", "hl") or provider() == "highlightly" else 1
    return timedelta(seconds=max(base, horizon / left * per_poll))


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick ya wakala."""
    out = {"job": "football", "sent": 0, "failed": 0, "detail": ""}
    if not provider():
        out["detail"] = "HIGHLIGHTLY_API_KEY / API_FOOTBALL_KEY haijawekwa"
        return out
    if dry_run:
        out["detail"] = "dry run"
        return out
    now = now or timezone.now()
    state = _state(now)
    notes = []
    try:
        hours = getattr(settings, "API_FOOTBALL_SCHEDULE_HOURS", 6)
        if provider() == "highlightly":          # ratiba ya siku nzima ni kurasa kadhaa — mara chache
            hours = getattr(settings, "HIGHLIGHTLY_SCHEDULE_HOURS", 24)
        every = timedelta(hours=hours)
        if not state.schedule_at or now - state.schedule_at >= every:
            state.schedule_at = now           # muda wa jaribio — kosa lisirudiwe kila dakika
            state.save(update_fields=["schedule_at"])
            notes.append(f"ratiba: mpya {sync_schedule(state, now)}")
            if provider() == "highlightly":
                from news import highlightly
                notes.append(highlightly.last_scan_note())
        n = promote_favourites(now)
        if n:
            notes.append(f"timu zetu: LIVE mpya {n}")
        for m in LiveMatch.objects.select_related("article").filter(
                fixture_id__isnull=False, cover_ok=False, article__status="published")[:3]:
            refresh_cover(m)                   # mechi za zamani zenye picha za mfumo -> nembo za timu
        if active(now) and (not state.live_at or now - state.live_at >= live_interval(state, now)):
            n = sync_live(state, now)
            out["sent"] = n
            notes.append(f"live: masasisho {n}")
        if provider() == "highlightly":       # baada ya mechi zinazochezwa: taarifa za ziada
            from news import enrich
            n = finalize(state, now)
            if n:
                notes.append(f"matokeo ya mwisho yaliyochelewa: maombi {n}")
            n = enrich.run(state, now)
            if n:
                notes.append(f"ziada: maombi {n}")
            # Matokeo ya mechi zisizo na LIVE. Hayasubiri tena LIVE ziishe: siku zenye mechi mfululizo
            # (wikendi) hayakuwahi kufika zamu. live_interval inaacha OFFLIVE_RESERVE kwa ajili yake.
            from news import fixtures
            n = fixtures.rescan_day(state, now, daily_limit() - state.requests)
            if n:
                notes.append(f"matokeo ya jana (ratiba nzima): maombi {n}")
            n = fixtures.refresh_results(state, now, daily_limit() - state.requests)
            if n:
                notes.append(f"matokeo ya ratiba: maombi {n}")
            if not active(now):                 # siku iliyokosa ratiba kabisa si ya haraka: isubiri LIVE ziishe
                n = fixtures.backfill(state, now, daily_limit() - state.requests)
                if n:
                    notes.append(f"ratiba ya siku iliyokosekana: maombi {n}")
        if state.error:
            state.error = ""
            state.save(update_fields=["error"])
    except QuotaExhausted as exc:
        notes.append(str(exc))
        if state.error != str(exc)[:300]:      # ionekane Admin -> Football states
            state.error = str(exc)[:300]
            state.save(update_fields=["error"])
    except Exception as exc:          # API au mtandao — tick iendelee, kosa lionekane
        log.warning("football sync failed: %s", exc)
        state.error = str(exc)[:300]
        state.save(update_fields=["error", "requests", "remaining"])
        out["failed"] = 1
        notes.append(f"kosa: {exc}")
    out["detail"] = "; ".join(notes) or "hakuna kazi"
    return out

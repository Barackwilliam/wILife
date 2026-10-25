"""
Ratiba na matokeo ya siku — kurasa za "Mechi za leo", "Matokeo ya jana" na /mechi/<tarehe>/.

    store()            ratiba ya siku kutoka usomaji wa asubuhi (football.sync_schedule) — bure, hakuna ombi jipya
    refresh_results()  jioni: matokeo ya mechi zisizo na LIVE, ombi 1 kwa kila ligi (si kwa kila mechi)
    day_rows()         mechi za siku kwa ukurasa: Fixture + LiveMatch (LIVE ina data mpya zaidi na ukurasa wake)

Mechi zenye LIVE (football.py) zinasasishwa dakika kwa dakika; nyingine zinapata matokeo jioni.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from news.models import Fixture, LiveMatch

log = logging.getLogger("news.fixtures")

REFRESH_EVERY = timedelta(hours=3)
MATCH_LENGTH = timedelta(minutes=135)      # dakika 90 + mapumziko + nyongeza
MAX_LEAGUES = 6                            # maombi ya juu kabisa kwa usasishaji mmoja


def _set(f, item):
    fx, lg, tm, gl = item["fixture"], item.get("league") or {}, item.get("teams") or {}, item.get("goals") or {}
    st = fx.get("status") or {}
    f.competition = (lg.get("name") or "")[:80]
    f.competition_id = lg.get("id") if str(lg.get("id") or "").lstrip("-").isdigit() else None
    f.competition_logo = (lg.get("logo") or "")[:300]
    f.country = (lg.get("country") or "")[:60] if isinstance(lg.get("country"), str) else ""
    f.round = (lg.get("round") or "")[:80]
    f.home = ((tm.get("home") or {}).get("name") or "Wenyeji")[:80]
    f.away = ((tm.get("away") or {}).get("name") or "Wageni")[:80]
    f.home_logo = ((tm.get("home") or {}).get("logo") or "")[:300]
    f.away_logo = ((tm.get("away") or {}).get("logo") or "")[:300]
    f.home_goals, f.away_goals = gl.get("home"), gl.get("away")
    f.status = (st.get("short") or "NS")[:5]
    f.minute = st.get("elapsed")
    if fx.get("date"):
        f.kickoff = datetime.fromisoformat(fx["date"].replace("Z", "+00:00"))
    if "_rank" in item:
        f.rank = min(int(item["_rank"]), 99)


def store(items, day):
    """Hifadhi/sasisha mechi za siku (muundo wa API-Football / highlightly.normalize). Inarudisha idadi."""
    from news.football import provider
    source, n = provider(), 0
    for item in items:
        try:
            fid = int(item["fixture"]["id"])
        except (KeyError, TypeError, ValueError):
            continue
        f = Fixture.objects.filter(provider=source, fixture_id=fid).first() or Fixture(provider=source, fixture_id=fid)
        _set(f, item)
        f.day = timezone.localtime(f.kickoff).date() if f.kickoff else day
        f.save()
        n += 1
    return n


def _stale(now):
    """Mechi zilizopaswa kuisha lakini hazina matokeo ya mwisho, na hazina LIVE (LIVE inajisasisha)."""
    live_ids = set(LiveMatch.objects.filter(fixture_id__isnull=False).values_list("fixture_id", flat=True))
    rows = Fixture.objects.filter(kickoff__lte=now - MATCH_LENGTH, kickoff__gte=now - timedelta(days=2),
                                  provider="highlightly").exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF)
    return [f for f in rows if f.fixture_id not in live_ids]


def refresh_results(state, now, left):
    """Matokeo ya jioni kwa mechi zisizo na LIVE. Inarudisha maombi yaliyotumika."""
    from django.core.cache import cache
    from news import highlightly
    if cache.get("fixtures:refresh") or left <= 15:
        return 0
    stale = _stale(now)
    if not stale:
        return 0
    cache.set("fixtures:refresh", now.isoformat(), int(REFRESH_EVERY.total_seconds()))
    groups = {}
    for f in stale:
        if f.competition_id:
            groups.setdefault((f.day, f.competition_id), []).append(f)
    spent = 0
    for (day, league_id), rows in sorted(groups.items(), key=lambda kv: min(f.rank for f in kv[1]))[:MAX_LEAGUES]:
        try:
            data = highlightly._get("/matches", {"date": day.isoformat(), "leagueId": league_id,
                                                 "timezone": settings.TIME_ZONE, "limit": 100}, state)
        except Exception as exc:
            log.warning("fixtures refresh failed for league %s: %s", league_id, exc)
            spent += 1
            break
        spent += 1
        wanted = {f.fixture_id: f for f in rows}
        found = data.get("data") if isinstance(data, dict) else data
        for raw in found or []:
            f = wanted.get(raw.get("id")) if isinstance(raw, dict) else None
            if f:                                    # leagueId isipoheshimiwa, tunasasisha tulizoziomba tu
                rank = f.rank
                _set(f, highlightly.normalize(raw))
                f.rank = rank
                f.save()
    return spent


class Row:
    """Mstari mmoja wa ukurasa wa siku — kutoka Fixture au LiveMatch."""
    def __init__(self, src, article=None, rank=99):
        for k in ("competition", "competition_logo", "round", "home", "away", "home_logo", "away_logo",
                  "home_goals", "away_goals", "status", "minute", "kickoff"):
            setattr(self, k, getattr(src, k, None))
        self.venue = getattr(src, "venue", "")
        self.city = getattr(src, "city", "")
        self.article = article
        self.match = src if isinstance(src, LiveMatch) else None
        self.fixture = src if isinstance(src, Fixture) else None
        self.rank = rank
        self.key = f"{getattr(src, 'provider', '')}{getattr(src, 'fixture_id', '') or src.pk}"
        self.fixture_id = getattr(src, "fixture_id", None)

    has_score = property(lambda s: s.home_goals is not None and s.away_goals is not None)
    is_playing = property(lambda s: s.status in LiveMatch.PLAYING and not LiveMatch.stale(s)
                          and not (s.kickoff and LiveMatch.not_started(s)))
    is_finished = property(lambda s: s.status in LiveMatch.FINISHED)
    is_off = property(lambda s: s.status in LiveMatch.OFF)
    clock = property(lambda s: LiveMatch.clock.fget(s))
    url = property(lambda s: s.article.get_absolute_url() if s.article
                   else (s.fixture.get_absolute_url() if s.fixture else ""))

    def get_status_display(self):
        return dict(LiveMatch.STATUS).get(self.status, self.status)

    @property
    def prediction(self):
        return (self.match.extra or {}).get("prediction") if self.match else None


def _bounds(day):
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    return start, start + timedelta(days=1)


def day_rows(day):
    """Mechi zote za siku: LIVE (za API na za mhariri) na ratiba nyingine, kwa ligi na muda."""
    start, end = _bounds(day)
    matches = list(LiveMatch.objects.select_related("article").defer("article__cover_png", "article__cover_thumb")
                   .filter(article__status="published", kickoff__gte=start, kickoff__lt=end))
    fixtures = list(Fixture.objects.filter(day=day))
    ranks = {(f.provider, f.fixture_id): f.rank for f in fixtures}
    taken = {(m.provider, m.fixture_id) for m in matches if m.fixture_id}
    rows = [Row(m, m.article, ranks.get((m.provider, m.fixture_id), 50 if not m.fixture_id else 99)) for m in matches]
    rows += [Row(f, None, f.rank) for f in fixtures if (f.provider, f.fixture_id) not in taken]
    rows.sort(key=lambda r: (r.rank, r.competition or "", r.kickoff or start, r.home))
    return rows


def groups(rows):
    """[(ligi, nembo, [mistari])] kwa mpangilio wa umuhimu wa ligi."""
    found = {}
    for r in rows:                                   # ligi moja = kundi moja, hata mechi za mhariri zikiwemo
        name = r.competition or "Mechi nyingine"
        group = found.setdefault(name, [r.rank, r.competition_logo, []])
        group[0] = min(group[0], r.rank)
        group[1] = group[1] or r.competition_logo
        group[2].append(r)
    out = sorted(found.items(), key=lambda kv: (kv[1][0], kv[0]))
    for _, (_, _, items) in out:
        items.sort(key=lambda r: (r.kickoff or timezone.now(), r.home))
    return [(name, logo, items) for name, (_, logo, items) in out]


def days_with_fixtures(limit=60):
    """Siku zilizopita zenye mechi (kwa sitemap)."""
    since = timezone.localdate() - timedelta(days=limit)
    days = set(Fixture.objects.filter(day__gte=since).values_list("day", flat=True).distinct())
    days |= {timezone.localtime(k).date() for k in LiveMatch.objects.filter(
        article__status="published", kickoff__gte=timezone.now() - timedelta(days=limit)).values_list("kickoff", flat=True) if k}
    return sorted(days, reverse=True)


# ── Ukurasa wa mechi isiyo na LIVE: maelezo yanaombwa msomaji anapoufungua ─────

ONDEMAND_KEY = "fixture-detail"


def _due(f, now):
    """Je, maelezo ya mechi hii yaombwe tena sasa? (ombi 1; mechi iliyoisha haiombwi tena)"""
    from datetime import datetime as _dt
    x = f.extra or {}
    try:
        age = now - _dt.fromisoformat(x["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return bool(f.kickoff and f.kickoff <= now + timedelta(hours=1))   # mechi ya kesho: bado hakuna cha kuonyesha
    if f.status in LiveMatch.FINISHED | LiveMatch.OFF and x.get("final"):
        return False
    started = f.kickoff and f.kickoff <= now
    return bool(started and age > timedelta(minutes=10)) or age > timedelta(hours=12)


def refresh_detail(f, now=None):
    """
    Maelezo ya mechi (matokeo, matukio, takwimu, uwanja, mwamuzi) msomaji anapofungua ukurasa wake.
    Ndani ya bajeti tu: FOOTBALL_ONDEMAND_MAX kwa siku, na maombi ya mechi za LIVE yakibaki ya kutosha.
    Inarudisha True kama ombi lilifanyika.
    """
    from news import football, highlightly
    from news.enrich import LIVE_RESERVE
    from news.models import DailyCount
    now = now or timezone.now()
    if f.provider != "highlightly" or football.provider() != "highlightly" or not _due(f, now):
        return False
    state = football._state(now)
    if (football.daily_limit() - state.requests <= LIVE_RESERVE
            or DailyCount.get(ONDEMAND_KEY) >= getattr(settings, "FOOTBALL_ONDEMAND_MAX", 20)):
        return False
    f.extra = {**(f.extra or {}), "fetched_at": now.isoformat()}     # muda wa jaribio — wageni wengi, ombi moja
    type(f).objects.filter(pk=f.pk).update(extra=f.extra)
    DailyCount.bump(ONDEMAND_KEY)
    try:
        items = highlightly.match(state, f)
        state.save(update_fields=["requests", "remaining"])
    except Exception as exc:
        log.warning("fixture detail %s failed: %s", f.fixture_id, exc)
        return True
    if not items:
        return True
    item = items[0]
    rank = f.rank
    _set(f, item)
    f.rank = rank
    fx = item["fixture"]
    venue = fx.get("venue") or {}
    events = []
    for ev in sorted(item.get("events") or [], key=lambda e: ((e.get("time") or {}).get("elapsed") or 0,
                                                              (e.get("time") or {}).get("extra") or 0)):
        out = football.event_text(ev, f)
        if out:
            events.append({"label": football._label(ev), "kind": out[0], "text": out[1]})
    extra = item.get("_extra") or {}
    f.extra = {**(f.extra or {}), "events": events, "stats": extra.get("stats") or (f.extra or {}).get("stats") or [],
               "referee": extra.get("referee", ""), "venue": venue.get("name") or "", "city": venue.get("city") or "",
               "final": f.status in LiveMatch.FINISHED}
    f.save()
    return True

"""
Hifadhi ya matokeo ya mechi (MatchResult) — data ya kufundishia utabiri (news/ratings.py, news/predict.py).

    collect()                 bure: mechi za ratiba (Fixture) na LIVE zilizokwisha zinaingia kwenye hifadhi
    backfill(state, now, n)   historia ya msimu mzima wa kila ligi (wa sasa na uliopita), kwa maombi
                              yanayobaki ya siku tu — kurasa chache kila tick, hadi BACKFILL_PER_DAY kwa siku

Zaidi ya historia, ndivyo ukadiriaji wa nguvu ya timu unavyoaminika. Ligi ya mechi 380 kwa msimu ni
maombi 4 tu, kwa hiyo ndani ya siku chache ligi zetu zote zinakuwa na misimu miwili kamili.
"""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

log = logging.getLogger("news.results")

PAGE = 100
BACKFILL_MIN_LEFT = 45      # maombi ya siku yanayohitajika kabla ya kupakua historia (LIVE na ratiba kwanza)
BACKFILL_PER_DAY = 20       # kikomo cha maombi ya historia kwa siku
BACKFILL_PER_TICK = 3
BACKFILL_LEAGUES = 25       # ligi za kipaumbele cha juu (HIGHLIGHTLY_LEAGUES)
SEASONS_BACK = 2            # msimu wa sasa + uliopita
PROGRESS_KEY = "results-backfill"


def _finished():
    from news.models import LiveMatch
    return LiveMatch.FINISHED


def collect(days=None):
    """
    Mechi zilizokwisha zenye matokeo -> MatchResult. days: zilizosasishwa karibuni tu (kila tick); hifadhi
    ikiwa tupu inasoma kila kitu mara moja. Inarudisha idadi mpya. Hakuna ombi la API.
    """
    from news.models import Fixture, LiveMatch, MatchResult
    before = MatchResult.objects.count()
    since = timezone.now() - timedelta(days=days) if days and before else None
    have, new = set(), {}
    fx = Fixture.objects.filter(status__in=_finished(), home_goals__isnull=False, away_goals__isnull=False)
    if since:
        fx = fx.filter(updated_at__gte=since)
    for f in fx.only("provider", "fixture_id", "competition_id", "competition", "country", "kickoff", "day",
                     "home", "away", "home_goals", "away_goals", "extra"):
        if f.fixture_id in have or f.fixture_id in new:
            continue
        season = (f.extra or {}).get("season")
        played = f.kickoff or timezone.make_aware(datetime.combine(f.day, datetime.min.time()))
        new[f.fixture_id] = MatchResult(provider=f.provider, fixture_id=f.fixture_id, competition_id=f.competition_id,
                               competition=f.competition[:80], country=(f.country or "")[:60],
                               season=int(season) if str(season or "").isdigit() else None, played_at=played,
                               home=f.home[:80], away=f.away[:80], home_goals=f.home_goals, away_goals=f.away_goals)
    lm = LiveMatch.objects.filter(status__in=_finished(), fixture_id__isnull=False, kickoff__isnull=False,
                                  home_goals__isnull=False, away_goals__isnull=False)
    if since:
        lm = lm.filter(kickoff__gte=since - timedelta(days=2))
    for m in lm.only("provider", "fixture_id", "competition", "kickoff", "home", "away", "home_goals",
                     "away_goals", "extra"):
        if m.fixture_id in have or m.fixture_id in new:
            continue
        x = m.extra or {}
        season = x.get("season")
        new[m.fixture_id] = MatchResult(provider="highlightly", fixture_id=m.fixture_id, competition_id=x.get("league_id"),
                               competition=m.competition[:80], country=str(x.get("country") or "")[:60],
                               season=int(season) if str(season or "").isdigit() else None, played_at=m.kickoff,
                               home=m.home[:80], away=m.away[:80], home_goals=m.home_goals, away_goals=m.away_goals)
    if new:
        MatchResult.objects.bulk_create(new.values(), ignore_conflicts=True, batch_size=500)
    return MatchResult.objects.count() - before


def _progress():
    from news.models import ModelReport
    rep, _ = ModelReport.objects.get_or_create(key=PROGRESS_KEY, defaults={"data": {}})
    return rep


def _jobs(now):
    """[(ligi, msimu)] kwa kipaumbele — msimu wa sasa kwanza, kisha uliopita."""
    from news.models import League
    jobs = []
    for lg in League.objects.filter(active=True).order_by("rank", "name")[:BACKFILL_LEAGUES]:
        current = lg.season or now.year
        for back in range(SEASONS_BACK):
            jobs.append((lg, current - back))
    jobs.sort(key=lambda j: (j[1] != (j[0].season or now.year), j[0].rank))
    return jobs


def _store(rows, lg, season):
    from news import highlightly
    from news.models import MatchResult
    out = []
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        try:
            item = highlightly.normalize(raw)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if item["fixture"]["status"]["short"] not in _finished():
            continue
        hg, ag = item["goals"]["home"], item["goals"]["away"]
        if hg is None or ag is None or not item["fixture"].get("date"):
            continue
        try:
            played = datetime.fromisoformat(str(item["fixture"]["date"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(MatchResult(provider="highlightly", fixture_id=item["fixture"]["id"], competition_id=lg.competition_id,
                               competition=(item["league"].get("name") or lg.competition)[:80],
                               country=(item["league"].get("country") or lg.country or "")[:60], season=season,
                               played_at=played, home=(item["teams"]["home"]["name"] or "")[:80],
                               away=(item["teams"]["away"]["name"] or "")[:80], home_goals=hg, away_goals=ag))
    MatchResult.objects.bulk_create(out, ignore_conflicts=True)
    return len(out)


def backfill(state, now, left):
    """Kurasa chache za historia ya ligi (leagueId + season). Inarudisha idadi ya maombi yaliyotumika."""
    from news import football, highlightly
    from news.models import DailyCount
    if football.provider() != "highlightly" or left < BACKFILL_MIN_LEFT:
        return 0
    used_today = DailyCount.get("results:backfill")
    if used_today >= BACKFILL_PER_DAY:
        return 0
    rep = _progress()
    progress = dict(rep.data or {})
    spent = 0
    for lg, season in _jobs(now):
        if spent >= BACKFILL_PER_TICK or used_today + spent >= BACKFILL_PER_DAY:
            break
        key = f"{lg.competition_id}:{season}"
        job = progress.get(key) or {"offset": 0, "done": False}
        if job.get("done"):
            continue
        try:
            data = highlightly._get("/matches", {"leagueId": lg.competition_id, "season": season,
                                                 "limit": PAGE, "offset": job["offset"]}, state)
        except Exception as exc:               # ligi/msimu usiokubalika: usirudie kila tick
            log.warning("results backfill %s failed: %s", key, exc)
            progress[key] = {**job, "done": True, "error": str(exc)[:200]}
            spent += 1
            continue
        spent += 1
        rows = (data.get("data") if isinstance(data, dict) else data) or []
        stored = _store(rows, lg, season)
        total = ((data.get("pagination") or {}).get("totalCount") if isinstance(data, dict) else None) or 0
        offset = job["offset"] + PAGE
        done = len(rows) < PAGE or (total and offset >= total)
        progress[key] = {"offset": offset, "done": bool(done), "stored": job.get("stored", 0) + stored,
                         "at": now.isoformat()}
    if spent:
        DailyCount.bump("results:backfill", by=spent)
        state.save(update_fields=["requests", "remaining"])
        rep.data = progress
        rep.save(update_fields=["data", "updated_at"])
    return spent

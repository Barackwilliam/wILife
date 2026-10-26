"""
Kurasa za ligi (/ligi/ na /ligi/<slug>/): msimamo, mechi za leo/kesho, matokeo ya karibuni, habari na video.

    sync()                         ligi mpya kutoka ratiba (Fixture) — hakuna ombi la API
    refresh_standings(state, now)  msimamo wa ligi MOJA kwa mzunguko, kila ligi mara moja kwa siku,
                                   maombi ya siku yakiwa mengi tu (LEAGUE_STANDINGS_MIN_LEFT)
    summary(league)                data ya ukurasa

Msimamo unatoka API (Highlightly /standings) tu — haukokotolewi kutoka mechi chache tulizohifadhi,
kwa sababu ungekuwa si sahihi. Jibu lisiloeleweka linahifadhiwa (standings_note) ili lionekane Admin.
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

log = logging.getLogger("news.leagues")

STANDINGS_EVERY = timedelta(hours=20)
MIN_LEFT = 50                  # maombi ya leo yaliyobaki: chini ya hapa msimamo unasubiri (LIVE na matokeo kwanza)
INTERNATIONAL = {"", "world", "international", "europe", "africa", "*"}


def _slug(competition, country):
    from news.models import League
    base = slugify(competition if (country or "").strip().lower() in INTERNATIONAL else f"{competition} {country}")
    base = base[:100] or "ligi"
    slug, n = base, 2
    while League.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _label(competition, country):
    if (country or "").strip().lower() in INTERNATIONAL:
        return competition
    return f"{competition} ({country})"


def sync():
    """Ligi zote za ratiba yetu zipate rekodi (jina, nembo, msimu, kipaumbele). Inarudisha mpya."""
    from news.models import Fixture, League
    new = 0
    seen = {}
    for f in Fixture.objects.exclude(competition_id=None).order_by("-day")[:3000]:
        if f.competition_id in seen:
            continue
        seen[f.competition_id] = f
    from django.db.models import Min
    # Kipaumbele: nafasi ya juu kabisa ya mechi zake (0 = ligi ambayo timu zetu — Simba, Yanga… — zinacheza)
    ranks = dict(Fixture.objects.filter(competition_id__in=seen).values("competition_id")
                 .annotate(r=Min("rank")).values_list("competition_id", "r"))
    known = {lg.competition_id: lg for lg in League.objects.filter(competition_id__in=seen)}
    for cid, f in seen.items():
        season = (f.extra or {}).get("season")
        season = int(season) if str(season or "").isdigit() else None
        lg = known.get(cid)
        if lg is None:
            League.objects.create(competition_id=cid, competition=f.competition, country=f.country,
                                  name=_label(f.competition, f.country)[:120], slug=_slug(f.competition, f.country),
                                  logo=f.competition_logo, season=season, rank=min(ranks.get(cid, 99), 99))
            new += 1
            continue
        changes = {}
        if season and season != lg.season:
            changes["season"] = season
        if f.competition_logo and not lg.logo:
            changes["logo"] = f.competition_logo
        if ranks.get(cid, 99) < lg.rank:
            changes["rank"] = ranks[cid]
        if changes:
            League.objects.filter(pk=lg.pk).update(**changes)
    return new


# ── Msimamo ────────────────────────────────────────────────────────────────────

def _num(row, *keys):
    for k in keys:
        cur = row
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, (int, float)) and not isinstance(cur, bool):
            return int(cur)
        if isinstance(cur, str) and cur.lstrip("-").isdigit():
            return int(cur)
    return None


def _row(raw):
    team = raw.get("team") if isinstance(raw.get("team"), dict) else {}
    name = (team.get("name") or raw.get("teamName") or "").strip()
    pos = _num(raw, "position", "rank")
    pts = _num(raw, "points", "pts")
    if not name or pos is None or pts is None:
        return None
    return {"pos": pos, "team": name[:80], "logo": (team.get("logo") or "")[:300] if
            str(team.get("logo") or "").startswith("https://") else "",
            "p": _num(raw, "games", "played", "gamesPlayed", "total.games", "total.played", "all.played"),
            "w": _num(raw, "wins", "won", "total.wins", "total.won", "all.win"),
            "d": _num(raw, "draws", "drawn", "total.draws", "total.drawn", "all.draw"),
            "l": _num(raw, "loses", "losses", "lost", "total.loses", "total.lost", "all.lose"),
            "gf": _num(raw, "scoredGoals", "goalsFor", "goals_for", "total.scoredGoals", "all.goals.for"),
            "ga": _num(raw, "receivedGoals", "goalsAgainst", "goals_against", "total.receivedGoals",
                       "all.goals.against"),
            "gd": _num(raw, "goalDifference", "goalsDiff", "goal_difference"),
            "pts": pts}


def _with_gd(r):
    if r and r["gd"] is None and r["gf"] is not None and r["ga"] is not None:
        r["gd"] = r["gf"] - r["ga"]
    return r


def _tables(node, group="", out=None):
    """Tafuta orodha za safu za msimamo popote kwenye jibu (muundo hautegemewi kikamilifu)."""
    out = [] if out is None else out
    if isinstance(node, list):
        rows = [_with_gd(_row(x)) for x in node if isinstance(x, dict)]
        rows = [r for r in rows if r]
        if len(rows) >= 2:
            out.append({"group": group, "rows": sorted(rows, key=lambda r: r["pos"])})
            return out
        for x in node:
            _tables(x, group, out)
    elif isinstance(node, dict):
        name = node.get("name") or node.get("group") or group
        for v in node.values():
            if isinstance(v, (list, dict)):
                _tables(v, name if isinstance(name, str) else group, out)
    return out


def parse(data):
    tables = _tables(data)
    if len(tables) == 1:
        tables[0]["group"] = ""
    return tables[:16]


def refresh_standings(state, now, left):
    """Msimamo wa ligi moja iliyochelewa zaidi (ombi 1). Inarudisha maombi yaliyotumika."""
    from news import highlightly
    from news.models import League
    limit = int(getattr(settings, "LEAGUE_STANDINGS_MAX", 8))
    if left <= MIN_LEFT or not limit:
        return 0
    due = [lg for lg in League.objects.filter(active=True).exclude(season=None).order_by("rank", "name")[:limit]
           if not lg.standings_at or now - lg.standings_at >= STANDINGS_EVERY]
    if not due:
        return 0
    lg = min(due, key=lambda x: (x.standings_at or now - timedelta(days=365), x.rank))
    League.objects.filter(pk=lg.pk).update(standings_at=now)        # muda wa jaribio — kosa lisirudiwe kila dakika
    try:
        data = highlightly._get("/standings", {"leagueId": lg.competition_id, "season": lg.season}, state)
    except Exception as exc:
        log.warning("standings %s failed: %s", lg, exc)
        League.objects.filter(pk=lg.pk).update(standings_note=f"kosa: {str(exc)[:400]}")
        state.save(update_fields=["requests", "remaining"])
        return 1
    state.save(update_fields=["requests", "remaining"])
    tables = parse(data)
    if tables:
        League.objects.filter(pk=lg.pk).update(standings=tables, standings_note="")
    else:                                   # muundo usiotarajiwa: hifadhi mfano, msimamo wa zamani ubaki
        League.objects.filter(pk=lg.pk).update(
            standings_note="jibu halikueleweka: " + json.dumps(data, ensure_ascii=False, default=str)[:3000])
    return 1


# ── Ukurasa ────────────────────────────────────────────────────────────────────

def _matches(lg, since, until):
    """Mechi za ligi hii kati ya siku hizi: LIVE (zina kurasa zao) na ratiba nyingine."""
    from news.fixtures import Row
    from news.models import Fixture, LiveMatch
    fixtures = list(Fixture.objects.filter(competition_id=lg.competition_id, day__gte=since, day__lte=until))
    ids = {f.fixture_id for f in fixtures}
    lives = {m.fixture_id: m for m in LiveMatch.objects.select_related("article").defer(
        "article__cover_png", "article__cover_thumb").filter(fixture_id__in=ids, article__status="published")}
    return [Row(lives[f.fixture_id], lives[f.fixture_id].article, f.rank) if f.fixture_id in lives
            else Row(f, None, f.rank) for f in fixtures]


def standing_of(lg, team):
    """Safu ya timu kwenye msimamo (kwa jina), au None."""
    from news.scorebat import _close, team_key
    key = team_key(team)
    for table in lg.standings or []:
        for r in table.get("rows") or []:
            if _close(team_key(r["team"]), key):
                return {**r, "group": table.get("group") or "", "size": len(table.get("rows") or [])}
    return None


def summary(lg, now=None):
    from news.models import Article, Highlight
    now = now or timezone.now()
    today = timezone.localdate(now)
    upcoming = [r for r in _matches(lg, today, today + timedelta(days=2)) if not r.is_finished]
    upcoming.sort(key=lambda r: (r.kickoff or now, r.home))
    results = [r for r in _matches(lg, today - timedelta(days=21), today) if r.is_finished]
    results.sort(key=lambda r: (r.kickoff or now, r.home), reverse=True)
    from news.models import Fixture
    from news.scorebat import team_key
    rows = Fixture.objects.filter(competition_id=lg.competition_id)
    ids = list(rows.values_list("fixture_id", flat=True)[:2000])
    keys = {team_key(n) for pair in rows.values_list("home", "away")[:2000] for n in pair} - {""}
    # Kwa namba ya ligi, si jina: "Premier League" ni ya England na ya Tanzania pia
    news = list(Article.published.defer("cover_png", "body").filter(match__fixture_id__in=ids)
                .order_by("-published_at")[:8])
    videos = list(Highlight.objects.filter(home_key__in=keys, away_key__in=keys)[:6]) if keys else []
    return {"league": lg, "upcoming": upcoming[:20], "results": results[:20], "news": news, "videos": videos,
            "tables": lg.standings or []}


def for_competition(competition_id=None, name=""):
    """League ya mechi hii (kwa namba ya ligi, au jina), au None."""
    from news.models import League
    qs = League.objects.filter(active=True)
    if competition_id:
        return qs.filter(competition_id=competition_id).first()
    if name:
        return qs.filter(competition=name).order_by("rank").first()
    return None

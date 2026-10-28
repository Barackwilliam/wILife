"""
Kurasa za timu (/timu/<slug>/) — kila kitu kinajazwa chenyewe kutoka data tuliyo nayo tayari:

    habari      makala zenye maneno ya timu (Team.news_terms) + makala za LIVE za mechi zake
    mechi       ijayo / inayochezwa (LIVE au ratiba), matokeo ya karibuni, fomu ya mechi 5
    takwimu     ushindi/sare/vipigo na magoli — kutoka mechi zilizohifadhiwa kwenye wILife tu
    video       video za magoli (ScoreBat, news/scorebat.py)

Hakuna kinachobuniwa: ratiba ya mbali, msimamo wa ligi na historia ya klabu havipo kwenye data yetu,
kwa hiyo havionyeshwi (utambulisho wa timu unaandikwa na mhariri — Team.intro).
"""
from datetime import timedelta
from functools import reduce
from operator import or_

from django.db.models import Q
from django.utils import timezone

from news.highlightly import _team_key
from news.models import Article, Fixture, Highlight, LiveMatch

RESULTS = 10
NEWS = 12


def _side(team, home, away):
    """'home' / 'away' kama timu hii inacheza mechi hii, vinginevyo None."""
    keys = team.keys
    if _team_key(home) in keys:
        return "home"
    if _team_key(away) in keys:
        return "away"
    return None


def _name_q(team):
    """Kichujio cha database (kipana) — kisha _side kinathibitisha jina kamili."""
    words = [n.strip() for n in team.match_names.split(",") if n.strip()]
    return reduce(or_, [Q(home__icontains=w) | Q(away__icontains=w) for w in words]) if words else Q(pk__in=[])


def matches(team, days=120):
    """Mechi zote za timu (LIVE + ratiba), bila kurudia, za karibuni kwanza: [(Row, side)]."""
    from news.fixtures import Row
    since = timezone.now() - timedelta(days=days)
    out, seen = [], set()
    for m in (LiveMatch.objects.select_related("article").filter(_name_q(team), article__status="published",
                                                                  kickoff__gte=since).order_by("-kickoff")):
        side = _side(team, m.home, m.away)
        if side:
            out.append((Row(m, m.article), side))
            if m.fixture_id:
                seen.add((m.provider, m.fixture_id))
    for f in Fixture.objects.filter(_name_q(team), kickoff__gte=since).order_by("-kickoff"):
        side = _side(team, f.home, f.away)
        if side and (f.provider, f.fixture_id) not in seen:
            out.append((Row(f, None, f.rank), side))
    out.sort(key=lambda rs: rs[0].kickoff or since, reverse=True)
    return out


def _result(row, side):
    mine, theirs = (row.home_goals, row.away_goals) if side == "home" else (row.away_goals, row.home_goals)
    return "W" if mine > theirs else ("D" if mine == theirs else "L"), mine, theirs


def summary(team, now=None):
    """Kila kitu cha ukurasa wa timu."""
    now = now or timezone.now()
    rows = matches(team)
    finished = [(r, s) for r, s in rows if r.is_finished and r.has_score]
    upcoming = sorted([(r, s) for r, s in rows if not r.is_finished and not r.is_off and r.kickoff
                       and r.kickoff >= now - timedelta(hours=3)], key=lambda rs: rs[0].kickoff)
    results = []
    for r, s in finished[:RESULTS]:
        res, mine, theirs = _result(r, s)
        results.append({"row": r, "res": res, "mine": mine, "theirs": theirs,
                        "opponent": r.away if s == "home" else r.home, "venue": "Nyumbani" if s == "home" else "Ugenini"})
    stats = {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0}
    for r, s in finished:
        res, mine, theirs = _result(r, s)
        stats["P"] += 1
        stats[res] += 1
        stats["GF"] += mine
        stats["GA"] += theirs
    logo = team.logo
    if not logo:
        for r, s in rows:
            logo = r.home_logo if s == "home" else r.away_logo
            if logo:
                break
    return {"team": team, "logo": logo, "next": upcoming[0][0] if upcoming else None,
            "upcoming": [r for r, _ in upcoming[1:4]], "results": results,
            "form": [x["res"] for x in results[:5]], "stats": stats if stats["P"] else None,
            "videos": videos(team), "news": news(team), "leagues": leagues(team)}


def leagues(team):
    """Ligi/mashindano ambayo timu imecheza (kwa ratiba tuliyohifadhi) — viungo vya msimamo."""
    from news.models import Fixture, League
    from news.scorebat import team_key
    keys = team.keys
    ids = {cid for home, away, cid in Fixture.objects.exclude(competition_id=None)
           .values_list("home", "away", "competition_id")[:5000]
           if team_key(home) in keys or team_key(away) in keys}
    return list(League.objects.filter(competition_id__in=ids, active=True)) if ids else []


def news(team, limit=NEWS):
    """Makala za timu: maneno yake (kichwa/maelezo/keywords) au LIVE ya mechi zake."""
    terms = team.terms
    q = Q(pk__in=[])
    for t in terms:
        q |= Q(title__icontains=t) | Q(excerpt__icontains=t) | Q(keywords__icontains=t)
    live_ids = [m.article_id for m in LiveMatch.objects.filter(_name_q(team)).only("article_id", "home", "away")
                if _side(team, m.home, m.away)]
    q |= Q(pk__in=live_ids)
    return list(Article.published.filter(q).defer("cover_png", "body").order_by("-published_at")[:limit])


def videos(team, limit=4):
    from news.scorebat import team_key
    keys = {team_key(n) for n in team.match_names.split(",") if n.strip()}
    qs = Highlight.objects.filter(Q(home_key__in=keys) | Q(away_key__in=keys) | Q(team=team))
    return list(qs[:limit])


def for_names(*names):
    """Timu zenye kurasa kati ya majina haya (kwa viungo kwenye kurasa za mechi)."""
    from news.models import Team
    found = []
    for team in Team.objects.filter(active=True):
        if any(_team_key(n) in team.keys for n in names if n) and team not in found:
            found.append(team)
    return found

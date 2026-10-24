"""
Makadirio ya mechi (si uhakika) — kutoka data halisi tuliyonayo tayari (news/enrich.py):

    fomu ya mechi 5 zilizopita za kila timu   magoli wanayofunga na kufungwa kwa wastani
    uso kwa uso                               uzito mdogo, kama wamekutana mara 3+

Magoli yanayotarajiwa ya kila timu -> mgawanyo wa Poisson -> uwezekano wa ushindi wa nyumbani,
sare, ushindi wa ugenini, magoli 3+ na timu zote kufunga. Makadirio yanafungwa mechi ikianza,
na baada ya filimbi ya mwisho yanapimwa (yalipatia au la) — rekodi inaonyeshwa wazi kwa wasomaji.
"""
import math
from datetime import timedelta

from django.utils import timezone

from news.models import LiveMatch

HOME_EDGE = 1.12           # faida ya kucheza nyumbani (wastani wa ligi kubwa)
AWAY_EDGE = 0.90
MIN_GAMES = 3              # chini ya hapa data haitoshi kukadiria
MAX_GOALS = 10
PICK_SW = {"1": "ushindi wa {home}", "X": "sare", "2": "ushindi wa {away}"}


def _goals(score):
    try:
        h, a = (int(x) for x in str(score).replace("–", "-").split("-"))
        return h, a
    except ValueError:
        return None


def _rates(games, team):
    """(wastani wa magoli ya kufunga, ya kufungwa, idadi ya mechi) kwa timu hii."""
    scored = conceded = n = 0
    for g in games or []:
        sc = _goals(g.get("score"))
        if not sc:
            continue
        if g.get("home") == team:
            scored, conceded = scored + sc[0], conceded + sc[1]
        elif g.get("away") == team:
            scored, conceded = scored + sc[1], conceded + sc[0]
        else:
            continue
        n += 1
    return (scored / n, conceded / n, n) if n else (0, 0, 0)


def _poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _clamp(v):
    return max(0.25, min(3.5, v))


def expected_goals(m):
    """(magoli yanayotarajiwa ya wenyeji, ya wageni) au None kama data haitoshi."""
    x = m.extra or {}
    hs, hc, hn = _rates(x.get("form_home_games"), m.home)
    as_, ac, an = _rates(x.get("form_away_games"), m.away)
    if hn < MIN_GAMES or an < MIN_GAMES:
        return None
    lam_h = (hs + ac) / 2 * HOME_EDGE
    lam_a = (as_ + hc) / 2 * AWAY_EDGE
    h2h = [g for g in x.get("h2h") or [] if _goals(g.get("score"))]
    if len(h2h) >= 3:
        mine, theirs = _rates(h2h, m.home)[:2]
        lam_h, lam_a = 0.8 * lam_h + 0.2 * mine, 0.8 * lam_a + 0.2 * theirs
    return _clamp(lam_h), _clamp(lam_a)


def _round100(values):
    """Asilimia kamili zinazojumlisha 100."""
    raw = [v * 100 for v in values]
    out = [int(v) for v in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:100 - sum(out)]:
        out[i] += 1
    return out


def compute(m):
    lam = expected_goals(m)
    if not lam:
        return None
    ph = [_poisson(k, lam[0]) for k in range(MAX_GOALS + 1)]
    pa = [_poisson(k, lam[1]) for k in range(MAX_GOALS + 1)]
    win = draw = loss = over = btts = 0.0
    best, best_p = (0, 0), -1
    for i, p1 in enumerate(ph):
        for j, p2 in enumerate(pa):
            p = p1 * p2
            if i > j:
                win += p
            elif i == j:
                draw += p
            else:
                loss += p
            if i + j >= 3:
                over += p
            if i and j:
                btts += p
            if p > best_p:
                best, best_p = (i, j), p
    total = win + draw + loss
    p_home, p_draw, p_away = _round100([win / total, draw / total, loss / total])
    pick = max((("1", p_home), ("X", p_draw), ("2", p_away)), key=lambda t: t[1])[0]
    return {"home": p_home, "draw": p_draw, "away": p_away, "pick": pick,
            "score": f"{best[0]}-{best[1]}", "over25": round(over * 100), "btts": round(btts * 100),
            "xg": [round(lam[0], 2), round(lam[1], 2)], "made_at": timezone.now().isoformat()}


def ensure(m):
    """Weka makadirio kwenye mechi kabla haijaanza (mara moja; yanafungwa mechi ikianza)."""
    x = m.extra or {}
    if m.status != "NS" or (m.kickoff and m.kickoff <= timezone.now()):
        return x.get("prediction")
    p = compute(m)
    if p and p != x.get("prediction"):
        old = x.get("prediction") or {}
        if {k: v for k, v in old.items() if k != "made_at"} != {k: v for k, v in p.items() if k != "made_at"}:
            m.extra = {**x, "prediction": p}
            LiveMatch.objects.filter(pk=m.pk).update(extra=m.extra)
    return (m.extra or {}).get("prediction")


def outcome(m):
    if not m.has_score:
        return None
    return "1" if m.home_goals > m.away_goals else ("X" if m.home_goals == m.away_goals else "2")


def settle(m):
    """Baada ya filimbi ya mwisho: makadirio yalipatia matokeo (1/X/2) na matokeo kamili?"""
    x = m.extra or {}
    p = x.get("prediction")
    if not p or not m.is_finished or "prediction_hit" in x:
        return
    result = outcome(m)
    m.extra = {**x, "prediction_hit": p["pick"] == result,
               "prediction_exact": p.get("score") == f"{m.home_goals}-{m.away_goals}"}
    LiveMatch.objects.filter(pk=m.pk).update(extra=m.extra)


def pick_text(m, p=None):
    p = p or (m.extra or {}).get("prediction") or {}
    return PICK_SW.get(p.get("pick"), "").format(home=m.home, away=m.away)


def sentence(m):
    """Sentensi ya makadirio kwa uchambuzi wa makala."""
    p = (m.extra or {}).get("prediction")
    if not p:
        return ""
    return (f"Makadirio ya wILife (kutokana na fomu na rekodi zao): {m.home} kushinda {p['home']}%, "
            f"sare {p['draw']}%, {m.away} kushinda {p['away']}%; matokeo yanayowezekana zaidi ni {p['score']}.")


def record(days=30):
    """Rekodi ya makadirio yaliyopimwa: {total, hits, exact, pct, recent}."""
    since = timezone.now() - timedelta(days=days)
    rows = list(LiveMatch.objects.select_related("article")
                .defer("article__cover_png", "article__cover_thumb", "article__body")
                .filter(extra__has_key="prediction_hit", kickoff__gte=since, article__status="published")
                .order_by("-kickoff"))
    hits = sum(1 for m in rows if m.extra.get("prediction_hit"))
    exact = sum(1 for m in rows if m.extra.get("prediction_exact"))
    return {"total": len(rows), "hits": hits, "exact": exact,
            "pct": round(hits * 100 / len(rows)) if rows else 0, "recent": rows[:20], "days": days}


def upcoming(now=None, hours=36):
    now = now or timezone.now()
    return list(LiveMatch.objects.select_related("article")
                .defer("article__cover_png", "article__cover_thumb", "article__body")
                .filter(extra__has_key="prediction", article__status="published",
                        kickoff__gte=now - timedelta(hours=3), kickoff__lte=now + timedelta(hours=hours))
                .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF).order_by("kickoff"))

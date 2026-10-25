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
LEAGUE_GOALS = 1.4         # wastani wa magoli ya timu moja kwa mechi (ligi kubwa)
PRIOR_GAMES = 5            # mechi 5 ni chache: magoli yanavutwa kuelekea wastani kana kwamba kuna mechi 5 za wastani
H2H_WEIGHT = 0.3           # uzito wa magoli ya mechi walizokutana (zikiwa 3 au zaidi)
RESULT_WEIGHT = 0.35       # uzito wa matokeo (ushindi/sare/kipigo + historia ya kukutana) dhidi ya magoli
POINTS = {"W": 3, "D": 1, "L": 0}
MAX_GOALS = 10
PICK_SW = {"1": "{home} ishinde", "X": "sare", "2": "{away} ishinde"}


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


def _shrink(avg, n):
    """Wastani wa mechi chache, ukivutwa kuelekea wastani wa ligi (ushindi mmoja wa 4-0 usiamue kila kitu)."""
    return (avg * n + LEAGUE_GOALS * PRIOR_GAMES) / (n + PRIOR_GAMES)


def expected_goals(m):
    """(magoli yanayotarajiwa ya wenyeji, ya wageni) au None kama data haitoshi."""
    x = m.extra or {}
    hs, hc, hn = _rates(x.get("form_home_games"), m.home)
    as_, ac, an = _rates(x.get("form_away_games"), m.away)
    if hn < MIN_GAMES or an < MIN_GAMES:
        return None
    hs, hc, as_, ac = _shrink(hs, hn), _shrink(hc, hn), _shrink(as_, an), _shrink(ac, an)
    lam_h = (hs + ac) / 2 * HOME_EDGE
    lam_a = (as_ + hc) / 2 * AWAY_EDGE
    h2h = [g for g in x.get("h2h") or [] if _goals(g.get("score"))]
    if len(h2h) >= 3:
        mine, theirs, n = _rates(h2h, m.home)
        if n >= 3:
            w = H2H_WEIGHT
            lam_h, lam_a = (1 - w) * lam_h + w * mine, (1 - w) * lam_a + w * theirs
    return _clamp(lam_h), _clamp(lam_a)


def _result_odds(m):
    """
    Uwezekano kutokana na matokeo yenyewe: pointi za mechi 5 zilizopita (W=3, D=1) na ushindi kwenye
    historia ya kukutana. Magoli pekee yanaweza kumpendelea timu iliyoshinda 4-0 dhidi ya timu dhaifu.
    """
    x = m.extra or {}
    fh, fa = x.get("form_home") or [], x.get("form_away") or []
    form = 0.0
    if fh and fa:
        form = (sum(POINTS[r] for r in fh) / len(fh) - sum(POINTS[r] for r in fa) / len(fa)) / 3
    t = x.get("h2h_tally") or {}
    n = sum(t.values()) if t else 0
    h2h = (t.get("W", 0) - t.get("L", 0)) / n if n >= 3 else 0.0
    s = 0.6 * form + 0.4 * h2h if n >= 3 else form                 # -1 (wageni bora) .. 1 (wenyeji bora)
    home, away = 0.45 + 0.30 * s, 0.29 - 0.30 * s
    home, away = max(0.05, home), max(0.05, away)
    draw = max(0.10, 1 - home - away)
    total = home + draw + away
    return home / total, draw / total, away / total


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
    rh, rd, ra = _result_odds(m)
    mix = [(1 - RESULT_WEIGHT) * p + RESULT_WEIGHT * r for p, r in zip((win / total, draw / total, loss / total),
                                                                         (rh, rd, ra))]
    p_home, p_draw, p_away = _round100(mix)
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
    return (f"Utabiri wa wILife kwa kuzingatia kiwango na rekodi za timu: {m.home} kushinda {p['home']}%, "
            f"sare {p['draw']}%, na {m.away} kushinda {p['away']}%. Matokeo yanayotarajiwa zaidi ni {p['score']}.")


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

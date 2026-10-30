"""
Kipimo cha uaminifu wa utabiri (walk-forward): kwa kila wiki ya kipindi cha majaribio, modeli inajengwa
kwa mechi za KABLA ya wiki hiyo tu, kisha inatabiri mechi za wiki hiyo — kama ingefanyika siku hizo.

    run(days=120) -> ripoti iliyohifadhiwa kwenye ModelReport("model-backtest"):
        usahihi (1X2, magoli 3+, timu zote kufunga), Brier, log loss, ulinganisho na kipimo cha msingi
        (asilimia za ligi tu), jedwali la calibration (tulisema 60% — ilitokea mara ngapi?) na uzito bora
        wa Dixon–Coles dhidi ya Elo unaotumiwa na news/predict.py.

Fomu ya mechi 5 (news/enrich.py) haipo kwa mechi za zamani, kwa hiyo uzito wake haubadilishwi hapa.
"""
import math
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

MIN_MATCHES = 300
STEP = timedelta(days=7)
ELO_GRID = [x / 10 for x in range(0, 11)]
BUCKETS = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
EVERY = timedelta(days=7)        # backtest ya moja kwa moja kwenye tick (football.run)


def _outcome(hg, ag):
    return 0 if hg > ag else (1 if hg == ag else 2)


def _brier(p, o):
    return sum((q - (1 if i == o else 0)) ** 2 for i, q in enumerate(p))


def _logloss(p, o):
    return -math.log(max(1e-6, p[o]))


def _mix(goals, elo, we):
    if goals and elo:
        return tuple((1 - we) * g + we * e for g, e in zip(goals, elo))
    return goals or elo


def _summary(items, key):
    rows = [it for it in items if it.get(key)]
    if not rows:
        return None
    acc = sum(1 for it in rows if max(range(3), key=lambda i: it[key][i]) == it["o"]) / len(rows)
    return {"n": len(rows), "accuracy": round(acc * 100, 1),
            "brier": round(sum(_brier(it[key], it["o"]) for it in rows) / len(rows), 4),
            "logloss": round(sum(_logloss(it[key], it["o"]) for it in rows) / len(rows), 4)}


def evaluate(rows, start, end):
    """Utabiri wa kila mechi kati ya start na end kwa modeli za wakati huo. Inarudisha orodha ya vipimo."""
    from news import predict, ratings
    rows = sorted(rows, key=lambda r: r.played_at)
    items = []
    t = start
    while t < end:
        week_end = min(end, t + STEP)
        train = [r for r in rows if r.played_at < t]
        test = [r for r in rows if t <= r.played_at < week_end]
        if not test or len(train) < 50:
            t = week_end
            continue
        model = ratings.build(before=t, rows=train)
        base = {}
        for r in train[-3000:]:
            b = base.setdefault(r.competition_id, [1, 1, 1])
            b[_outcome(r.home_goals, r.away_goals)] += 1
        for r in test:
            m = SimpleNamespace(home=r.home, away=r.away, competition_id=r.competition_id, country=r.country,
                                extra={}, status="NS", kickoff=r.played_at)
            a = predict.analyse(m, model)
            if not a:
                continue
            o = _outcome(r.home_goals, r.away_goals)
            b = base.get(r.competition_id, [1, 1, 1])
            it = {"o": o, "base": tuple(x / sum(b) for x in b),
                  "elo": tuple(a["elo"]["probs"]) if "elo" in a else None, "goals": None,
                  "goals_total": r.home_goals + r.away_goals, "btts": bool(r.home_goals and r.away_goals)}
            if "dc" in a:
                grid = predict._matrix(a["lam"][0], a["lam"][1], a["rho"])
                it["goals"] = predict._one_x_two(grid)
                it["over25"] = sum(p for (i, j), p in grid.items() if i + j > 2)
                it["p_btts"] = sum(p for (i, j), p in grid.items() if i and j)
            items.append(it)
        t = week_end
    return items


def report(items):
    if not items:
        return None
    best_we, best_ll = 0.3, None
    for we in ELO_GRID:
        both = [it for it in items if it["goals"] and it["elo"]]
        if not both:
            break
        ll = sum(_logloss(_mix(it["goals"], it["elo"], we), it["o"]) for it in both) / len(both)
        if best_ll is None or ll < best_ll:
            best_we, best_ll = we, ll
    for it in items:
        it["ens"] = _mix(it["goals"], it["elo"], best_we)
    calib = []
    for lo, hi in BUCKETS:
        group = [it for it in items if it["ens"] and lo <= max(it["ens"]) < hi]
        if group:
            said = sum(max(it["ens"]) for it in group) / len(group)
            hit = sum(1 for it in group if max(range(3), key=lambda i: it["ens"][i]) == it["o"]) / len(group)
            calib.append({"range": f"{round(lo * 100)}–{min(100, round(hi * 100))}%", "n": len(group),
                          "said": round(said * 100), "happened": round(hit * 100)})
    goal_items = [it for it in items if "over25" in it]
    markets = {}
    if goal_items:
        markets["over25"] = round(sum(1 for it in goal_items if (it["over25"] >= 0.5) == (it["goals_total"] >= 3))
                                  * 100 / len(goal_items), 1)
        markets["btts"] = round(sum(1 for it in goal_items if (it["p_btts"] >= 0.5) == it["btts"])
                                * 100 / len(goal_items), 1)
        markets["n"] = len(goal_items)
    from news.predict import DEFAULT_WEIGHTS
    form = DEFAULT_WEIGHTS["form"]
    return {"tested": len(items), "elo_share": best_we,
            "weights": {"goals": round((1 - best_we) * (1 - form), 3), "elo": round(best_we * (1 - form), 3),
                        "form": form},
            "models": {"ensemble": _summary(items, "ens"), "dixon_coles": _summary(items, "goals"),
                       "elo": _summary(items, "elo"), "baseline": _summary(items, "base")},
            "markets": markets, "calibration": calib}


def run(days=120, now=None):
    from django.core.cache import cache
    from news.models import MatchResult, ModelReport
    from news.predict import REPORT_KEY
    now = now or timezone.now()
    rows = list(MatchResult.objects.only("competition_id", "country", "played_at", "home", "away",
                                         "home_goals", "away_goals"))
    data = {"at": now.isoformat(), "days": days, "matches": len(rows)}
    rep = None
    if len(rows) >= MIN_MATCHES:
        rep = report(evaluate(rows, now - timedelta(days=days), now))
    if rep:
        data.update(rep)
    else:
        data["note"] = f"data haitoshi bado (mechi {len(rows)}; zinahitajika {MIN_MATCHES}+)"
    ModelReport.objects.update_or_create(key=REPORT_KEY, defaults={"data": data})
    cache.delete("predict:weights")
    return data


def due(now):
    from news.models import ModelReport
    from news.predict import REPORT_KEY
    rep = ModelReport.objects.filter(key=REPORT_KEY).first()
    return not rep or now - rep.updated_at >= EVERY

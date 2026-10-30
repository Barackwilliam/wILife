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


DEFAULT_WEIGHTS = {"goals": 0.55, "elo": 0.30, "form": 0.15}
FORM_GOALS_WEIGHT = 0.25   # uzito wa fomu ya mechi 5 kwenye magoli yanayotarajiwa, Dixon–Coles ikiwepo
MIN_DC_GAMES = 4           # mechi za timu kwenye ligi hii kabla ya kutumia nguvu yake ya Dixon–Coles
MIN_ELO_GAMES = 6
DEFAULT_TOTAL = 2.6        # magoli ya mechi kwa wastani (Elo peke yake haijui magoli)
REPORT_KEY = "model-backtest"
VERSION = 2
TIER_SW = {"juu": "Uhakika wa juu", "wastani": "Uhakika wa wastani", "chini": "Uhakika mdogo"}


def weights():
    """Uzito wa kila modeli: uliopimwa na backtest (manage.py backtest_predictions) au wa msingi."""
    from django.core.cache import cache
    w = cache.get("predict:weights")
    if w is None:
        from news.models import ModelReport
        rep = ModelReport.objects.filter(key=REPORT_KEY).values_list("data", flat=True).first() or {}
        w = rep.get("weights") or DEFAULT_WEIGHTS
        cache.set("predict:weights", w, 3600)
    return w


def _ctx(m):
    x = m.extra or {}
    comp = getattr(m, "competition_id", None) or x.get("league_id")
    country = getattr(m, "country", "") or x.get("country", "")
    return comp, country


def _matrix(lh, la, rho):
    """Uwezekano wa kila matokeo (i, j) — Poisson pamoja na marekebisho ya Dixon–Coles kwa magoli machache."""
    from news.ratings import _tau
    ph = [_poisson(k, lh) for k in range(MAX_GOALS + 1)]
    pa = [_poisson(k, la) for k in range(MAX_GOALS + 1)]
    grid = {}
    for i, p1 in enumerate(ph):
        for j, p2 in enumerate(pa):
            grid[(i, j)] = max(0.0, p1 * p2 * _tau(i, j, lh, la, rho))
    total = sum(grid.values()) or 1
    return {k: v / total for k, v in grid.items()}


def _one_x_two(grid):
    win = sum(p for (i, j), p in grid.items() if i > j)
    draw = sum(p for (i, j), p in grid.items() if i == j)
    return win, draw, 1 - win - draw


def _lambdas_from_probs(ph, pa, total=DEFAULT_TOTAL):
    """Magoli yanayotarajiwa yanayolingana na uwezekano wa ushindi (Elo peke yake)."""
    lo, hi = -(total - 0.4), total - 0.4          # tofauti ya magoli; (ushindi - kipigo) inaongezeka nayo
    for _ in range(25):
        s = (lo + hi) / 2
        w, _, l = _one_x_two(_matrix((total + s) / 2, (total - s) / 2, 0.0))
        if w - l < ph - pa:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return (total + s) / 2, (total - s) / 2


def analyse(m, model=None):
    """Uchambuzi kamili wa mechi: dict ya modeli zote zilizopo au None kama hakuna data ya kutosha."""
    from news import ratings
    model = model or ratings.current()
    comp, country = _ctx(m)
    out = {"factors": []}
    lg = model.leagues.get(comp) if comp else None
    dc = None
    if lg:
        hk, ak = ratings.team_key(m.home), ratings.team_key(m.away)
        if lg.raw_games.get(hk, 0) >= MIN_DC_GAMES and lg.raw_games.get(ak, 0) >= MIN_DC_GAMES:
            dc = lg.lambdas(m.home, m.away)
            out["dc"] = {"xg": [round(dc[0], 2), round(dc[1], 2)], "games": [lg.raw_games[hk], lg.raw_games[ak]],
                         "attack": [round(lg.attack[hk], 2), round(lg.attack[ak], 2)],
                         "defence": [round(lg.defence[hk], 2), round(lg.defence[ak], 2)],
                         "home_edge": round(lg.home, 2), "league_games": lg.n}
    elo = model.elo_pair(m.home, m.away, country)
    if elo and min(elo[2], elo[3]) >= MIN_ELO_GAMES:
        out["elo"] = {"ratings": [round(elo[0]), round(elo[1])], "games": [elo[2], elo[3]],
                      "probs": list(ratings.elo_probs(elo[0], elo[1], model.draw_max))}
    form = expected_goals(m)
    if form:
        out["form"] = {"xg": [round(form[0], 2), round(form[1], 2)], "probs": list(_result_odds(m))}
    if not (dc or "elo" in out or form):
        return None
    if dc and form:
        w = FORM_GOALS_WEIGHT
        lam = (dc[0] ** (1 - w) * form[0] ** w, dc[1] ** (1 - w) * form[1] ** w)
    elif dc or form:
        lam = dc or form
    else:
        ph, _, pa = out["elo"]["probs"]
        lam = _lambdas_from_probs(ph, pa)
    out["lam"] = (_clamp(lam[0]), _clamp(lam[1]))
    out["rho"] = lg.rho if lg else -0.05
    out["rev"] = model.matches
    return out


def _tier(a, probs):
    score = 0
    if "dc" in a and min(a["dc"]["games"]) >= 15:
        score += 2
    elif "dc" in a:
        score += 1
    if "elo" in a:
        score += 1
    picks = set()
    for k in ("goals", "elo", "form"):
        p = a.get("probs_" + k)
        if p:
            picks.add(max(range(3), key=lambda i: p[i]))
    if len(picks) == 1 and len(a.get("used", [])) >= 2:
        score += 1
    elif len(picks) > 1:
        score -= 1
    top = max(probs)
    if top >= 0.55:
        score += 1
    elif top < 0.42:
        score -= 1
    if score >= 4 and top >= 0.5:          # "juu" ni kwa chaguo lenye nafasi kubwa kuliko nyingine zote kwa pamoja
        return "juu"
    return "wastani" if score >= 2 else "chini"


def _factors(m, a):
    f = []
    if "dc" in a:
        d = a["dc"]
        f.append(f"Nguvu ya mashambulizi (wastani wa ligi = 100): {m.home} {d['attack'][0] * 100:.0f}, "
                 f"{m.away} {d['attack'][1] * 100:.0f}.")
        f.append(f"Ulinzi (chini ya 100 ni bora — wanaruhusu magoli machache): {m.home} {d['defence'][0] * 100:.0f}, "
                 f"{m.away} {d['defence'][1] * 100:.0f}.")
        f.append(f"Faida ya uwanja wa nyumbani kwenye ligi hii: {(d['home_edge'] - 1) * 100:+.0f}% magoli "
                 f"(mechi {d['league_games']} za ligi zimepimwa).")
    if "elo" in a:
        r = a["elo"]["ratings"]
        f.append(f"Alama za Elo (nguvu ya jumla): {m.home} {r[0]}, {m.away} {r[1]}.")
    x = m.extra or {}
    if x.get("form_home") and x.get("form_away"):
        f.append(f"Fomu ya mechi za karibuni: {m.home} {''.join(x['form_home'])}, {m.away} {''.join(x['form_away'])}.")
    t = x.get("h2h_tally") or {}
    if sum(t.values() if t else []) >= 3:
        f.append(f"Walipokutana: {m.home} ameshinda {t.get('W', 0)}, sare {t.get('D', 0)}, {m.away} ameshinda {t.get('L', 0)}.")
    f.append(f"Magoli yanayotarajiwa: {m.home} {a['lam'][0]:.2f}, {m.away} {a['lam'][1]:.2f}.")
    return f


def compute(m, model=None):
    a = analyse(m, model)
    if not a:
        return None
    grid = _matrix(a["lam"][0], a["lam"][1], a["rho"])
    w = weights()
    only_elo = "dc" not in a and "form" not in a      # magoli yametokana na Elo yenyewe: isihesabiwe mara mbili
    parts = [] if only_elo else [("goals", _one_x_two(grid))]
    if "elo" in a:
        parts.append(("elo", tuple(a["elo"]["probs"])))
    if "form" in a:
        parts.append(("form", tuple(a["form"]["probs"])))
    total_w = sum(w.get(k, 0) for k, _ in parts) or 1
    probs = [sum(w.get(k, 0) * p[i] for k, p in parts) / total_w for i in range(3)]
    for k, p in parts:
        a["probs_" + k] = p
    a["used"] = [k for k, _ in parts]
    p_home, p_draw, p_away = _round100(probs)
    pick = max((("1", p_home), ("X", p_draw), ("2", p_away)), key=lambda t: t[1])[0]
    over = {n: sum(p for (i, j), p in grid.items() if i + j > n) for n in (1, 2, 3)}
    btts = sum(p for (i, j), p in grid.items() if i and j)
    top = sorted(grid.items(), key=lambda kv: kv[1], reverse=True)[:3]
    markets = {"1x": round((probs[0] + probs[1]) * 100), "x2": round((probs[1] + probs[2]) * 100),
               "12": round((probs[0] + probs[2]) * 100),
               "over15": round(over[1] * 100), "over25": round(over[2] * 100), "over35": round(over[3] * 100),
               "btts": round(btts * 100)}
    tier = _tier(a, probs)
    return {"v": VERSION, "home": p_home, "draw": p_draw, "away": p_away, "pick": pick,
            "score": f"{top[0][0][0]}-{top[0][0][1]}", "over25": markets["over25"], "btts": markets["btts"],
            "xg": [round(a["lam"][0], 2), round(a["lam"][1], 2)], "markets": markets,
            "scores": [{"score": f"{i}-{j}", "pct": round(p * 100)} for (i, j), p in top],
            "confidence": tier, "models": a["used"], "factors": _factors(m, a), "rev": a["rev"],
            "made_at": timezone.now().isoformat()}


def _same(a, b):
    skip = {"made_at"}
    return {k: v for k, v in (a or {}).items() if k not in skip} == {k: v for k, v in (b or {}).items() if k not in skip}


def ensure(m, model=None):
    """Weka makadirio kwenye mechi (LiveMatch au Fixture) kabla haijaanza; yanafungwa mechi ikianza."""
    x = m.extra or {}
    if m.status != "NS" or (m.kickoff and m.kickoff <= timezone.now()):
        return x.get("prediction")
    p = compute(m, model)
    if p and not _same(p, x.get("prediction")):
        m.extra = {**x, "prediction": p}
        type(m).objects.filter(pk=m.pk).update(extra=m.extra)
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
    if result is None:
        return
    goals = m.home_goals + m.away_goals
    actual = {"1": (1, 0, 0), "X": (0, 1, 0), "2": (0, 0, 1)}[result]
    probs = (p.get("home", 0) / 100, p.get("draw", 0) / 100, p.get("away", 0) / 100)
    m.extra = {**x, "prediction_hit": p["pick"] == result,
               "prediction_exact": p.get("score") == f"{m.home_goals}-{m.away_goals}",
               "prediction_brier": round(sum((q - a) ** 2 for q, a in zip(probs, actual)), 4),
               "prediction_markets": {"over25": (p.get("over25", 0) >= 50) == (goals >= 3),
                                      "btts": (p.get("btts", 0) >= 50) == bool(m.home_goals and m.away_goals)}}
    type(m).objects.filter(pk=m.pk).update(extra=m.extra)


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


def _rows(objs):
    from news.fixtures import Row
    return [Row(o, getattr(o, "article", None)) if isinstance(o, LiveMatch) else Row(o, None, o.rank) for o in objs]


def record(days=30):
    """
    Rekodi ya makadirio yaliyopimwa (LIVE na ratiba nzima): {total, hits, exact, pct, recent, tiers, markets, brier}.
    tiers: usahihi kwa kila kiwango cha uhakika — kipimo cha kama "uhakika wa juu" kweli unapatia zaidi.
    """
    from news.models import Fixture
    since = timezone.now() - timedelta(days=days)
    lives = list(LiveMatch.objects.select_related("article")
                 .defer("article__cover_png", "article__cover_thumb", "article__body")
                 .filter(extra__has_key="prediction_hit", kickoff__gte=since, article__status="published"))
    seen = {m.fixture_id for m in lives if m.fixture_id}
    fxs = [f for f in Fixture.objects.filter(extra__has_key="prediction_hit", kickoff__gte=since)
           if f.fixture_id not in seen]
    rows = sorted(lives + fxs, key=lambda o: o.kickoff, reverse=True)
    hits = sum(1 for m in rows if m.extra.get("prediction_hit"))
    exact = sum(1 for m in rows if m.extra.get("prediction_exact"))
    tiers = {}
    for t in ("juu", "wastani", "chini"):
        group = [m for m in rows if m.extra["prediction"].get("confidence") == t]
        if group:
            ok = sum(1 for m in group if m.extra.get("prediction_hit"))
            tiers[t] = {"label": TIER_SW[t], "total": len(group), "hits": ok, "pct": round(ok * 100 / len(group))}
    markets = {}
    for k, label in (("over25", "Magoli 3+ / chini ya 3"), ("btts", "Timu zote kufunga")):
        group = [m.extra["prediction_markets"][k] for m in rows if k in (m.extra.get("prediction_markets") or {})]
        if group:
            markets[k] = {"label": label, "total": len(group), "hits": sum(group),
                          "pct": round(sum(group) * 100 / len(group))}
    briers = [m.extra["prediction_brier"] for m in rows if "prediction_brier" in m.extra]
    return {"total": len(rows), "hits": hits, "exact": exact,
            "pct": round(hits * 100 / len(rows)) if rows else 0, "recent": _rows(rows[:30]), "days": days,
            "tiers": tiers, "markets": markets,
            "brier": round(sum(briers) / len(briers), 3) if briers else None}


def upcoming(now=None, hours=36):
    now = now or timezone.now()
    return list(LiveMatch.objects.select_related("article")
                .defer("article__cover_png", "article__cover_thumb", "article__body")
                .filter(extra__has_key="prediction", article__status="published",
                        kickoff__gte=now - timedelta(hours=3), kickoff__lte=now + timedelta(hours=hours))
                .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF).order_by("kickoff"))


def board(now=None, hours=36, limit=80):
    """Mechi zote zijazo zenye utabiri (LIVE na ratiba nzima) kama mistari ya Row, kwa muda wa kuanza."""
    from news.models import Fixture
    now = now or timezone.now()
    lives = upcoming(now, hours)
    seen = {m.fixture_id for m in lives if m.fixture_id}
    fxs = [f for f in Fixture.objects.filter(extra__has_key="prediction", kickoff__gte=now - timedelta(hours=3),
                                             kickoff__lte=now + timedelta(hours=hours))
           .exclude(status__in=LiveMatch.FINISHED | LiveMatch.OFF) if f.fixture_id not in seen]
    rows = sorted(lives + fxs, key=lambda o: (o.kickoff, getattr(o, "rank", 99)))[:limit]
    return _rows(rows)


def top_picks(rows, n=6):
    """Utabiri wenye uhakika mkubwa zaidi (kiwango cha juu kwanza, kisha asilimia ya chaguo)."""
    order = {"juu": 0, "wastani": 1, "chini": 2}

    def key(r):
        p = r.prediction
        return order.get(p.get("confidence"), 3), -max(p["home"], p["draw"], p["away"])
    return sorted([r for r in rows if r.prediction and r.prediction.get("confidence") in ("juu", "wastani")],
                  key=key)[:n]


FIXTURE_HOURS = 48


def refresh_fixtures(now=None, limit=400):
    """
    Utabiri wa mechi zote za ratiba zinazokuja (si LIVE tu) na upimaji wa zilizokwisha. Hakuna ombi la API:
    modeli inatoka kwenye hifadhi ya matokeo. Inarudisha (zilizotabiriwa, zilizopimwa).
    """
    from news import ratings
    from news.models import Fixture
    now = now or timezone.now()
    made = settled = 0
    soon = list(Fixture.objects.filter(status="NS", kickoff__gt=now, kickoff__lte=now + timedelta(hours=FIXTURE_HOURS))
                .order_by("kickoff")[:limit])
    if soon:
        model = ratings.current()
        for f in soon:
            before = (f.extra or {}).get("prediction")
            if before and before.get("v") == VERSION and before.get("rev") == model.matches:
                continue                      # hakuna matokeo mapya tangu utabiri huu
            if ensure(f, model) and not _same(before, f.extra.get("prediction")):
                made += 1
    for f in Fixture.objects.filter(extra__has_key="prediction", status__in=LiveMatch.FINISHED,
                                    kickoff__gte=now - timedelta(days=4)).exclude(extra__has_key="prediction_hit"):
        settle(f)
        settled += 1
    return made, settled

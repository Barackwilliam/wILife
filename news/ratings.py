"""
Ukadiriaji wa nguvu ya timu kutoka hifadhi ya matokeo (MatchResult — news/results.py). Hakuna ombi la API.

    Dixon–Coles (kwa kila ligi)   nguvu ya mashambulizi na ulinzi ya kila timu, faida ya uwanja wa nyumbani
                                  ya ligi hiyo na marekebisho ya matokeo madogo (0-0, 1-0, 0-1, 1-1).
                                  Mechi za karibuni zina uzito zaidi (nusu-uhai HALF_LIFE siku); timu yenye
                                  mechi chache inavutwa kuelekea wastani (PRIOR mechi za wastani).
    Elo (timu zote)               alama moja ya nguvu inayosasishwa kila mechi kwa mpangilio wa tarehe —
                                  inaunganisha ligi, makombe na mechi za kimataifa.

    build(before=None)  -> Model    (before: tarehe — kwa backtest, mechi za kabla yake tu)
    current()           -> Model    (cache ya saa CACHE_SECONDS; inajengwa upya hifadhi ikibadilika)
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field

from django.core.cache import cache
from django.utils import timezone

HALF_LIFE = 180.0          # siku: mechi ya miezi 6 iliyopita ina nusu ya uzito wa ya leo
PRIOR = 4.0                # mechi za "wastani" zinazoongezwa kwa kila timu (kuzuia mechi chache kuamua kila kitu)
ITERATIONS = 40
MIN_LEAGUE_GAMES = 30      # chini ya hapa ligi haina modeli yake: inatumia Elo tu
RHO_GRID = [x / 100 for x in range(-20, 11, 2)]
MAX_AGE_DAYS = 800

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME = 60.0            # alama za faida ya nyumbani
ELO_DRAW_MAX = 0.28        # uwezekano wa sare timu zikiwa sawa kabisa (unasahihishwa na backtest)

CACHE_KEY = "ratings:v1"
CACHE_SECONDS = 3 * 3600
INTERNATIONAL = {"", "world", "africa", "europe", "international", "asia", "south america", "north america"}


def team_key(name):
    from news.highlightly import _team_key
    return _team_key(str(name or ""))


def norm_country(name):
    from news.football import _norm_country
    return _norm_country(str(name or ""))


@dataclass
class League:
    comp: int
    mu: float = 1.35                     # magoli ya wastani ya timu moja ugenini (msingi)
    home: float = 1.15                   # faida ya nyumbani (kizidishi)
    rho: float = -0.05
    attack: dict = field(default_factory=dict)
    defence: dict = field(default_factory=dict)
    games: dict = field(default_factory=dict)     # mechi zenye uzito kwa kila timu (kipimo cha uhakika)
    raw_games: dict = field(default_factory=dict)  # idadi halisi ya mechi
    n: int = 0
    draw_rate: float = 0.26

    def lambdas(self, home, away):
        """(magoli yanayotarajiwa nyumbani, ugenini) au None timu isipojulikana kwenye ligi hii."""
        h, a = team_key(home), team_key(away)
        if h not in self.attack or a not in self.attack:
            return None
        return (self.mu * self.home * self.attack[h] * self.defence[a],
                self.mu * self.attack[a] * self.defence[h])


@dataclass
class Model:
    leagues: dict = field(default_factory=dict)    # competition_id -> League
    elo: dict = field(default_factory=dict)        # ufunguo wa timu -> alama
    elo_games: dict = field(default_factory=dict)
    by_name: dict = field(default_factory=dict)    # jina -> [funguo za Elo] (kwa mechi za kimataifa)
    draw_max: float = ELO_DRAW_MAX
    matches: int = 0
    built_at: str = ""

    def elo_key(self, name, country=""):
        k = team_key(name)
        c = norm_country(country)
        if c and c not in INTERNATIONAL and f"{c}|{k}" in self.elo:
            return f"{c}|{k}"
        options = self.by_name.get(k) or []
        if len(options) == 1:
            return options[0]
        if c and c not in INTERNATIONAL:
            return f"{c}|{k}"
        return None

    def elo_pair(self, home, away, country=""):
        hk, ak = self.elo_key(home, country), self.elo_key(away, country)
        if not hk or not ak or hk not in self.elo or ak not in self.elo:
            return None
        return self.elo[hk], self.elo[ak], self.elo_games.get(hk, 0), self.elo_games.get(ak, 0)


def elo_probs(r_home, r_away, draw_max=ELO_DRAW_MAX, home=ELO_HOME):
    """(nyumbani, sare, ugenini) kutoka tofauti ya alama za Elo."""
    e = 1 / (1 + 10 ** (-(r_home + home - r_away) / 400))
    draw = max(0.04, draw_max * (1 - (2 * e - 1) ** 2))
    ph = max(0.01, e - draw / 2)
    pa = max(0.01, 1 - ph - draw)
    t = ph + draw + pa
    return ph / t, draw / t, pa / t


def _goal_mult(gd):
    gd = abs(gd)
    return 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8)


def elo_update(model, home_key, away_key, hg, ag):
    rh = model.elo.get(home_key, ELO_START)
    ra = model.elo.get(away_key, ELO_START)
    e = 1 / (1 + 10 ** (-(rh + ELO_HOME - ra) / 400))
    s = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
    d = ELO_K * _goal_mult(hg - ag) * (s - e)
    model.elo[home_key] = rh + d
    model.elo[away_key] = ra - d
    model.elo_games[home_key] = model.elo_games.get(home_key, 0) + 1
    model.elo_games[away_key] = model.elo_games.get(away_key, 0) + 1


def match_elo_keys(model, row):
    """Funguo za Elo kwa mechi ya hifadhi: ligi ya nchi -> 'nchi|timu'; kimataifa -> funguo iliyopo ya jina hilo."""
    c = norm_country(row.country)
    keys = []
    for name in (row.home, row.away):
        k = team_key(name)
        if c and c not in INTERNATIONAL:
            key = f"{c}|{k}"
            if key not in model.by_name.setdefault(k, []):
                model.by_name[k].append(key)
        else:
            options = model.by_name.get(k) or []
            key = options[0] if len(options) == 1 else f"|{k}"
        keys.append(key)
    return keys


def _tau(i, j, lh, la, rho):
    if i == 0 and j == 0:
        return 1 - lh * la * rho
    if i == 0 and j == 1:
        return 1 + lh * rho
    if i == 1 and j == 0:
        return 1 + la * rho
    if i == 1 and j == 1:
        return 1 - rho
    return 1.0


def fit_league(comp, rows, now):
    """rows: [(siku_tangu, nyumbani, ugenini, magoli_n, magoli_u)] — Poisson yenye uzito, kisha rho."""
    lg = League(comp=comp)
    data = []
    for age, h, a, hg, ag in rows:
        w = 0.5 ** (age / HALF_LIFE)
        data.append((w, h, a, hg, ag))
        for t in (h, a):
            lg.games[t] = lg.games.get(t, 0) + w
            lg.raw_games[t] = lg.raw_games.get(t, 0) + 1
    lg.n = len(data)
    teams = list(lg.games)
    att = {t: 1.0 for t in teams}
    dfn = {t: 1.0 for t in teams}
    sw = sum(d[0] for d in data) or 1
    lg.draw_rate = sum(d[0] for d in data if d[3] == d[4]) / sw
    mu = max(0.4, sum(d[0] * d[4] for d in data) / sw)
    home = max(0.8, min(1.6, (sum(d[0] * d[3] for d in data) / sw) / mu))
    for _ in range(ITERATIONS):
        num, den = defaultdict(float), defaultdict(float)
        for w, h, a, hg, ag in data:
            num[h] += w * hg
            den[h] += w * mu * home * dfn[a]
            num[a] += w * ag
            den[a] += w * mu * dfn[h]
        for t in teams:
            att[t] = (num[t] + PRIOR) / (den[t] + PRIOR)
        num, den = defaultdict(float), defaultdict(float)
        for w, h, a, hg, ag in data:
            num[a] += w * hg
            den[a] += w * mu * home * att[h]
            num[h] += w * ag
            den[h] += w * mu * att[a]
        for t in teams:
            dfn[t] = (num[t] + PRIOR) / (den[t] + PRIOR)
        # kiwango: wastani wa kijiometri wa mashambulizi na ulinzi = 1 (mu na home vinabeba ukubwa)
        ga = math.exp(sum(math.log(v) for v in att.values()) / len(att))
        gd = math.exp(sum(math.log(v) for v in dfn.values()) / len(dfn))
        att = {t: v / ga for t, v in att.items()}
        dfn = {t: v / gd for t, v in dfn.items()}
        ah = sum(w * mu * att[h] * dfn[a] for w, h, a, _, _ in data)
        aa = sum(w * mu * att[a] * dfn[h] for w, h, a, _, _ in data)
        home = max(0.8, min(1.6, sum(w * hg for w, _, _, hg, _ in data) / max(1e-9, ah)))
        mu = max(0.3, min(2.5, mu * sum(w * ag for w, _, _, _, ag in data) / max(1e-9, aa)))
    lg.attack, lg.defence, lg.mu, lg.home = att, dfn, mu, home
    best, best_ll = lg.rho, None
    for rho in RHO_GRID:
        ll = 0.0
        ok = True
        for w, h, a, hg, ag in data:
            if hg > 1 or ag > 1:
                continue
            lh, la = mu * home * att[h] * dfn[a], mu * att[a] * dfn[h]
            t = _tau(hg, ag, lh, la, rho)
            if t <= 0:
                ok = False
                break
            ll += w * math.log(t)
        if ok and (best_ll is None or ll > best_ll):
            best, best_ll = rho, ll
    lg.rho = best
    return lg


def build(before=None, rows=None):
    """Modeli kamili kutoka hifadhi (mechi za kabla ya `before` tu)."""
    from news.models import MatchResult
    now = before or timezone.now()
    if rows is None:
        qs = MatchResult.objects.filter(played_at__lt=now)
        rows = list(qs.only("competition_id", "country", "played_at", "home", "away", "home_goals", "away_goals"))
    model = Model(matches=len(rows), built_at=now.isoformat())
    per_league = defaultdict(list)
    for r in sorted(rows, key=lambda r: r.played_at):
        hk, ak = match_elo_keys(model, r)
        elo_update(model, hk, ak, r.home_goals, r.away_goals)
        age = (now - r.played_at).total_seconds() / 86400
        if r.competition_id and age <= MAX_AGE_DAYS:
            per_league[r.competition_id].append((max(0.0, age), team_key(r.home), team_key(r.away),
                                                 r.home_goals, r.away_goals))
    draws = total = 0
    for comp, data in per_league.items():
        if len(data) >= MIN_LEAGUE_GAMES:
            model.leagues[comp] = fit_league(comp, data, now)
        draws += sum(1 for d in data if d[3] == d[4])
        total += len(data)
    if total >= 200:          # sare za ligi zetu kwa jumla (Elo haijui magoli)
        model.draw_max = max(0.18, min(0.36, draws / total + 0.03))
    return model


def _signature():
    from django.db.models import Count, Max
    from news.models import MatchResult
    agg = MatchResult.objects.aggregate(n=Count("id"), last=Max("id"))
    return f"{agg['n']}:{agg['last']}"


def current():
    sig = _signature()
    data = cache.get(CACHE_KEY)
    if data and data.get("sig") == sig:
        return data["model"]
    model = build()
    cache.set(CACHE_KEY, {"sig": sig, "model": model}, CACHE_SECONDS)
    return model


def forget():
    cache.delete(CACHE_KEY)

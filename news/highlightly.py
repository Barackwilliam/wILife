"""
Highlightly (highlightly.net) — mtoaji wa data za mechi kwa LIVE ya kujiendesha (news/football.py).

Kwa nini: API-Football inasimamisha akaunti za bure zinazoita kutoka IP za pamoja za cloud
(Render). Highlightly ina mpango wa bure (maombi 100 kwa siku) wenye ligi 950+ na matukio.

Muundo wa data umechukuliwa kutoka mfano wao rasmi (github.com/Highlightly/world-cup-tracker):
    GET /matches?date=YYYY-MM-DD&timezone=..&limit=100&offset=0  -> {"data": [...], "pagination": {...}}
    GET /matches/{id}                                            -> [match]  (pamoja na events, venue)
    match: id, date, league{id,name,logo}, country{name}, homeTeam{id,name,logo}, awayTeam{...},
           state{description, clock, score{current: "1 - 0"}}, venue{name,city},
           events[{type, time, team{id,name}, player, assist, substituted}]

Kila mechi inageuzwa kuwa muundo ule ule wa API-Football ambao football.apply() unaujua, kwa
hiyo ubao wa matokeo, matukio ya Kiswahili, picha za nembo na SEO havibadiliki.

Jibu la kwanza halisi la API linahifadhiwa kwenye FootballState.sample (Admin) — ukaguzi wa
majina ya sehemu dhidi ya data halisi.
"""
import json
import re

import requests
from django.conf import settings

from news.models import FootballState

BASE = "https://soccer.highlightly.net"
RAPIDAPI_BASE = "https://football-highlights-api.p.rapidapi.com"
RAPIDAPI_HOST = "football-highlights-api.p.rapidapi.com"
PAGE = 100
MAX_PAGES = 15           # ratiba ya siku nzima (mechi za dunia nzima): hadi mechi 1500, mara moja kwa siku
_scan = {"pages": 0, "total": 0, "rows": 0}


def key():
    return getattr(settings, "HIGHLIGHTLY_API_KEY", "")


def _base_and_headers():
    headers = {"x-rapidapi-key": key()}
    if getattr(settings, "HIGHLIGHTLY_VIA_RAPIDAPI", False):
        headers["x-rapidapi-host"] = RAPIDAPI_HOST
        return RAPIDAPI_BASE, headers
    return BASE, headers


def _get(path, params, state):
    from news.football import spend
    spend(state)
    base, headers = _base_and_headers()
    r = requests.get(f"{base}{path}", params=params, headers=headers, timeout=20)
    remaining = r.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None and remaining.lstrip("-").isdigit():
        state.remaining = int(remaining)
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("message") or body.get("error") or str(body)
        except ValueError:
            msg = r.text
        raise RuntimeError(f"Highlightly {r.status_code}: {str(msg)[:250]}")
    return r.json()


def _sample(state, raw):
    """Hifadhi mfano mmoja wa jibu halisi (mara moja kwa siku) — kuhakiki majina ya sehemu."""
    if state.sample:
        return
    state.sample = json.dumps(raw, ensure_ascii=False, default=str)[:6000]
    FootballState.objects.filter(pk=state.pk).update(sample=state.sample)


# ── Ligi tunazofuatilia ────────────────────────────────────────────────────────

def rules():
    """
    HIGHLIGHTLY_LEAGUES: "Nchi:Ligi" kwa kipaumbele, mf. "England:Premier League,*:UEFA Champions League".
    '*' = nchi yoyote (mashindano ya kimataifa).
    """
    out = []
    for part in getattr(settings, "HIGHLIGHTLY_LEAGUES", []):
        country, _, league = part.partition(":")
        if league.strip():
            out.append((country.strip().lower(), league.strip().lower()))
    return out


def _country(raw):
    c = raw.get("country") or (raw.get("league") or {}).get("country") or {}
    return (c.get("name") if isinstance(c, dict) else str(c or "")).strip()


def rank(raw):
    """Nafasi ya ligi ya mechi kwenye orodha yetu, au None kama haitufai."""
    league = ((raw.get("league") or {}).get("name") or "").strip().lower()
    country = _country(raw).lower()
    for i, (want_country, want_league) in enumerate(rules()):
        if league == want_league and want_country in ("*", country):
            return i
    return None


# ── Muundo wa Highlightly -> muundo wa API-Football ───────────────────────────

def _status(raw):
    st = raw.get("state") or {}
    d = (st.get("description") or "").strip().lower()
    if not d or d in ("not started", "to be announced", "tba"):
        return "NS"
    if "finished" in d or d in ("ended", "full time", "after penalties", "after extra time"):
        if "penalt" in d:
            return "PEN"
        return "AET" if "extra" in d else "FT"
    if "half time" in d or d == "halftime":
        return "HT"
    if "postpon" in d:
        return "PST"
    if "cancel" in d:
        return "CANC"
    if "abandon" in d:
        return "ABD"
    if "suspend" in d or "interrupt" in d:
        return "SUSP"
    if "penalt" in d:
        return "P"
    if "break" in d:
        return "BT"
    if "extra" in d:
        return "ET"
    if "first" in d or d == "1st half":
        return "1H"
    if "second" in d or d == "2nd half":
        return "2H"
    return "LIVE"


def _score(raw):
    cur = ((raw.get("state") or {}).get("score") or {}).get("current")
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", str(cur or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _clock(value):
    try:
        return int(str(value).split("+")[0])
    except (TypeError, ValueError):
        return None


def _time(value):
    """'45+2' / 45 / "67'" -> (elapsed, extra)."""
    m = re.match(r"\s*(\d+)\s*(?:\+\s*(\d+))?", str(value or ""))
    if not m:
        return None, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def _event(ev, raw):
    t = (ev.get("type") or "").strip()
    tl = t.lower()
    team = ev.get("team") or {}
    if not isinstance(team, dict):
        team = {"name": str(team)}
    player = ev.get("player") or ""
    if isinstance(player, dict):
        player = player.get("name") or ""
    assist = ev.get("assist") or ""
    if isinstance(assist, dict):
        assist = assist.get("name") or ""
    elapsed, extra = _time(ev.get("time"))
    if "own goal" in tl:
        typ, detail = "Goal", "Own Goal"
    elif "penalty" in tl and "miss" in tl:
        typ, detail = "Goal", "Missed Penalty"
    elif "penalty" in tl and "card" not in tl:
        typ, detail = "Goal", "Penalty"
    elif "goal" in tl and "disallow" not in tl and "cancel" not in tl:
        typ, detail = "Goal", "Normal Goal"
    elif "yellow" in tl and ("second" in tl or "2nd" in tl):
        typ, detail = "Card", "Second Yellow card"
    elif "yellow" in tl:
        typ, detail = "Card", "Yellow Card"
    elif "red" in tl:
        typ, detail = "Card", "Red Card"
    elif "subst" in tl:
        # Highlightly: player = anaingia, substituted = anatoka
        typ, detail = "subst", "Substitution"
        assist = ev.get("substituted") or assist
        if isinstance(assist, dict):
            assist = assist.get("name") or ""
    elif "var" in tl or "disallow" in tl or "cancel" in tl:
        typ, detail = "Var", t
    else:
        typ, detail = t, t
    return {"time": {"elapsed": elapsed, "extra": extra},
            "team": {"id": team.get("id"), "name": team.get("name") or ""},
            "player": {"id": None, "name": player}, "assist": {"id": None, "name": assist},
            "type": typ, "detail": detail}


def normalize(raw):
    """Mechi moja ya Highlightly -> kipengele chenye muundo wa API-Football (football._fill/apply)."""
    league = raw.get("league") or {}
    home, away = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
    venue = raw.get("venue") or {}
    hg, ag = _score(raw)
    status = _status(raw)
    item = {
        "fixture": {"id": int(raw["id"]), "date": raw.get("date"),
                    "status": {"short": status, "elapsed": _clock((raw.get("state") or {}).get("clock"))},
                    "venue": {"name": venue.get("name"), "city": venue.get("city")}},
        "league": {"id": league.get("id"), "name": league.get("name"), "logo": league.get("logo"),
                   "round": raw.get("round") or league.get("round") or "", "country": _country(raw)},
        "teams": {"home": {"id": home.get("id"), "name": home.get("name"), "logo": home.get("logo")},
                  "away": {"id": away.get("id"), "name": away.get("name"), "logo": away.get("logo")}},
        "goals": {"home": hg if status != "NS" else None, "away": ag if status != "NS" else None},
    }
    if "events" in raw:
        item["events"] = [_event(e, raw) for e in raw.get("events") or [] if isinstance(e, dict)]
    extra = {"home_id": home.get("id"), "away_id": away.get("id"), "league_id": league.get("id"),
             "season": league.get("season")}
    if raw.get("statistics"):
        extra["stats"] = stats(raw["statistics"], home.get("id"))
    ref = raw.get("referee")
    if isinstance(ref, dict) and ref.get("name"):
        extra["referee"] = ref["name"]
    fc = raw.get("forecast")
    if isinstance(fc, dict) and (fc.get("temperature") or fc.get("status")):
        extra["forecast"] = {"temperature": str(fc.get("temperature") or ""), "status": str(fc.get("status") or "")}
    item["_extra"] = {k: v for k, v in extra.items() if v not in (None, "", [])}
    return item


STAT_SW = {
    "possession": "Umiliki wa mpira", "ball possession": "Umiliki wa mpira",
    "expected goals": "Nafasi za kufunga (xG)", "total shots": "Mashuti yote", "shots": "Mashuti yote",
    "shots on target": "Mashuti yaliyolenga lango", "shots off target": "Mashuti nje ya lango",
    "blocked shots": "Mashuti yaliyozuiwa", "corners": "Kona", "corner kicks": "Kona", "fouls": "Faulo",
    "offsides": "Kuotea", "yellow cards": "Kadi za njano", "red cards": "Kadi nyekundu",
    "saves": "Kuokoa (kipa)", "goalkeeper saves": "Kuokoa (kipa)", "successful passes": "Pasi zilizofika",
    "total passes": "Pasi zote", "passes": "Pasi zote", "pass accuracy": "Usahihi wa pasi",
    "big chances": "Nafasi kubwa", "free kicks": "Mipira ya adhabu", "throw-ins": "Mirusho",
}
STAT_ORDER = ["Umiliki wa mpira", "Nafasi za kufunga (xG)", "Mashuti yote", "Mashuti yaliyolenga lango",
              "Nafasi kubwa", "Kona", "Pasi zilizofika", "Usahihi wa pasi", "Faulo", "Kuotea", "Kadi za njano",
              "Kadi nyekundu", "Kuokoa (kipa)"]


def _num(v):
    if isinstance(v, (int, float)):
        return v
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def stats(raw, home_id=None):
    """[{team, statistics:[{displayName, value}]} x2] -> [{name, home, away, pct}] kwa Kiswahili."""
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    a, b = raw[0] or {}, raw[1] or {}
    if home_id is not None and (b.get("team") or {}).get("id") == home_id:
        a, b = b, a
    def table(side):
        out = {}
        for s in side.get("statistics") or []:
            if isinstance(s, dict) and s.get("displayName"):
                out[s["displayName"].strip()] = s.get("value")
        return out
    ha, aw = table(a), table(b)
    rows = []
    for name in ha:
        if name not in aw:
            continue
        hv, av = _num(ha[name]), _num(aw[name])
        if hv is None or av is None:
            continue
        pct_stat = "possession" in name.lower() or "accuracy" in name.lower()
        if pct_stat and hv <= 1 and av <= 1:
            hv, av = round(hv * 100), round(av * 100)
        fmt = (lambda x: f"{x:g}%") if pct_stat else (lambda x: f"{round(x, 2):g}")
        total = hv + av
        rows.append({"name": STAT_SW.get(name.lower(), name), "home": fmt(hv), "away": fmt(av),
                     "pct": round(hv / total * 100) if total else 50})
    rank = {n: i for i, n in enumerate(STAT_ORDER)}
    rows.sort(key=lambda r: rank.get(r["name"], 99))
    return rows[:14]


# ── Maombi ────────────────────────────────────────────────────────────────────

def schedule(state, day):
    """Mechi za siku za ligi zetu, kwa kipaumbele. Kurasa kadhaa (100 kwa ukurasa)."""
    found = []
    _scan.update(pages=0, total=0, rows=0)
    for page in range(MAX_PAGES):
        data = _get("/matches", {"date": day.isoformat(), "timezone": settings.TIME_ZONE,
                                 "limit": PAGE, "offset": page * PAGE}, state)
        rows = data.get("data") if isinstance(data, dict) else data
        rows = rows or []
        _scan["pages"] += 1
        _scan["rows"] += len(rows)
        if rows:
            _sample(state, rows[0])
        for raw in rows:
            r = rank(raw)
            if r is not None and raw.get("id") is not None:
                item = normalize(raw)
                item["_rank"] = r
                found.append((r, raw.get("date") or "", item))
        total = ((data.get("pagination") or {}).get("totalCount") if isinstance(data, dict) else None) or 0
        _scan["total"] = total
        if len(rows) < PAGE or (page + 1) * PAGE >= total:
            break
    found.sort(key=lambda x: (x[0], x[1]))
    return [item for _, _, item in found]


def last_scan_note():
    """Mf. 'kurasa 9, mechi 870/870' — ionekane kwenye Agent runs kama ratiba ilikatika."""
    total = _scan["total"] or _scan["rows"]
    cut = " (IMEKATIKA — ongeza MAX_PAGES)" if _scan["total"] and _scan["rows"] < _scan["total"] else ""
    return f"kurasa {_scan['pages']}, mechi {_scan['rows']}/{total}{cut}"


def match(state, m):
    """Mechi moja kwa undani (matukio yote). Ombi 1."""
    data = _get(f"/matches/{m.fixture_id}", {"timezone": settings.TIME_ZONE}, state)
    raw = data[0] if isinstance(data, list) and data else (data.get("data") if isinstance(data, dict) else None)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return []
    raw.setdefault("events", [])
    return [normalize(raw)]

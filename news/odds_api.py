"""
Odds za kampuni za kimataifa kutoka The Odds API (the-odds-api.com) — ufunguo ODDS_API_KEY (Render -> Environment).

    fetch(now)   kwa kila ligi (sport) yenye mechi zetu za ratiba ndani ya saa 48: ombi 1 la odds za 1X2 na
                 magoli juu/chini (h2h, totals), kisha kila tukio linaunganishwa na mechi yetu kwa majina ya timu
                 na muda wa kuanza. Zinahifadhiwa kwenye Fixture.extra["odds"] (na LiveMatch ya mechi hiyo).

Mpango wa bure una "credits" chache kwa mwezi: kila ombi linagharimu masoko x maeneo (2 x 1 = 2). Kwa hiyo
ligi inasomwa tu ikiwa na mechi zetu zinazokuja, mara moja kila EVERY, na kwa kikomo cha siku ODDS_API_PER_DAY.
Ligi Kuu Tanzania haipo kwenye huduma hii — odds za kampuni za hapa zinawekwa na mhariri kwenye Admin.
"""
import difflib
import logging
import re
import unicodedata
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

log = logging.getLogger("news.odds_api")

BASE = "https://api.the-odds-api.com/v4"
EVERY = timedelta(hours=12)
AHEAD = timedelta(hours=48)
MATCH_WINDOW = timedelta(hours=3)
MIN_REMAINING = 10           # credits za mwezi zisizotumika kamwe (akiba)
PROGRESS_KEY = "oddsapi-progress"
SOURCE = "The Odds API"

# sport ya The Odds API -> (nchi, maneno ya jina la mashindano kwenye ratiba yetu)
SPORTS = {
    "soccer_epl": ("england", "premier league"),
    "soccer_spain_la_liga": ("spain", "la liga"),
    "soccer_italy_serie_a": ("italy", "serie a"),
    "soccer_germany_bundesliga": ("germany", "bundesliga"),
    "soccer_france_ligue_one": ("france", "ligue 1"),
    "soccer_uefa_champs_league": ("", "uefa champions league"),
    "soccer_uefa_europa_league": ("", "uefa europa league"),
}
_DROP = re.compile(r"\b(fc|afc|cf|sc|ac|club|calcio|de|the)\b")


def key():
    return getattr(settings, "ODDS_API_KEY", "")


def _name(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = _DROP.sub(" ", re.sub(r"[^a-z0-9 ]", " ", s))
    return " ".join(s.split())


def similar(a, b):
    a, b = _name(a), _name(b)
    if not a or not b:
        return 0.0
    if a == b or (len(a) >= 5 and a in b) or (len(b) >= 5 and b in a):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fixtures(sport, now):
    from news.models import Fixture
    country, words = SPORTS[sport]
    out = []
    for f in Fixture.objects.filter(status="NS", kickoff__gt=now, kickoff__lte=now + AHEAD):
        comp = (f.competition or "").lower()
        if words in comp and (not country or (f.country or "").lower() == country):
            out.append(f)
    return out


def match(event, fixtures):
    """Mechi yetu inayolingana na tukio la The Odds API (majina ya timu + muda), au None."""
    try:
        start = datetime.fromisoformat(str(event.get("commence_time", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    best, best_score = None, 0.0
    for f in fixtures:
        if not f.kickoff or abs(f.kickoff - start) > MATCH_WINDOW:
            continue
        h, a = similar(event.get("home_team"), f.home), similar(event.get("away_team"), f.away)
        score = (h + a) / 2
        if min(h, a) >= 0.6 and score >= 0.75 and score > best_score:
            best, best_score = f, score
    return best


def prices(event):
    """Tukio -> {(soko, kampuni): [bei]} kwa summarise() ya news/odds.py."""
    out = {}
    home, away = event.get("home_team"), event.get("away_team")
    for book in event.get("bookmakers") or []:
        title = book.get("title") or book.get("key") or "?"
        for mk in book.get("markets") or []:
            outs = mk.get("outcomes") or []
            if mk.get("key") == "h2h":
                row = [None, None, None]
                for o in outs:
                    idx = 0 if o.get("name") == home else (2 if o.get("name") == away else
                                                           (1 if str(o.get("name")).lower() == "draw" else None))
                    if idx is not None:
                        row[idx] = _price(o)
                if all(row):
                    out[("1x2", title)] = row
            elif mk.get("key") == "totals":
                row = [None, None]
                for o in outs:
                    if _point(o) != 2.5:
                        continue
                    n = str(o.get("name")).lower()
                    if n in ("over", "under"):
                        row[0 if n == "over" else 1] = _price(o)
                if all(row):
                    out[("ou25", title)] = row
    return out


def _price(o):
    from news.odds import _num
    return _num(o.get("price"))


def _point(o):
    try:
        return float(o.get("point"))
    except (TypeError, ValueError):
        return None


def _store(fixture, summary, now):
    from news.models import Fixture, LiveMatch
    data = {**summary, "at": now.isoformat(), "source": SOURCE}
    for model, objs in ((Fixture, [fixture]), (LiveMatch, LiveMatch.objects.filter(fixture_id=fixture.fixture_id))):
        for obj in objs:
            if obj.status != "NS":
                continue
            obj.extra = {**(obj.extra or {}), "odds": data}
            model.objects.filter(pk=obj.pk).update(extra=obj.extra)


def fetch(now=None):
    """Ligi zilizofika zamu. Inarudisha idadi ya maombi yaliyotumika."""
    from news import odds
    from news.models import DailyCount
    if not key():
        return 0
    now = now or timezone.now()
    rep = odds._report(PROGRESS_KEY)
    prog = dict(rep.data or {})
    per_day = int(getattr(settings, "ODDS_API_PER_DAY", 14))
    regions = getattr(settings, "ODDS_API_REGIONS", "eu")
    cost = 2 * len([r for r in regions.split(",") if r.strip()])
    spent = 0
    for sport in SPORTS:
        if DailyCount.get("oddsapi:credits") + cost > per_day:
            prog["_hali"] = {"at": now.isoformat(), "sababu": f"kikomo cha siku ({per_day} credits) kimefikiwa"}
            break
        if (prog.get("_hali") or {}).get("remaining") is not None and prog["_hali"]["remaining"] < MIN_REMAINING:
            break
        job = prog.get(sport) or {}
        if job.get("at") and now - datetime.fromisoformat(job["at"]) < EVERY:
            continue
        fixtures = _fixtures(sport, now)
        if not fixtures:
            continue
        try:
            r = requests.get(f"{BASE}/sports/{sport}/odds", timeout=20, params={
                "apiKey": key(), "regions": regions, "markets": "h2h,totals", "oddsFormat": "decimal",
                "dateFormat": "iso"})
        except requests.RequestException as exc:
            prog[sport] = {"at": now.isoformat(), "error": str(exc)[:200]}
            break
        spent += 1
        used = r.headers.get("x-requests-last")
        DailyCount.bump("oddsapi:credits", by=int(used) if str(used or "").isdigit() else cost)
        remaining = r.headers.get("x-requests-remaining")
        if r.status_code >= 400:
            prog[sport] = {"at": now.isoformat(), "error": f"{r.status_code}: {r.text[:200]}"}
            log.warning("odds api %s failed: %s", sport, prog[sport]["error"])
            if r.status_code in (401, 429):
                break
            continue
        events = r.json() if isinstance(r.json(), list) else []
        stored = 0
        for ev in events:
            f = match(ev, fixtures)
            if not f:
                continue
            p = prices(ev)
            if p:
                _store(f, odds.summarise(p), now)
                stored += 1
        prog[sport] = {"at": now.isoformat(), "events": len(events), "fixtures": len(fixtures), "stored": stored}
        prog["_hali"] = {"at": now.isoformat(), "sababu": "inafanya kazi",
                         "remaining": int(float(remaining)) if remaining not in (None, "") else None}
    if spent or prog != (rep.data or {}):
        rep.data = prog
        rep.save(update_fields=["data", "updated_at"])
    return spent

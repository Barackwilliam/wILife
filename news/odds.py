"""
Odds za kampuni za kubeti (Highlightly /odds — ufunguo ule ule wa API, maombi ya siku yale yale).

    fetch(state, now, left)   odds za mechi za leo na kesho (kabla ya mechi), kurasa chache kwa siku, maombi
                              ya ziada tu. Zinahifadhiwa kwenye Fixture.extra["odds"] / LiveMatch.extra["odds"]:
        {"1x2": {"avg": [n, s, u], "best": [n, s, u], "books": k},
         "ou25": {"avg": [juu, chini], ...}, "btts": {"avg": [ndiyo, hapana], ...},
         "fair": {"1x2": [...], ...}   uwezekano wa soko baada ya kuondoa faida ya kampuni (margin)
         "margin": {"1x2": 0.05, ...}, "at": "..."}
    compare(p, odds)          utabiri wetu dhidi ya soko: tofauti na "thamani" (EV) ya kila chaguo
    settle_value(obj)         baada ya mechi: chaguo la thamani lingeleta faida au hasara kiasi gani

Muundo wa jibu la /odds haujathibitishwa kwenye nyaraka: msomaji hapa ni mvumilivu (majina kadhaa ya
sehemu yanakubaliwa) na mfano wa jibu halisi unahifadhiwa (Admin -> Model reports -> "odds-sample").
Kisichoeleweka hakionyeshwi — hakuna odds za kubuni.
"""
import json
import logging
from datetime import datetime, timedelta

from django.utils import timezone

log = logging.getLogger("news.odds")

PAGE = 100
MIN_LEFT = 35              # maombi ya siku yanayohitajika kabla ya kusoma odds (LIVE na ratiba kwanza)
PER_DAY = 8                # kikomo cha maombi ya odds kwa siku
MAX_PAGES = 3              # kwa siku moja ya mechi, kwa mzunguko mmoja
EVERY = timedelta(hours=6)  # odds hubadilika: siku hiyo inasomwa tena baada ya muda huu
SAMPLE_KEY = "odds-sample"
PROGRESS_KEY = "odds-progress"

VALUE_EDGE = 0.05          # EV ya chini ya chaguo la "thamani" (5%) kwa odds za wastani
VALUE_MIN_P = 0.25         # chaguo zenye nafasi ndogo sana (longshots) hazitajwi
MIN_ODD, MAX_ODD = 1.01, 100.0

MARKETS = {
    "1x2": ("full time result", "match winner", "1x2", "three way", "fulltime result", "match result", "winner"),
    "ou25": ("over/under", "over under", "goals over/under", "total goals", "totals"),
    "btts": ("both teams to score", "both teams score", "btts", "goal/no goal", "gg/ng"),
}


# ── Kusoma jibu (mvumilivu) ───────────────────────────────────────────────────

def _num(v):
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if MIN_ODD <= f <= MAX_ODD else None


def _text(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("value")
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return ""


def _match_id(item):
    for k in ("matchId", "match_id", "fixtureId", "fixture_id"):
        if str(item.get(k) or "").isdigit():
            return int(item[k])
    m = item.get("match") or item.get("fixture")
    if isinstance(m, dict) and str(m.get("id") or "").isdigit():
        return int(m["id"])
    return None


def _market_kind(name, outcomes):
    n = name.lower()
    for kind, words in MARKETS.items():
        if any(w in n for w in words):
            if kind == "1x2" and ("half" in n or "1st" in n or "2nd" in n or "handicap" in n or "draw no bet" in n):
                return None
            if kind == "ou25" and ("half" in n or "corner" in n or "card" in n or "team" in n):
                return None
            if kind == "btts" and "half" in n:
                return None
            return kind
    return None


def _outcome(kind, label, line=None):
    """Lebo ya chaguo -> nafasi yake kwenye soko (au None)."""
    lab = label.lower().replace("-", " ").strip()
    if kind == "1x2":
        return {"home": 0, "1": 0, "draw": 1, "x": 1, "away": 2, "2": 2}.get(lab)
    if kind == "ou25":
        text = f"{lab} {line or ''}"
        if "2.5" not in text:
            return None
        return 0 if lab.startswith("over") else (1 if lab.startswith("under") else None)
    if kind == "btts":
        return {"yes": 0, "no": 1, "gg": 0, "ng": 1}.get(lab)
    return None


def _entries(node, book=""):
    """Kila (bookmaker, jina la soko, [chaguo]) ndani ya kipengele — kwa kina chochote."""
    if isinstance(node, list):
        for x in node:
            yield from _entries(x, book)
        return
    if not isinstance(node, dict):
        return
    book = _text(node, "bookmakerName", "bookmaker", "bookmaker_name") or book
    market = _text(node, "market", "marketName", "market_name", "name", "type")
    outcomes = None
    for k in ("values", "outcomes", "odds", "selections", "bets"):
        v = node.get(k)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            if any(_num(x.get("odd") or x.get("odds") or x.get("price")) for x in v):
                outcomes = v
                break
    if market and outcomes:
        yield book, market, node.get("line") or node.get("handicap") or node.get("total"), outcomes
        return
    for v in node.values():
        if isinstance(v, (list, dict)):
            yield from _entries(v, book)


def parse(rows):
    """[kipengele cha API] -> {match_id: odds} (muundo wa juu ya faili)."""
    out = {}
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        mid = _match_id(item)
        if mid is None:
            continue
        prices = {}                      # (kind, book) -> [bei]
        for book, market, line, outcomes in _entries(item):
            kind = _market_kind(market, outcomes)
            if not kind:
                continue
            size = 3 if kind == "1x2" else 2
            row = [None] * size
            for o in outcomes:
                idx = _outcome(kind, _text(o, "value", "name", "label", "outcome"), o.get("line") or line or market)
                odd = _num(o.get("odd") or o.get("odds") or o.get("price"))
                if idx is not None and odd:
                    row[idx] = odd
            if all(row):
                prices[(kind, book or f"#{len(prices)}")] = row
        if prices:
            out[mid] = summarise(prices)
    return out


def summarise(prices):
    res = {"fair": {}, "margin": {}}
    for kind in MARKETS:
        rows = [r for (k, _), r in prices.items() if k == kind]
        if not rows:
            continue
        n = len(rows[0])
        avg = [round(sum(r[i] for r in rows) / len(rows), 2) for i in range(n)]
        best = [round(max(r[i] for r in rows), 2) for i in range(n)]
        inv = [1 / a for a in avg]
        res[kind] = {"avg": avg, "best": best, "books": len(rows)}
        res["fair"][kind] = [round(x / sum(inv), 4) for x in inv]
        res["margin"][kind] = round(sum(inv) - 1, 4)
    return res


# ── Kusoma kutoka API ─────────────────────────────────────────────────────────

def _report(key):
    from news.models import ModelReport
    rep, _ = ModelReport.objects.get_or_create(key=key, defaults={"data": {}})
    return rep


def _store(found, now):
    from news.models import Fixture, LiveMatch
    n = 0
    stamp = now.isoformat()
    for model in (Fixture, LiveMatch):
        for obj in model.objects.filter(fixture_id__in=list(found)).exclude(status__in=LiveMatch.FINISHED):
            if obj.status != "NS":
                continue                   # odds za kabla ya mechi tu: zinafungwa mechi ikianza
            x = obj.extra or {}
            obj.extra = {**x, "odds": {**found[obj.fixture_id], "at": stamp}}
            model.objects.filter(pk=obj.pk).update(extra=obj.extra)
            n += 1
    return n


def fetch(state, now, left):
    """Kurasa za odds za leo/kesho zilizopitwa na muda. Inarudisha maombi yaliyotumika."""
    from django.conf import settings
    from news import football, highlightly
    from news.models import DailyCount
    if (football.provider() != "highlightly" or left < MIN_LEFT
            or not getattr(settings, "ODDS_ENABLED", True)):
        return 0
    used = DailyCount.get("odds:requests")
    if used >= PER_DAY:
        return 0
    rep = _report(PROGRESS_KEY)
    prog = dict(rep.data or {})
    today = timezone.localtime(now).date()
    spent = 0
    for day in (today, today + timedelta(days=1)):
        key = day.isoformat()
        job = prog.get(key) or {}
        last = job.get("at")
        if last and now - datetime.fromisoformat(last) < EVERY:
            continue
        if job.get("error_at") and now - datetime.fromisoformat(job["error_at"]) < timedelta(hours=24):
            continue                       # mpango wa API hauruhusu au kosa: jaribu kesho
        found, pages, error = {}, 0, ""
        for page in range(MAX_PAGES):
            if used + spent >= PER_DAY:
                break
            try:
                data = highlightly._get("/odds", {"date": key, "timezone": settings.TIME_ZONE,
                                                  "oddsType": "prematch", "limit": PAGE, "offset": page * PAGE}, state)
            except Exception as exc:
                spent += 1
                error = str(exc)[:300]
                log.warning("odds %s failed: %s", key, exc)
                break
            spent += 1
            pages += 1
            rows = (data.get("data") if isinstance(data, dict) else data) or []
            if page == 0 and rows:            # mfano wa jibu halisi — kuhakiki majina ya sehemu
                sample = _report(SAMPLE_KEY)
                sample.data = {"at": now.isoformat(), "raw": json.dumps(rows[0], ensure_ascii=False, default=str)[:6000]}
                sample.save(update_fields=["data", "updated_at"])
            found.update(parse(rows))
            total = ((data.get("pagination") or {}).get("totalCount") if isinstance(data, dict) else None) or 0
            if len(rows) < PAGE or (total and (page + 1) * PAGE >= total):
                break
        stored = _store(found, now) if found else 0
        prog[key] = {"at": now.isoformat(), "pages": pages, "parsed": len(found), "stored": stored}
        if error:
            prog[key].update(error=error, error_at=now.isoformat())
        if used + spent >= PER_DAY:
            break
    if spent:
        DailyCount.bump("odds:requests", by=spent)
        state.save(update_fields=["requests", "remaining"])
        keep = {k: v for k, v in prog.items() if k >= (today - timedelta(days=3)).isoformat()}
        rep.data = keep
        rep.save(update_fields=["data", "updated_at"])
    return spent


# ── Utabiri dhidi ya soko ─────────────────────────────────────────────────────

LABELS = {"1x2": ("1", "X", "2"), "ou25": ("Magoli 3+", "Chini ya 3"), "btts": ("Zote kufunga", "Si zote kufunga")}


def ours(p, kind):
    """Uwezekano wetu (0–1) kwa soko hili, kutoka utabiri uliohifadhiwa."""
    if kind == "1x2":
        return [p["home"] / 100, p["draw"] / 100, p["away"] / 100]
    mk = p.get("markets") or {}
    if kind == "ou25" and "over25" in mk:
        return [mk["over25"] / 100, 1 - mk["over25"] / 100]
    if kind == "btts" and "btts" in mk:
        return [mk["btts"] / 100, 1 - mk["btts"] / 100]
    return None


def compare(p, odds):
    """
    Kila chaguo: {market, label, ours, market_p, odd, best, ev}. ev = uwezekano wetu x odds za wastani - 1:
    chanya ina maana utabiri wetu unaona nafasi kubwa kuliko bei ya kampuni. Inarudisha (mistari, thamani).
    """
    if not p or not odds:
        return [], None
    rows = []
    for kind in ("1x2", "ou25", "btts"):
        mk = odds.get(kind)
        mine = ours(p, kind)
        if not mk or not mine:
            continue
        fair = (odds.get("fair") or {}).get(kind) or []
        for i, label in enumerate(LABELS[kind]):
            ev = mine[i] * mk["avg"][i] - 1
            rows.append({"market": kind, "i": i, "label": label, "ours": round(mine[i] * 100),
                         "market_p": round(fair[i] * 100) if i < len(fair) else None,
                         "odd": mk["avg"][i], "best": mk["best"][i], "ev": round(ev * 100, 1)})
    value = None
    if p.get("confidence") in ("juu", "wastani"):
        cands = [r for r in rows if r["ev"] >= VALUE_EDGE * 100 and r["ours"] >= VALUE_MIN_P * 100]
        if cands:
            value = max(cands, key=lambda r: r["ev"])
    return rows, value


def view(obj):
    """Kwa template: {rows, value, books, margin, at} au None."""
    x = getattr(obj, "x", None) or getattr(obj, "extra", None) or {}
    odds, p = x.get("odds"), x.get("prediction")
    if not odds or not p:
        return None
    rows, value = compare(p, odds)
    if not rows:
        return None
    one = odds.get("1x2") or {}
    return {"rows": rows, "value": value, "books": one.get("books") or max(
        (odds[k]["books"] for k in MARKETS if k in odds), default=0),
        "margin": round((odds.get("margin") or {}).get("1x2", 0) * 100, 1) if one else None,
        "at": odds.get("at"), "x2": [r for r in rows if r["market"] == "1x2"]}


def settle_value(obj):
    """Baada ya mechi: chaguo la thamani (kama lilikuwepo) — faida kwa dau la 1 kwa odds za wastani."""
    x = obj.extra or {}
    if "value_result" in x or not obj.has_score:
        return None
    _, value = compare(x.get("prediction"), x.get("odds"))
    if not value:
        return None
    hg, ag = obj.home_goals, obj.away_goals
    actual = {"1x2": 0 if hg > ag else (1 if hg == ag else 2), "ou25": 0 if hg + ag >= 3 else 1,
              "btts": 0 if hg and ag else 1}[value["market"]]
    won = actual == value["i"]
    res = {"market": value["market"], "label": value["label"], "odd": value["odd"], "ev": value["ev"],
           "won": won, "profit": round(value["odd"] - 1, 2) if won else -1.0}
    obj.extra = {**x, "value_result": res}
    type(obj).objects.filter(pk=obj.pk).update(extra=obj.extra)
    return res


def market_hit(obj):
    """Je, kipenzi cha kampuni (1X2) kilishinda? None kama hakuna odds."""
    x = obj.extra or {}
    fair = ((x.get("odds") or {}).get("fair") or {}).get("1x2")
    if not fair or not obj.has_score:
        return None
    hg, ag = obj.home_goals, obj.away_goals
    actual = 0 if hg > ag else (1 if hg == ag else 2)
    return max(range(3), key=lambda i: fair[i]) == actual

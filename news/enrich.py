"""
Taarifa za ziada za mechi za Highlightly — zinazofanya ukurasa wa mechi uwe kamili:

    uso kwa uso (H2H) + fomu ya mechi 5 zilizopita   kabla ya mechi (makala inapata uchambuzi)
    vikosi (lineups)                                  saa moja kabla ya mechi hadi inapoanza
    video za magoli / highlights                      baada ya filimbi ya mwisho
    takwimu, mwamuzi, hali ya hewa                    zinakuja bure na kila sasisho la mechi

Maombi ni machache (mpango wa bure: 100 kwa siku), kwa hiyo kila taarifa inaombwa mara moja
tu, na tu kama maombi ya kutosha yamebaki kwa mechi zinazochezwa (LIVE_RESERVE). Njia ya API
ikikataa (mf. mpango hauruhusu), inazimwa kwa siku hiyo — haijaribiwi kila dakika.
"""
import json
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from news import highlightly, predict
from news.models import LiveMatch

log = logging.getLogger("news.enrich")

LIVE_RESERVE = 35        # maombi yanayobaki kwa ajili ya mechi zinazochezwa
PER_TICK = 2             # maombi ya ziada kwa tick moja, juu kabisa


def _left(state):
    from news.football import daily_limit
    return daily_limit() - state.requests


def _off(kind):
    return cache.get(f"enrich:off:{kind}")


def _call(state, kind, path, params, m):
    """Ombi moja la ziada. None likishindikana (na njia hiyo inazimwa kwa saa 24)."""
    try:
        data = highlightly._get(path, params, state)
    except Exception as exc:
        log.warning("enrich %s failed for %s: %s", kind, m, exc)
        if "40" in str(exc)[:15]:                    # 400/403/404: njia hairuhusiwi / haipo
            cache.set(f"enrich:off:{kind}", str(exc)[:200], 24 * 3600)
        _save(m, {f"{kind}_error": str(exc)[:200]})
        return None
    raw = data.get("data") if isinstance(data, dict) and "data" in data else data
    _save(m, {"raw": {**(m.extra or {}).get("raw", {}), kind: json.dumps(raw, ensure_ascii=False, default=str)[:3000]}})
    return raw


def _save(m, values):
    m.extra = {**(m.extra or {}), **values}
    LiveMatch.objects.filter(pk=m.pk).update(extra=m.extra)


# ── Uso kwa uso na fomu ───────────────────────────────────────────────────────

def _result(raw, team_id, team_name):
    """W/D/L kwa timu hii kwenye mechi moja (muundo wa mechi wa Highlightly), au None."""
    hg, ag = highlightly._score(raw)
    if hg is None:
        return None
    home, away = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
    is_home = home.get("id") == team_id or (team_id is None and home.get("name") == team_name)
    is_away = away.get("id") == team_id or (team_id is None and away.get("name") == team_name)
    if not (is_home or is_away):
        return None
    mine, theirs = (hg, ag) if is_home else (ag, hg)
    return "W" if mine > theirs else ("D" if mine == theirs else "L")


def _brief(raw):
    home, away = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
    hg, ag = highlightly._score(raw)
    return {"date": (raw.get("date") or "")[:10], "home": home.get("name") or "", "away": away.get("name") or "",
            "score": f"{hg} - {ag}" if hg is not None else "", "league": (raw.get("league") or {}).get("name") or ""}


def _finished(raw):
    return highlightly._status(raw) in LiveMatch.FINISHED


def _rows(raw):
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("matches") or []
    return [r for r in raw or [] if isinstance(r, dict)]


def head_to_head(state, m):
    x = m.extra or {}
    raw = _call(state, "h2h", "/head-2-head", {"teamIdOne": x.get("home_id"), "teamIdTwo": x.get("away_id")}, m)
    if raw is None:
        return
    games = [r for r in _rows(raw) if _finished(r)][:6]
    tally = {"W": 0, "D": 0, "L": 0}
    for g in games:
        r = _result(g, x.get("home_id"), m.home)
        if r:
            tally[r] += 1
    _save(m, {"h2h": [_brief(g) for g in games], "h2h_tally": tally, "h2h_done": True})


def form(state, m, side):
    x = m.extra or {}
    team_id = x.get(f"{side}_id")
    raw = _call(state, "form", "/last-five-games", {"teamId": team_id}, m)
    if raw is None:
        return
    games = [r for r in _rows(raw) if _finished(r)][:5]
    letters = [r for r in (_result(g, team_id, getattr(m, side)) for g in games) if r]
    _save(m, {f"form_{side}": letters, f"form_{side}_games": [_brief(g) for g in games]})


# ── Vikosi ────────────────────────────────────────────────────────────────────

def _players(obj, out=None):
    """Kusanya wachezaji ({name, number, position}) popote walipo ndani ya muundo."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("player") or obj.get("playerName")
        if isinstance(name, str) and name and not any(k in obj for k in ("initialLineup", "substitutes")):
            out.append({"name": name, "number": obj.get("number") or obj.get("shirtNumber") or "",
                        "position": obj.get("position") or ""})
            return out
        for v in obj.values():
            _players(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _players(v, out)
    return out


def _side(raw, key):
    side = raw.get(key) or {}
    if not isinstance(side, dict):
        return {}
    start = side.get("initialLineup") or side.get("startingLineup") or side.get("lineup") or side.get("startXI") or []
    subs = side.get("substitutes") or side.get("bench") or []
    return {"formation": str(side.get("formation") or ""), "start": _players(start)[:11], "subs": _players(subs)[:12]}


def lineups(state, m):
    raw = _call(state, "lineups", f"/lineups/{m.fixture_id}", {}, m)
    if raw is None:
        return
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    home, away = _side(raw, "homeTeam"), _side(raw, "awayTeam")
    if home.get("start") or away.get("start"):
        _save(m, {"lineups": {"home": home, "away": away}, "lineups_done": True})
    else:
        _save(m, {"lineups_try": timezone.now().isoformat()})     # bado havijatangazwa — jaribu baadaye


# ── Video ─────────────────────────────────────────────────────────────────────

def highlights(state, m):
    raw = _call(state, "highlights", "/highlights", {"matchId": m.fixture_id, "limit": 10}, m)
    if raw is None:
        return
    clips = []
    for h in _rows(raw):
        url = h.get("url") or h.get("embedUrl")
        if not url or not str(url).startswith("https://"):
            continue
        clips.append({"title": (h.get("title") or h.get("description") or "Video")[:140],
                      "url": url, "embed": h.get("embedUrl") or "", "img": h.get("imgUrl") or "",
                      "type": (h.get("type") or "").upper()[:20], "source": (h.get("source") or "")[:40]})
    tries = (m.extra or {}).get("highlights_tries", 0) + 1
    _save(m, {"highlights": clips[:8], "highlights_tries": tries,
              "highlights_done": bool(clips) or tries >= 3, "highlights_at": timezone.now().isoformat()})


# ── Uchambuzi wa Kiswahili (kwa ukurasa na makala) ─────────────────────────────



def _count(letters):
    return letters.count("W"), letters.count("D"), letters.count("L")


def insight_items(m, with_prediction=True):
    """
    Uchambuzi kwa vipengele vyenye kichwa ({label, text}), ili msomaji ajue kila takwimu inahusu nini:
    mechi walizokutana wao kwa wao ni tofauti na mechi za kila timu dhidi ya timu nyingine.
    Kwenye ukurasa utabiri una kisanduku chake, kwa hiyo hapo hauirudii (with_prediction=False).
    """
    x, out = m.extra or {}, []
    t = x.get("h2h_tally")
    if t and sum(t.values()):
        n = sum(t.values())
        draws = f"sare {t['D']}" if t["D"] else "hakuna sare"
        out.append({"label": "Walipokutana wao kwa wao",
                    "text": f"Katika mechi {n} za mwisho kati ya {m.home} na {m.away}: {m.home} wameshinda {t['W']}, "
                            f"{m.away} wameshinda {t['L']}, {draws}."})
    for side in ("home", "away"):
        letters = x.get(f"form_{side}") or []
        if letters:
            team = getattr(m, side)
            w, d, l = _count(letters)
            out.append({"label": f"Mechi {len(letters)} za mwisho za {team}",
                        "text": f"Dhidi ya timu mbalimbali: ushindi {w}, sare {d}, kipigo {l}."})
    guess = predict.sentence(m) if with_prediction else ""
    if guess:
        out.append({"label": "Utabiri", "text": guess})
    poss = next((r for r in x.get("stats") or [] if r["name"] == "Umiliki wa mpira"), None)
    if poss and m.status != "NS":
        lead = m.home if poss["pct"] >= 50 else m.away
        out.append({"label": "Umiliki wa mpira",
                    "text": f"{lead} wameutawala mpira kwa muda mwingi zaidi: {m.home} {poss['home']} – "
                            f"{poss['away']} {m.away}."})
    xg = next((r for r in x.get("stats") or [] if r["name"].startswith("Nafasi za kufunga")), None)
    if xg and m.status != "NS":
        out.append({"label": "Nafasi za kufunga zilizotengenezwa (xG)",
                    "text": f"{m.home} {xg['home']} – {xg['away']} {m.away}."})
    return out


def insights(m, with_prediction=True):
    """Uchambuzi kama sentensi kamili (kichwa: maelezo) — kwa makala na majaribio."""
    return [i["text"] if i["text"].startswith(i["label"]) else f"{i['label']}: {i['text']}"
            for i in insight_items(m, with_prediction)]


def _body_block(m):
    """Uchambuzi kwa makala (SEO: maudhui halisi ya kipekee kwa kila mechi), kipengele kimoja kwa mstari."""
    lines = insights(m)
    x = m.extra or {}
    parts = []
    if lines:
        parts.append("## Uchambuzi kabla ya mechi\n\n" + "\n".join(f"- {line}" for line in lines))
    if x.get("h2h"):
        rows = [f"- {g['date']}: {g['home']} {g['score']} {g['away']}" for g in x["h2h"][:5] if g.get("score")]
        if rows:
            parts.append("## Matokeo ya mechi zilizopita kati yao\n\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _refresh_body(m):
    """Weka uchambuzi (na kichwa cha sasa) kwenye makala ya mechi kabla haijaanza."""
    from news.football import _preview_body, preview_title
    block = _body_block(m)
    if not block or m.status != "NS":
        return
    article = m.article
    article.body = _preview_body(m) + "\n\n" + block
    article.title = preview_title(m)
    article.save(update_fields=["body", "title", "updated_at"])


# ── Kazi ya tick ──────────────────────────────────────────────────────────────

def run(state, now):
    """Taarifa za ziada kwa mechi za leo, kwa mpangilio wa umuhimu. Inarudisha idadi ya maombi."""
    spent = 0
    matches = list(LiveMatch.objects.select_related("article").filter(
        provider="highlightly", fixture_id__isnull=False, article__status="published",
        kickoff__gte=now - timedelta(hours=6), kickoff__lte=now + timedelta(hours=24)).order_by("kickoff"))
    for m in matches:
        x = m.extra or {}
        if m.status == "NS" and x.get("h2h_done"):
            before = x.get("prediction")
            after = predict.ensure(m)              # fomu mpya / hesabu iliyoboreshwa: utabiri unasasishwa
            if after and after != before:
                _refresh_body(m)
        if spent >= PER_TICK or _left(state) <= 5:
            break
        x = m.extra or {}
        to_kickoff = (m.kickoff - now) if m.kickoff else timedelta(hours=99)
        # 1. Vikosi: saa moja kabla hadi dakika 30 baada ya kuanza (kila dakika 15 hadi vipatikane)
        if (not x.get("lineups_done") and not _off("lineups") and timedelta(minutes=-30) <= to_kickoff <= timedelta(hours=1)
                and (not x.get("lineups_try") or now - _dt(x["lineups_try"]) > timedelta(minutes=15))
                and _left(state) > 10):
            lineups(state, m)
            spent += 1
            continue
        # 2. Video baada ya mechi (mara 3 juu kabisa, kila saa)
        if (m.is_finished and not x.get("highlights_done") and not _off("highlights") and _left(state) > 8
                and (not x.get("highlights_at") or now - _dt(x["highlights_at"]) > timedelta(hours=1))):
            highlights(state, m)
            spent += 1
            continue
        # 3. Kabla ya mechi: uso kwa uso + fomu ya timu zote mbili (maombi 3), kama yanatosha
        if (m.status == "NS" and not x.get("h2h_done") and x.get("home_id") and x.get("away_id")
                and _left(state) > LIVE_RESERVE + 3):
            if not _off("h2h"):
                head_to_head(state, m)
                spent += 1
            for side in ("home", "away"):
                if not _off("form"):
                    form(state, m, side)
                    spent += 1
            _save(m, {"h2h_done": True})
            predict.ensure(m)
            _refresh_body(m)
    return spent


def _dt(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return timezone.now() - timedelta(days=1)

"""
Nembo za klabu pale data ya mechi haina nembo (Highlightly mara nyingi haitumi nembo za ligi ndogo).

    logo_for(jina, nembo_ya_API, nchi) -> URL au ""

Mpangilio:
  1. Nembo iliyokuja na mechi yenyewe (API)
  2. Nembo iliyowekwa na mhariri kwenye Admin -> Teams (Team.logo)
  3. Nembo iliyowahi kuonekana kwenye mechi nyingine ya klabu hiyo hiyo (LiveMatch/Fixture)

Nchi inalinganishwa: "Simba" ya DR Congo haipati nembo ya Simba SC ya Tanzania. Mechi za kimataifa
(CAF, kirafiki — nchi "World"/"Africa" au haijulikani) zinapokea nembo ya Admin ya jina hilo.
Ramani nzima iko kwenye cache (dakika 30) na inafutwa Team ikihifadhiwa — hakuna query kwa kila kadi.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

CACHE_KEY = "crests:v1"
CACHE_SECONDS = 30 * 60
SEEN_DAYS = 365
INTERNATIONAL = {"", "world", "africa", "europe", "international"}


def _country(name):
    from news.football import _norm_country
    return _norm_country(str(name or ""))


def _key(name):
    from news.highlightly import _team_key
    return _team_key(str(name or ""))


UNKNOWN = "?"
TZ_MARKS = ("ligi kuu", "nbc", "tanzania", "championship tz", "fa cup tz", "azam")
INTL_MARKS = ("caf", "confederation", "cecafa", "afcon", "africa", "world cup", "friendl", "mapinduzi", "kagame")


def country_of(obj):
    """
    Nchi ya mechi: Fixture.country, au extra['country'] ya LiveMatch (inahifadhiwa tangu usawazishaji huu).
    Mechi za zamani bila nchi: inakisiwa kutoka jina la mashindano (Ligi Kuu/NBC -> Tanzania, CAF -> kimataifa);
    isipojulikana kabisa ni UNKNOWN — hakuna nembo ya kukisia (Simba ya DR Congo isipate ya Simba SC).
    """
    country = getattr(obj, "country", "") or (getattr(obj, "extra", None) or {}).get("country", "")
    if country:
        return country
    comp = str(getattr(obj, "competition", "") or "").lower()
    if any(m in comp for m in TZ_MARKS):
        return "Tanzania"
    if any(m in comp for m in INTL_MARKS):
        return "World"
    return UNKNOWN


def _build():
    from news.models import Fixture, LiveMatch, Team
    admin = {}
    for t in Team.objects.exclude(logo=""):
        for k in t.keys | {_key(t.name)}:
            if k:
                admin[k] = (t.logo, _country(t.country))
    seen = {}
    since = timezone.now() - timedelta(days=SEEN_DAYS)
    rows = list(Fixture.objects.filter(kickoff__gte=since).values_list("home", "home_logo", "away", "away_logo", "country"))
    rows += [(h, hl, a, al, (x or {}).get("country", "")) for h, hl, a, al, x in
             LiveMatch.objects.filter(kickoff__gte=since).values_list("home", "home_logo", "away", "away_logo", "extra")]
    for home, home_logo, away, away_logo, country in rows:
        c = _country(country)
        for name, logo in ((home, home_logo), (away, away_logo)):
            if logo:
                seen[(_key(name), c)] = logo
    return {"admin": admin, "seen": seen}


def _maps():
    data = cache.get(CACHE_KEY)
    if data is None:
        data = _build()
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def forget(*_args, **_kwargs):
    """Admin akibadilisha nembo ya timu, kadi zionyeshe mpya mara moja."""
    cache.delete(CACHE_KEY)


def logo_for(name, logo="", country=""):
    if logo:
        return logo
    k = _key(name)
    if not k or country == UNKNOWN:
        return ""
    maps = _maps()
    c = _country(country)
    hit = maps["admin"].get(k)
    if hit and (c in INTERNATIONAL or not hit[1] or hit[1] == c):
        return hit[0]
    return maps["seen"].get((k, c), "")

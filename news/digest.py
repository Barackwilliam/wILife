"""
Matokeo na mechi za leo kwa WhatsApp — kwa wasomaji waliojiunga wenyewe tu.

    JIUNGE         msomaji anajiunga (anatuma neno hili kwa namba ya WhatsApp ya wILife)
    ACHA / STOP    anasitisha (inajibiwa tu kama ni mwanachama — "acha" ni neno la kawaida)

Ujumbe mmoja kwa siku kwa chaguo-msingi (FOOTBALL_DIGEST_HOURS, saa za Afrika Mashariki):
matokeo ya jana + mechi za leo + link ya tovuti. Unatumwa kidogo kidogo kila tick
(FOOTBALL_DIGEST_PER_TICK) ili namba isionekane kama spam kwa WhatsApp.
"""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from news.models import Subscriber

log = logging.getLogger("news.digest")

JOIN = {"JIUNGE", "JIUNGE MPIRA", "JIUNGE WILIFE", "SUBSCRIBE"}
LEAVE = {"ACHA", "STOP", "ACHA MPIRA", "SITISHA", "UNSUBSCRIBE"}
WINDOW_HOURS = 3           # ujumbe wa saa 2 asubuhi hautumwi baada ya saa 5 (mf. seva ilikuwa imelala)
MAX_FAILURES = 5


def _word(text):
    return re.sub(r"[^A-Z ]", "", (text or "").upper()).strip()


def handle(phone, text):
    """Jibu la JIUNGE/ACHA, au None kama ujumbe si wa hapa."""
    from core.agent.whatsapp import normalise_number
    word, number = _word(text), normalise_number(phone)
    if not number or (word not in JOIN and word not in LEAVE):
        return None
    sub = Subscriber.objects.filter(phone=number).first()
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    if word in JOIN:
        if sub and sub.active:
            return "✅ Tayari umejiunga na wILife Michezo. Utapata mechi za leo na matokeo kila siku.\n\nJibu ACHA kusitisha."
        sub = sub or Subscriber(phone=number)
        sub.active, sub.left_at, sub.failures = True, None, 0
        sub.save()
        return ("⚽ *Karibu wILife Michezo!*\n\nKila siku utapata mechi za leo, matokeo ya jana na makadirio "
                f"ya mechi kubwa.\n\n{site}/mechi-za-leo/\n\nJibu ACHA wakati wowote kusitisha.")
    if not sub or not sub.active:
        return None
    sub.active, sub.left_at = False, timezone.now()
    sub.save(update_fields=["active", "left_at"])
    return "Umesitisha. Hutapokea tena ujumbe wa michezo wa wILife. Tuma JIUNGE kurudi wakati wowote."


def hours():
    out = []
    for part in str(getattr(settings, "FOOTBALL_DIGEST_HOURS", "8")).split(","):
        if part.strip().isdigit() and 0 <= int(part) <= 23:
            out.append(int(part))
    return sorted(set(out))


def _line(r):
    if r.has_score and (r.is_finished or r.is_playing):
        return f"{r.home} {r.home_goals}-{r.away_goals} {r.away}" + ("" if r.is_finished else f" ({r.clock})")
    return f"{timezone.localtime(r.kickoff):%H:%M} {r.home} vs {r.away}" if r.kickoff else f"{r.home} vs {r.away}"


def message(now=None):
    """Ujumbe wa siku: matokeo ya jana, mechi za leo, makadirio. '' kama hakuna cha kusema."""
    from news import fixtures, predict
    from news.templatetags.news_tags import sw_date
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    done = [r for r in fixtures.day_rows(today - timedelta(days=1)) if r.is_finished and r.has_score][:8]
    soon = [r for r in fixtures.day_rows(today) if not r.is_finished and not r.is_off][:10]
    if not done and not soon:
        return ""
    parts = [f"⚽ *wILife Michezo* — {sw_date(today)}"]
    if done:
        parts.append("*Matokeo ya jana*\n" + "\n".join(_line(r) for r in done))
    if soon:
        parts.append("*Mechi za leo* (saa za Afrika Mashariki)\n" + "\n".join(_line(r) for r in soon))
    guesses = [m for m in predict.upcoming(now, hours=24)][:3]
    if guesses:
        parts.append("*Makadirio* (si uhakika)\n" + "\n".join(
            f"{m.home} vs {m.away}: {m.extra['prediction']['home']}% · {m.extra['prediction']['draw']}% · "
            f"{m.extra['prediction']['away']}%" for m in guesses) + f"\n{site}/makadirio/")
    parts.append(f"Zote, LIVE na matokeo: {site}/mechi-za-leo/")
    parts.append("_Jibu ACHA kusitisha._")
    return "\n\n".join(parts)


def _slot(now):
    """Kipindi cha kutuma kilicho wazi sasa, mf. '2026-10-25-08', au ''."""
    local = timezone.localtime(now)
    for h in reversed(hours()):
        if h <= local.hour < h + WINDOW_HOURS:
            return f"{local:%Y-%m-%d}-{h:02d}"
    return ""


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick: tuma ujumbe wa siku kwa wanachama wachache kwa kila tick."""
    import time
    from core.agent import whatsapp
    out = {"job": "football_digest", "sent": 0, "failed": 0, "detail": ""}
    now = now or timezone.now()
    slot = _slot(now)
    if not slot or not getattr(settings, "FOOTBALL_DIGEST_ENABLED", True):
        return out
    due = list(Subscriber.objects.filter(active=True).exclude(last_slot__gte=slot)
               .order_by("id")[:getattr(settings, "FOOTBALL_DIGEST_PER_TICK", 6)])
    if not due:
        return out
    if not getattr(settings, "AGENT_ENABLED", False) or not whatsapp.configured():
        out["detail"] = "WhatsApp haijawashwa — ujumbe wa michezo haukutumwa"
        return out
    text = cache.get(f"digest:{slot}")
    if text is None:
        text = message(now)
        cache.set(f"digest:{slot}", text, WINDOW_HOURS * 3600)
    if not text:
        out["detail"] = "hakuna mechi za kutuma leo"
        return out
    if dry_run:
        out["detail"] = f"dry run: {len(due)}"
        return out
    for sub in due:
        if deadline and time.monotonic() > deadline - 5:
            break
        try:
            whatsapp.send_whatsapp(sub.phone, text, retries=1)
        except Exception as exc:
            log.warning("digest to %s failed: %s", sub.phone, exc)
            out["failed"] += 1
            if "HTTP 4" not in str(exc) or "HTTP 429" in str(exc):
                break                         # bridge haipo/imelala: jaribu tena tick ijayo, bila kumwadhibu msomaji
            sub.failures += 1                 # namba hii haipokei (mf. si ya WhatsApp)
            sub.last_slot = slot
            sub.active = sub.failures < MAX_FAILURES
            sub.save(update_fields=["last_slot", "failures", "active"])
            continue
        sub.last_slot, sub.sent, sub.failures = slot, sub.sent + 1, 0
        sub.save(update_fields=["last_slot", "sent", "failures"])
        out["sent"] += 1
        time.sleep(1)                         # polepole: WhatsApp inafungia namba zinazotuma kwa kasi
    out["detail"] = f"{slot}: {out['sent']} wametumiwa"
    return out

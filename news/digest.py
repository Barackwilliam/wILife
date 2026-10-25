"""
Matokeo na mechi za leo — ujumbe mmoja kwa siku kwa wasomaji waliojiunga wenyewe tu, kwa njia tatu:

    WhatsApp (namba)   msomaji anatuma JIUNGE; ACHA/STOP kusitisha (inajibiwa tu kama ni mwanachama —
                       "acha" ni neno la kawaida). Idadi ina kikomo (FOOTBALL_WHATSAPP_MAX) ili namba ya
                       bridge isifungiwe; kikijaa, JIUNGE inamwelekeza msomaji kwenye email / channel.
    WhatsApp Channel   posti moja kwa siku kwenye channel (WHATSAPP_CHANNEL) — wafuasi hawana kikomo.
    Email              anajiunga kwenye tovuti (/michezo/jiunge/), anathibitisha kwa link, na kila email
                       ina link ya kujiondoa. Kikomo cha siku (FOOTBALL_EMAIL_DAILY_MAX) kwa Resend ya bure.

Saa za kutuma: FOOTBALL_DIGEST_HOURS (Afrika Mashariki). Unatumwa kidogo kidogo kila tick
(FOOTBALL_DIGEST_PER_TICK) ili namba isionekane kama spam kwa WhatsApp.
"""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from news.models import DailyCount, EmailSubscriber, Subscriber

log = logging.getLogger("news.digest")

JOIN = {"JIUNGE", "JIUNGE MPIRA", "JIUNGE WILIFE", "SUBSCRIBE"}
LEAVE = {"ACHA", "STOP", "ACHA MPIRA", "SITISHA", "UNSUBSCRIBE"}
WINDOW_HOURS = 3           # ujumbe wa saa 2 asubuhi hautumwi baada ya saa 5 (mf. seva ilikuwa imelala)
MAX_FAILURES = 5
CHANNEL_TRIES = 5          # majaribio ya posti ya channel kwa kipindi kimoja
CONFIRM_EVERY = timedelta(hours=1)    # email ya kuthibitisha haitumwi upya mara nyingi (anwani ya mtu mwingine)


def _site():
    return getattr(settings, "SITE_URL", "").rstrip("/")


def _word(text):
    return re.sub(r"[^A-Z ]", "", (text or "").upper()).strip()


def channel_link():
    """Link ya WhatsApp Channel ya kuonyesha wasomaji (si JID ya ndani)."""
    value = getattr(settings, "WHATSAPP_CHANNEL", "").strip()
    return value if value.startswith("https://") else ""


def channel_name():
    """Jina la WhatsApp Channel (WHATSAPP_CHANNEL_NAME), mf. Soka360."""
    return (getattr(settings, "WHATSAPP_CHANNEL_NAME", "") or "Soka360").strip()


def whatsapp_full():
    return Subscriber.objects.filter(active=True).count() >= getattr(settings, "FOOTBALL_WHATSAPP_MAX", 300)


def handle(phone, text):
    """Jibu la JIUNGE/ACHA, au None kama ujumbe si wa hapa."""
    from core.agent.whatsapp import normalise_number
    word, number = _word(text), normalise_number(phone)
    if not number or (word not in JOIN and word not in LEAVE):
        return None
    sub = Subscriber.objects.filter(phone=number).first()
    site = _site()
    if word in JOIN:
        if sub and sub.active:
            return "✅ Tayari umejiunga na wILife Michezo. Utapata mechi za leo na matokeo kila siku.\n\nJibu ACHA kusitisha."
        if whatsapp_full():
            other = [f"📧 Email (bure): {site}/michezo/jiunge/"]
            if channel_link():
                other.insert(0, f"📢 Fuata *{channel_name()}* (WhatsApp Channel): {channel_link()}")
            return ("⚽ *wILife Michezo*\n\nNafasi za kupokea ujumbe WhatsApp moja kwa moja zimejaa kwa sasa. "
                    "Unaweza kupata ujumbe uleule wa kila siku hapa:\n\n" + "\n".join(other) +
                    f"\n\nMechi za leo muda wote: {site}/mechi-za-leo/")
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


# ── Email: kujiunga, kuthibitisha, kujiondoa ──────────────────────────────────

def email_ready():
    from core.agent.email_channel import newsletter_ready
    return newsletter_ready()


def subscribe_email(address):
    """
    Msomaji ameandika email yake kwenye tovuti. Inarudisha (hali, mwanachama):
    'sent' (link ya kuthibitisha imetumwa), 'already', 'wait' (link ilitumwa hivi karibuni), 'failed'.
    """
    from core.agent.email_channel import send_email
    address = address.strip().lower()
    sub = EmailSubscriber.objects.filter(email=address).first()
    if sub and sub.active and sub.confirmed:
        return "already", sub
    sub = sub or EmailSubscriber(email=address)
    if sub.confirm_sent_at and timezone.now() - sub.confirm_sent_at < CONFIRM_EVERY and sub.active:
        return "wait", sub
    sub.active, sub.confirmed, sub.left_at = True, False, None
    sub.confirm_sent_at = timezone.now()
    sub.save()
    link = f"{_site()}/michezo/thibitisha/{sub.token}/"
    plain = ("⚽ *Thibitisha: wILife Michezo*\n\nBofya link hii kuanza kupokea mechi za leo, matokeo ya jana "
             f"na makadirio kila siku:\n\n{link}\n\nKama hukuomba hivi, puuza email hii — hutatumiwa kitu.")
    from django.template.loader import render_to_string
    page = render_to_string("news/email/confirm.html", {"link": link, "site": _site(), "logo": _logo_url(),
                                                        "email": address})
    try:
        send_email(address, plain, subject="Thibitisha kujiunga na wILife Michezo", newsletter=True, html=page)
        DailyCount.bump("email")
    except Exception as exc:
        log.warning("confirmation email to %s failed: %s", address, exc)
        return "failed", sub
    return "sent", sub


def confirm_email(token):
    sub = EmailSubscriber.objects.filter(token=token).first()
    if sub and not (sub.confirmed and sub.active):
        sub.confirmed, sub.active, sub.left_at = True, True, None
        sub.confirmed_at = timezone.now()
        sub.save(update_fields=["confirmed", "active", "left_at", "confirmed_at"])
    return sub


def unsubscribe_email(token):
    sub = EmailSubscriber.objects.filter(token=token).first()
    if sub and sub.active:
        sub.active, sub.left_at = False, timezone.now()
        sub.save(update_fields=["active", "left_at"])
    return sub


# ── Ujumbe wa siku ────────────────────────────────────────────────────────────

def hours():
    out = []
    for part in str(getattr(settings, "FOOTBALL_DIGEST_HOURS", "8")).split(","):
        if part.strip().isdigit() and 0 <= int(part) <= 23:
            out.append(int(part))
    return sorted(set(out))


RULE = "━━━━━━━━━━━━━━━"
MAX_DONE, MAX_SOON, MAX_PICKS = 10, 12, 3


def _clock_emoji(dt):
    """🕗 n.k. — saa ya karibu zaidi (inasaidia kusoma ratiba kwa haraka kwenye simu)."""
    faces = "🕛🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚"
    return faces[timezone.localtime(dt).hour % 12] if dt else "🕒"


def _by_league(rows, limit):
    """[(ligi, nembo, [mistari])] — ligi kwa umuhimu, kikomo cha mistari kwa ujumla."""
    from news import fixtures
    out, n = [], 0
    for name, logo, items in fixtures.groups(rows):
        take = items[:max(0, limit - n)]
        if take:
            out.append((name, logo, take))
            n += len(take)
    return out


def data(now=None):
    """Taarifa za ujumbe wa siku (sawa kwa WhatsApp, Channel na email), au None kama hakuna cha kusema."""
    from news import fixtures, predict
    from news.pipeline import swahili_date
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    done = [r for r in fixtures.day_rows(today - timedelta(days=1)) if r.is_finished and r.has_score]
    soon = [r for r in fixtures.day_rows(today) if not r.is_finished and not r.is_off]
    if not done and not soon:
        return None
    picks = []
    for m in predict.upcoming(now, hours=24)[:MAX_PICKS]:
        p = m.extra["prediction"]
        picks.append({"m": m, "p": p, "pick_text": predict.pick_text(m, p), "url": m.article.get_absolute_url()})
    site = _site()
    return {
        "date_text": swahili_date(today), "site": site,
        "done": _by_league(done, MAX_DONE), "done_count": len(done),
        "soon": _by_league(soon, MAX_SOON), "soon_count": len(soon),
        "live_count": sum(r.is_playing for r in soon), "picks": picks,
        "links": {"today": f"{site}/mechi-za-leo/", "yesterday": f"{site}/matokeo-ya-jana/",
                  "picks": f"{site}/makadirio/", "join": f"{site}/michezo/jiunge/"},
        "channel": channel_link(),
    }


def _row_text(r):
    if r.has_score and (r.is_finished or r.is_playing):
        live = "" if r.is_finished else f"  🔴 {r.clock}"
        return f"{r.home}  *{r.home_goals}–{r.away_goals}*  {r.away}{live}"
    when = timezone.localtime(r.kickoff).strftime("%H:%M") if r.kickoff else "--:--"
    return f"{_clock_emoji(r.kickoff)} `{when}`  {r.home} vs {r.away}"


def text(d, kind="whatsapp"):
    """
    Ujumbe wa WhatsApp / Channel: kichwa, mstari, makundi ya ligi, makadirio, link.
    Alama za WhatsApp: *nzito*, _italiki_, `msimbo`, > nukuu.
    """
    if not d:
        return ""
    if kind == "channel":
        parts = [f"⚽ *{channel_name().upper()}*\n_Habari za Soka Kila Upande · {d['date_text']}_", RULE]
    else:
        parts = [f"⚽ *wILife MICHEZO*\n_{d['date_text']}_", RULE]
    if d["done"]:
        block = [f"✅ *MATOKEO YA JANA*"]
        for name, _, items in d["done"]:
            block.append(f"\n🏆 *{name}*\n" + "\n".join(_row_text(r) for r in items))
        if d["done_count"] > MAX_DONE:
            block.append(f"\n_+ mechi {d['done_count'] - MAX_DONE} zaidi:_ {d['links']['yesterday']}")
        parts += ["\n".join(block), RULE]
    if d["soon"]:
        head = "📅 *MECHI ZA LEO*  _(saa za Afrika Mashariki)_"
        if d["live_count"]:
            head += f"\n🔴 _{d['live_count']} zinaendelea sasa_"
        block = [head]
        for name, _, items in d["soon"]:
            block.append(f"\n🏆 *{name}*\n" + "\n".join(_row_text(r) for r in items))
        if d["soon_count"] > MAX_SOON:
            block.append(f"\n_+ mechi {d['soon_count'] - MAX_SOON} zaidi kwenye tovuti_")
        parts += ["\n".join(block), RULE]
    if d["picks"]:
        block = ["🔮 *MAKADIRIO YA LEO*"]
        for x in d["picks"]:
            m, p = x["m"], x["p"]
            block.append(f"\n*{m.home} vs {m.away}*\n"
                         f"1️⃣ {p['home']}%   ✖️ {p['draw']}%   2️⃣ {p['away']}%\n"
                         f"> Inayowezekana zaidi: {p['score']} · Magoli 3+: {p['over25']}%")
        block.append(f"\n_Si uhakika · 18+ · Beti kwa uwajibikaji_\n{d['links']['picks']}")
        parts += ["\n".join(block), RULE]
    parts.append(f"🔴 *LIVE na matokeo yote:*\n{d['links']['today']}")
    if kind == "whatsapp":
        parts.append("_Jibu ACHA kusitisha ujumbe huu._")
    elif kind == "channel":
        parts.append(f"📲 _Shiriki {channel_name()} na rafiki anayependa mpira._\n_— kutoka wILife_")
    return "\n\n".join(parts)


def email_html(d, leave_url=""):
    """Email ya michezo (news/templates/news/email/digest.html) — nembo za timu, ubao wa matokeo, makadirio."""
    from django.template.loader import render_to_string
    return render_to_string("news/email/digest.html", {**d, "leave_url": leave_url, "logo": _logo_url(),
                                                       "channel_name": channel_name(),
                                                       "channel_logo": _logo_url("news/brand/soka360-96.png")})


def _logo_url(name="news/brand/logo-600.png"):
    from django.templatetags.static import static
    path = static(name)
    return path if path.startswith("http") else _site() + path


def body(now=None):
    """Ujumbe wa siku kwa maandishi (WhatsApp). '' kama hakuna cha kusema."""
    return text(data(now), "channel")


def message(now=None):
    """Ujumbe wa WhatsApp kwa mwanachama (una maelekezo ya kusitisha)."""
    return text(data(now), "whatsapp")


def _slot(now):
    """Kipindi cha kutuma kilicho wazi sasa, mf. '2026-10-25-08', au ''."""
    local = timezone.localtime(now)
    for h in reversed(hours()):
        if h <= local.hour < h + WINDOW_HOURS:
            return f"{local:%Y-%m-%d}-{h:02d}"
    return ""


def _post_channel(slot, text, out):
    """Posti moja kwenye WhatsApp Channel kwa kipindi hiki."""
    from core.agent import whatsapp
    channel = getattr(settings, "WHATSAPP_CHANNEL", "").strip()
    if not channel or DailyCount.get(f"channel:{slot}") or DailyCount.get(f"channel-fail:{slot}") >= CHANNEL_TRIES:
        return
    try:
        whatsapp.send_channel(channel, text)
    except Exception as exc:
        log.warning("whatsapp channel post failed: %s", exc)
        DailyCount.bump(f"channel-fail:{slot}")
        out["failed"] += 1
        out["notes"].append(f"channel: {str(exc)[:120]}")
        return
    DailyCount.bump(f"channel:{slot}")
    out["sent"] += 1
    out["notes"].append("channel: imechapishwa")


def _send_whatsapp(slot, text, out, deadline):
    import time
    from core.agent import whatsapp
    due = list(Subscriber.objects.filter(active=True).exclude(last_slot__gte=slot)
               .order_by("id")[:getattr(settings, "FOOTBALL_DIGEST_PER_TICK", 6)])
    n = 0
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
        n += 1
        time.sleep(1)                         # polepole: WhatsApp inafungia namba zinazotuma kwa kasi
    out["sent"] += n
    if n:
        out["notes"].append(f"whatsapp: {n}")


def _send_emails(slot, d, out, deadline):
    import time
    from core.agent.email_channel import send_email
    cap = getattr(settings, "FOOTBALL_EMAIL_DAILY_MAX", 90)
    room = min(cap - DailyCount.get("email"), getattr(settings, "FOOTBALL_DIGEST_PER_TICK", 6) * 2)
    if room <= 0:
        return
    due = list(EmailSubscriber.objects.filter(active=True, confirmed=True).exclude(last_slot__gte=slot)
               .order_by("id")[:room])
    n = 0
    for sub in due:
        if deadline and time.monotonic() > deadline - 3:
            break
        leave = f"{_site()}/michezo/acha/{sub.token}/"
        try:
            send_email(sub.email, f"{text(d, 'email')}\n\nKujiondoa: {leave}", newsletter=True,
                       subject=_subject(d), html=email_html(d, leave),
                       headers={"List-Unsubscribe": f"<{leave}>",
                                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        except Exception as exc:
            log.warning("digest email to %s failed: %s", sub.email, exc)
            out["failed"] += 1
            if "HTTP 4" not in str(exc) or "HTTP 429" in str(exc):
                break                         # Resend haipatikani / kikomo: tick ijayo
            sub.failures += 1                 # anwani imekataliwa
            sub.last_slot = slot
            sub.active = sub.failures < MAX_FAILURES
            sub.save(update_fields=["last_slot", "failures", "active"])
            continue
        DailyCount.bump("email")
        sub.last_slot, sub.sent, sub.failures = slot, sub.sent + 1, 0
        sub.save(update_fields=["last_slot", "sent", "failures"])
        n += 1
    out["sent"] += n
    if n:
        out["notes"].append(f"email: {n}")


def _subject(d):
    first = next((r for _, _, items in d["soon"] for r in items), None)
    lead = f": {first.home} vs {first.away}" if first else ""
    return f"⚽ Mechi za leo{lead} — wILife Michezo"[:120]


def run(now=None, deadline=None, dry_run=False):
    """Kazi ya tick: channel (posti 1), kisha wanachama wachache wa WhatsApp na email kwa kila tick."""
    from core.agent import whatsapp
    out = {"job": "football_digest", "sent": 0, "failed": 0, "detail": "", "notes": []}
    now = now or timezone.now()
    slot = _slot(now)
    if not slot or not getattr(settings, "FOOTBALL_DIGEST_ENABLED", True):
        out.pop("notes")
        return out
    wa_on = getattr(settings, "AGENT_ENABLED", False) and whatsapp.configured()
    email_on = getattr(settings, "AGENT_ENABLED", False) and email_ready()
    work = {
        "channel": wa_on and bool(getattr(settings, "WHATSAPP_CHANNEL", "").strip())
        and not DailyCount.get(f"channel:{slot}") and DailyCount.get(f"channel-fail:{slot}") < CHANNEL_TRIES,
        "whatsapp": wa_on and Subscriber.objects.filter(active=True).exclude(last_slot__gte=slot).exists(),
        "email": email_on and EmailSubscriber.objects.filter(active=True, confirmed=True)
        .exclude(last_slot__gte=slot).exists(),
    }
    if not any(work.values()):
        out.pop("notes")
        return out
    d = cache.get(f"digest:{slot}")
    if d is None:
        d = data(now) or {}
        cache.set(f"digest:{slot}", d, WINDOW_HOURS * 3600)
    if not d:
        out.pop("notes")
        out["detail"] = "hakuna mechi za kutuma leo"
        return out
    if dry_run:
        out.pop("notes")
        out["detail"] = f"dry run: {', '.join(k for k, v in work.items() if v)}"
        return out
    if work["channel"]:
        _post_channel(slot, text(d, "channel"), out)
    if work["whatsapp"]:
        _send_whatsapp(slot, text(d, "whatsapp"), out, deadline)
    if work["email"]:
        _send_emails(slot, d, out, deadline)
    out["detail"] = f"{slot}: " + ("; ".join(out.pop("notes")) or "hakuna aliyetumiwa")
    return out

"""
Amri za LIVE kutoka WhatsApp / Telegram — mhariri anaripoti mechi bila kufungua tovuti.

    live anza Simba vs Yanga            -> makala mpya ya LIVE (imechapishwa), link inarudi
    live 6' GOOOL! Chama anafunga       -> sasisho kwenye habari ya LIVE inayoendelea
    live 52' Yanga wasawazisha | Simba 1-1 Yanga   -> sasisho + matokeo mapya (baada ya "|")
    live matokeo Simba 2-1 Yanga        -> matokeo tu
    live mwisho                         -> LIVE inaisha (masasisho yanabaki)
    live                                -> hali na msaada

Inafanya kazi kwenye habari ya LIVE iliyoanzishwa karibuni zaidi. Ni mhariri aliyeidhinishwa
tu anayeweza kutuma (core/views_whatsapp._authorised_user, AGENT_TELEGRAM_CHAT_ID).
"""
import re

from django.conf import settings
from django.utils import timezone

from news import live
from news.models import Article

_CMD = re.compile(r"^\s*live\b[\s:,-]*(?P<rest>.*)$", re.I | re.S)
_LABEL = re.compile(r"^(?P<label>\d{1,3}(?:\+\d{1,2})?['’]|\d{1,2}:\d{2}|HT|FT)\s+(?P<text>.+)$", re.I | re.S)

HELP = ("📡 *Amri za LIVE*\n"
        "• live anza Simba vs Yanga\n"
        "• live 45' GOOOL! Chama anafunga\n"
        "• live 52' Yanga wasawazisha | Simba 1-1 Yanga\n"
        "• live matokeo Simba 2-1 Yanga\n"
        "• live mwisho\n"
        "• live futa  (futa sasisho la mwisho)\n"
        "• live futa yote  (ondoa LIVE nzima kwenye tovuti)")


def _kind(text):
    """Ikoni ya sasisho la mhariri kutokana na maneno yake (goli, kadi nyekundu, kadi…)."""
    t = text.lower()
    if re.search(r"\bgo{2,}l|\bgoli\b|\bgoal\b|⚽", t):
        return "goal"
    if "nyekundu" in t or "🟥" in t:
        return "red"
    if "kadi" in t or "🟨" in t:
        return "card"
    if "mabadiliko" in t:
        return "sub"
    return ""


def is_command(text):
    return bool(_CMD.match(text or ""))


def _url(article):
    return (getattr(settings, "SITE_URL", "") or "").rstrip("/") + article.get_absolute_url()


def _current():
    return (Article.objects.filter(status="published", live_started_at__isnull=False, live_ended_at__isnull=True)
            .order_by("-live_started_at").first())


def _start(title, user):
    from news import commission
    title = title.strip(" .:-")[:180]
    if len(title) < 4:
        return "Andika kichwa cha habari, mf.: live anza Simba vs Yanga"
    article = Article.objects.create(
        title=title, category=commission.guess_category(title),
        excerpt=f"Fuatilia {title} moja kwa moja — matukio yote dakika kwa dakika kwenye wILife."[:300],
        body=f"{title}: wILife inakuletea matukio yote moja kwa moja. Masasisho mapya yanaonekana juu.",
        keywords=", ".join(w for w in re.split(r"\s+(?:vs|v|dhidi ya|na)\s+", title, flags=re.I)[:4]),
        status="published", published_at=timezone.now(), origin="live")
    try:
        article.render_cover()
        article.save()
    except Exception:
        pass                                   # picha ya mfumo isizuie LIVE kuanza
    live.start(article)
    live.attach_match(article)
    return f"🔴 LIVE imeanza: {article.title}\n{_url(article)}\n\nTuma masasisho: live 1' Mechi imeanza!"


def from_chat(text, user=None):
    """Jibu la amri ya LIVE, au None kama ujumbe si amri ya LIVE."""
    m = _CMD.match(text or "")
    if not m:
        return None
    rest = m.group("rest").strip()
    word = rest.split(" ", 1)[0].lower() if rest else ""

    if word in ("anza", "start"):
        return _start(rest[len(word):], user)

    article = _current()
    if not rest or word in ("msaada", "help"):
        status = f"🔴 Inaendelea: {article.title}\n{_url(article)}\n\n" if article else "Hakuna LIVE inayoendelea.\n\n"
        return status + HELP
    if not article:
        return "Hakuna LIVE inayoendelea. Anza kwanza: live anza Simba vs Yanga"

    if word in ("futa", "delete"):
        if rest[len(word):].strip().lower() in ("yote", "zote", "all"):
            live.remove(article)
            return f"🗑 LIVE imeondolewa kwenye tovuti: {article.title}"
        last = article.live_updates.first()
        if not last:
            return "Hakuna sasisho la kufuta."
        live.delete_update(last)
        return f"🗑 Sasisho limefutwa: {(last.label + ' ') if last.label else ''}{last.text[:80]}"

    if word in ("mwisho", "maliza", "end", "stop"):
        live.end(article)
        score = f" — {article.live_score}" if article.live_score else ""
        return f"⏹ LIVE imeisha: {article.title}{score}\nMasasisho yote yanabaki kwenye makala."

    if word in ("matokeo", "score"):
        score = rest[len(word):].strip()
        if not score:
            return "Andika matokeo, mf.: live matokeo Simba 1-0 Yanga"
        live.add_score(article, score)
        return f"✅ Matokeo: {article.live_score}"

    body, _, score = rest.partition("|")
    body = body.strip()
    label = ""
    lm = _LABEL.match(body)
    if lm:
        label, body = lm.group("label"), lm.group("text").strip()
    if not body:
        return "Andika kilichotokea, mf.: live 45' GOOOL! Chama anafunga"
    live.add(article, body, label, score=score.strip() if score.strip() else None, kind=_kind(body))
    tail = f" ({article.live_score})" if article.live_score else ""
    return f"✅ Sasisho limewekwa{tail}."

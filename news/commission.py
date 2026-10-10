"""
Habari iliyoagizwa — "Andika habari kuhusu Simba na Yanga leo".

    1. Tafuta vyanzo vya mada hiyo SASA HIVI: Google News (Kiswahili, Tanzania,
       saa 48) + feeds za kundi, zikichujwa kwa maneno ya mada.
    2. Panga kwa uzito (vyombo vingi + mpya) na umpe AI vyanzo vya juu.
    3. AI ikikosa ukweli wa kutosha inakataa ("skip") — haibuni.
    4. Rasimu inasubiri "OK <code>" (au inachapishwa mara moja ukichagua hivyo).

Inaendeshwa kwenye thread ili ukurasa / webhook usisubiri AI.

Habari iliyopangwa ("Simba na Yanga saa 2 usiku"): AI inaanza saa iliyotajwa,
inatumia ripoti zilizotoka karibu na tukio tu (si makala za kabla ya mechi), na
vyombo visipokuwa vimeripoti bado inajaribu tena kila RETRY_MINUTES kwa saa
`watch_hours`.
"""

import logging
import re
import threading
from datetime import datetime, time as dtime, timedelta

from django.db import connection
from django.urls import reverse
from django.utils import timezone

from news import feeds, indexnow, writer
from news.models import Article, Category, EditorSettings, NewsRequest

log = logging.getLogger("news")

COMMAND = re.compile(
    r"^\s*(?:andika\s+habari|agiza\s+habari|habari\s*:)\s*(?:ya|za|kuhusu|juu\s+ya)?\s*[:\-]?\s*(?P<topic>.+)$",
    re.IGNORECASE | re.DOTALL)
PUBLISH_FLAG = re.compile(r"\s*(?:#|\()?\s*(chapisha(?:\s+moja\s+kwa\s+moja)?|publish)\s*\)?\s*$", re.IGNORECASE)
RETRY_MINUTES = 15
EARLIEST_SOURCE = timedelta(hours=2)   # ripoti za zamani kuliko (start_at - hii) ni za kabla ya tukio

# "saa 2 usiku", "saa 10:30 jioni", "kesho saa 3 asubuhi", "20:30", "kesho 18:00"
SW_TIME = re.compile(
    r"\s*(?P<kesho>kesho\s+)?(?:saa\s*(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*"
    r"(?P<p>asubuhi|alfajiri|mchana|jioni|usiku)|(?P<H>[01]?\d|2[0-3]):(?P<M>[0-5]\d))\b",
    re.IGNORECASE)
SPORTS_WORDS = ("simba", "yanga", "taifa stars", "azam", "mpira", "ligi", "mechi", "goli", "michezo",
                "twiga stars", "singida", "nbc premier")


def swahili_hour(h, period):
    """Saa za Kiswahili -> saa 24. 'saa 2 usiku' = 20, 'saa 1 asubuhi' = 7, 'saa 6 mchana' = 12."""
    base = (h + 6) % 12
    period = period.lower()
    if period in ("asubuhi", "alfajiri"):
        return base
    if period in ("mchana", "jioni"):
        return base + 12
    # usiku: saa 1–5 ni 19–23; saa 6 ni 0; saa 7–11 ni 1–5 alfajiri
    return base + 12 if 1 <= h <= 5 else base


def parse_time(text, now=None):
    """Toa muda wa tukio kwenye amri. Inarudisha (datetime au None, maandishi bila muda)."""
    m = SW_TIME.search(text or "")
    if not m:
        return None, text
    now = timezone.localtime(now or timezone.now())
    if m.group("h"):
        h = int(m.group("h"))
        if not 1 <= h <= 12:
            return None, text
        hour, minute = swahili_hour(h, m.group("p")), int(m.group("m") or 0)
    else:
        hour, minute = int(m.group("H")), int(m.group("M"))
    if minute > 59:
        return None, text
    day = now.date() + timedelta(days=1 if m.group("kesho") else 0)
    when = timezone.make_aware(datetime.combine(day, dtime(hour, minute)), now.tzinfo)
    if when < now - timedelta(minutes=30) and not m.group("kesho"):
        when += timedelta(days=1)   # "saa 2 usiku" ikiwa imeshapita leo = kesho
    cleaned = (text[:m.start()] + " " + text[m.end():]).strip()
    return when, re.sub(r"\s{2,}", " ", cleaned)


def parse_command(text):
    """'Andika habari kuhusu Simba vs Yanga leo #chapisha' -> ('Simba vs Yanga leo', True)."""
    m = COMMAND.match(text or "")
    if not m:
        return None, False
    topic = m.group("topic").strip()
    publish = bool(PUBLISH_FLAG.search(topic))
    topic = PUBLISH_FLAG.sub("", topic).strip(" .:-")
    return (topic[:300] or None), publish


def guess_category(topic):
    t = topic.lower()
    if any(w in t for w in ("dunia", "marekani", "ulaya", "china", "urusi", "uingereza")):
        return Category.DUNIA
    if any(w in t for w in ("kenya", "uganda", "rwanda", "afrika", "congo", "drc", "msumbiji", "zambia")):
        return Category.AFRIKA
    return Category.TANZANIA


def _search_query(topic):
    """'Simba na Yanga leo' -> 'Simba Yanga' (maneno ya kujaza yanaharibu utafutaji)."""
    filler = {"leo", "jana", "habari", "kuhusu", "ya", "za", "wa", "na", "la", "kwa", "mechi", "kati",
              "vs", "dhidi", "muda", "huu", "sasa", "hivi", "mpya", "today"}
    words = [w for w in re.findall(r"[\wÀ-ÿ']+", topic) if w.lower() not in filler]
    return " ".join(words[:8]) or topic


def find_sources(topic, category, focus=()):
    query = _search_query(topic)
    urls = [feeds.google_news_url(query, 2)]
    if query != topic:
        urls.append(feeds.google_news_url(topic, 2))
    items, errors = feeds.collect(category, max_age_hours=48, urls=urls)
    # Feeds za kundi: zile tu zinazogusa maneno ya mada
    words = [w.lower() for w in query.split() if len(w) >= 3]
    local, err2 = feeds.collect(category, max_age_hours=48)
    known = {i.url for i in items}
    for it in local:
        if it.url not in known and words and feeds.matches(it, words):
            items.append(it)
    ranked = feeds.rank(items, focus=list(focus) + words)
    # Habari inayogusa angalau neno moja la mada
    relevant = [i for i in ranked if not words or feeds.matches(i, words)]
    return relevant, errors + err2


def _notify(req, text):
    from core.agent.channels import DeliveryError, send_to_self
    from news.pipeline import _owner
    user = req.requested_by or _owner()
    if user is None:
        return
    try:
        send_to_self(user, text)
    except DeliveryError as exc:
        log.warning("commission notify failed: %s", exc)


def _site(path):
    from django.conf import settings
    return f"{getattr(settings, 'SITE_URL', '').rstrip('/')}{path}"


def run(req):
    """Andika habari iliyoagizwa. Inabadilisha `req` na kuirudisha."""
    from news.pipeline import _owner, _save_article

    req.status = "working"
    req.save(update_fields=["status"])
    cfg = EditorSettings.load()
    try:
        items, errors = find_sources(req.topic, req.category, focus=feeds._terms(cfg.focus_topics))
        if req.start_at:
            # Makala za kabla ya tukio (mf. "Simba vs Yanga leo: kikosi") si habari ya tukio
            cutoff = req.start_at - EARLIEST_SOURCE
            items = [i for i in items if not i.published or i.published >= cutoff]
        req.found = len(items)
        req.attempts += 1
        if not items:
            raise writer.WriterError(
                f"Sikupata habari za \"{req.topic}\" kwenye vyanzo (saa 48 zilizopita). "
                "Sitabuni — jaribu maneno mengine au subiri vyombo viripoti."
                + (f" [{'; '.join(errors)[:200]}]" if errors else ""))
        data = writer.write_commissioned(Category(req.category).label, req.topic, items,
                                         instructions=req.instructions, notes=cfg.editor_notes)
        main = items[0]
        taken = Article.objects.filter(source_url=main.url).exists()
        article = _save_article(None, 0, req.category, data,
                                [i.as_source() for i in items[:4]],
                                source_url=None if taken else main.url)
        req.article = article
        if req.publish_now:
            article.publish()
            indexnow.submit([article.get_absolute_url()])
            req.status = "published"
            msg = f"✅ Habari imechapishwa: {article.title}\n{_site(article.get_absolute_url())}"
        else:
            req.status = "drafted"
            req.approval = _approval(article, _owner() if req.requested_by is None else req.requested_by)
            code = f"\nJibu *OK {req.approval.code}* kuichapisha, au *NO {req.approval.code}* kuiacha." \
                if req.approval else ""
            msg = (f"📝 Rasimu ya habari uliyoagiza iko tayari:\n*{article.title}*\n"
                   f"{article.excerpt}\n\nSoma: {_site(article.get_absolute_url())}{code}")
    except Exception as exc:  # WriterError, network, anything — the request records it
        log.warning("commission %s failed: %s", req.pk, exc)
        req.error = str(exc)[:1000]
        if req.start_at and not req.article and timezone.now() < _watch_until(req):
            # Vyombo bado havijaripoti — jaribu tena baadaye, kimya
            req.status = "watching"
            req.next_try_at = timezone.now() + timedelta(minutes=RETRY_MINUTES)
            req.save()
            return req
        req.status = "failed"
        msg = f"⚠️ Habari \"{req.topic[:80]}\" haikuandikwa: {req.error[:300]}"
        if req.start_at:
            msg += f" (nimefuatilia kwa saa {req.watch_hours}, majaribio {req.attempts})"
    req.finished_at = timezone.now()
    req.next_try_at = None
    req.save()
    if req.via != "web" or req.start_at:
        _notify(req, msg)
    return req


def _watch_until(req):
    return req.start_at + timedelta(hours=req.watch_hours)


def run_due(now=None, dry_run=False):
    """Tick: anzisha habari zilizopangwa ambazo muda wake umefika."""
    now = now or timezone.now()
    due = list(NewsRequest.objects.filter(status__in=("scheduled", "watching"),
                                          next_try_at__lte=now).order_by("next_try_at")[:5])
    started = 0
    for req in due:
        if dry_run:
            continue
        # Dai ombi kwanza ili tick mbili zisilianzishe mara mbili
        if NewsRequest.objects.filter(pk=req.pk, status=req.status).update(status="queued", next_try_at=None):
            start(req)
            started += 1
    waiting = NewsRequest.objects.filter(status__in=("scheduled", "watching")).count()
    return {"job": "news_requests", "sent": started, "failed": 0,
            "detail": f"started {started}, waiting {waiting - started}" if (started or waiting) else "none"}


def schedule(req, when):
    req.start_at = when
    req.next_try_at = when
    req.status = "scheduled"
    return req


def _approval(article, user):
    if user is None:
        return None
    from django.conf import settings
    from core.agent.approvals import _new_code
    from core.models import ApprovalRequest
    return ApprovalRequest.objects.create(
        user=user, tool="publish_article", code=_new_code(), recipient_name="wILife",
        body=f"📰 {article.title}\n{article.excerpt}", context=f"article:{article.pk}",
        external_ref=str(article.pk), status="pending",
        expires_at=timezone.now() + timedelta(hours=getattr(settings, "AGENT_APPROVAL_TTL_HOURS", 24)))


def start(req):
    """Endesha kwenye thread (AI na feeds huchukua sekunde 10–40)."""
    def target():
        try:
            run(NewsRequest.objects.get(pk=req.pk))
        except Exception:
            log.exception("commission thread %s", req.pk)
            NewsRequest.objects.filter(pk=req.pk).update(status="failed", error="Hitilafu ya ndani",
                                                         finished_at=timezone.now())
        finally:
            connection.close()
    threading.Thread(target=target, name=f"habari-agizo-{req.pk}", daemon=True).start()


def from_chat(text, user, via):
    """Amri ya WhatsApp/Telegram. Inarudisha jibu la papo hapo, au None kama si amri."""
    topic, publish = parse_command(text)
    if not topic:
        return None
    when, topic = parse_time(topic)
    topic = topic.strip(" ,.-") or "habari"
    req = NewsRequest(topic=topic, category=guess_category(topic), publish_now=publish,
                      via=via, requested_by=user)
    tail = " Itachapishwa moja kwa moja." if publish else " Nitakutumia rasimu na code ya kuidhinisha."
    if when and when > timezone.now():
        schedule(req, when).save()
        local = timezone.localtime(when)
        return (f"⏰ Sawa. Nitaanza kuandaa habari ya \"{topic}\" saa {local:%H:%M} ({local:%d/%m}). "
                f"Vyombo visipokuwa vimeripoti bado, nitaendelea kuangalia kwa saa {req.watch_hours}.{tail}")
    req.save()
    start(req)
    return f"🔎 Ninatafuta na kuandika habari kuhusu \"{topic}\"…{tail}"

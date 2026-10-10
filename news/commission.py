"""
Habari iliyoagizwa — "Andika habari kuhusu Simba na Yanga leo".

    1. Tafuta vyanzo vya mada hiyo SASA HIVI: Google News (Kiswahili, Tanzania,
       saa 48) + feeds za kundi, zikichujwa kwa maneno ya mada.
    2. Panga kwa uzito (vyombo vingi + mpya) na umpe AI vyanzo vya juu.
    3. AI ikikosa ukweli wa kutosha inakataa ("skip") — haibuni.
    4. Rasimu inasubiri "OK <code>" (au inachapishwa mara moja ukichagua hivyo).

Inaendeshwa kwenye thread ili ukurasa / webhook usisubiri AI.
"""

import logging
import re
import threading
from datetime import timedelta

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
SPORTS_WORDS = ("simba", "yanga", "taifa stars", "azam", "mpira", "ligi", "mechi", "goli", "michezo",
                "twiga stars", "singida", "nbc premier")


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
        req.found = len(items)
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
        req.status = "failed"
        req.error = str(exc)[:1000]
        msg = f"⚠️ Habari \"{req.topic[:80]}\" haikuandikwa: {req.error[:300]}"
    req.finished_at = timezone.now()
    req.save()
    if req.via != "web":
        _notify(req, msg)
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
    req = NewsRequest.objects.create(topic=topic, category=guess_category(topic), publish_now=publish,
                                     via=via, requested_by=user)
    start(req)
    return (f"🔎 Ninatafuta na kuandika habari kuhusu \"{topic}\"…"
            + (" Itachapishwa moja kwa moja." if publish else " Nitakutumia rasimu na code ya kuidhinisha."))

"""
The daily news batch.

    1. At NEWS_DRAFT_HOUR the agent opens today's NewsBatch.
    2. Each tick fills empty slots until the batch is complete (one LLM call per
       article, bounded by the tick's deadline):
          slots 1      Dunia
          slots 2–3    Afrika
          slots 4–6    Tanzania
          slot  7      JamiiTek service   (Mon, Wed, Sat only)
    3. When complete, the owner gets one message listing the headlines with a
       code. "OK 1234" publishes the whole batch; drafts can also be edited,
       rejected or published one by one on the staff review page.

Nothing becomes public without that approval.
"""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import ApprovalRequest
from news import covers, feeds, writer
from news.models import Article, Category, JamiiTekService, NewsBatch

log = logging.getLogger("news")

PLAN = [
    (1, Category.DUNIA),
    (2, Category.AFRIKA), (3, Category.AFRIKA),
    (4, Category.TANZANIA), (5, Category.TANZANIA), (6, Category.TANZANIA),
]
JAMIITEK_SLOT = 7
JAMIITEK_WEEKDAYS = {0, 2, 5}  # Monday, Wednesday, Saturday
SWAHILI_DAYS = ["Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa", "Jumamosi", "Jumapili"]
SWAHILI_MONTHS = ["Januari", "Februari", "Machi", "Aprili", "Mei", "Juni", "Julai",
                  "Agosti", "Septemba", "Oktoba", "Novemba", "Desemba"]


def swahili_date(d):
    return f"{SWAHILI_DAYS[d.weekday()]}, {d.day} {SWAHILI_MONTHS[d.month - 1]} {d.year}"


def plan_for(day):
    slots = list(PLAN)
    if day.weekday() in JAMIITEK_WEEKDAYS:
        slots.append((JAMIITEK_SLOT, Category.JAMIITEK))
    return slots


def _owner():
    return User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first() or \
        User.objects.filter(is_active=True).order_by("pk").first()


def next_service():
    """Least recently featured active service."""
    return JamiiTekService.objects.filter(active=True).order_by(
        "last_featured_at", "order").first()


def _save_article(batch, slot, category, data, sources, source_url=None, service=None):
    article = Article(
        title=data["title"], excerpt=data["excerpt"], body=data["body"], keywords=data["keywords"],
        category=category, sources=sources, source_url=source_url, service=service,
        batch=batch, slot=slot, status="draft",
    )
    article.save()
    try:
        article.render_cover()
        article.save()
    except Exception as exc:  # a missing cover never blocks the article
        log.warning("cover failed for %s: %s", article.pk, exc)
    return article


def fill_slot(batch, slot, category):
    """Write one article for this slot. Returns the Article or raises."""
    if category == Category.JAMIITEK:
        service = next_service()
        if service is None:
            raise writer.WriterError("no active JamiiTek service to feature")
        data = writer.write_service(service)
        source = {"title": service.name, "url": service.get_absolute_url(), "publisher": "JamiiTek"}
        article = _save_article(batch, slot, category, data, [source], service=service)
        service.last_featured_at = timezone.now()
        service.save(update_fields=["last_featured_at"])
        return article

    used = set(Article.objects.exclude(source_url=None).values_list("source_url", flat=True))
    items, errors = feeds.collect(category, exclude_urls=used)
    if not items:
        raise writer.WriterError(f"no fresh {category} stories ({'; '.join(errors)[:300] or 'feeds empty'})")
    last_error = None
    for item in items[:3]:  # try the next story if the model fails on one
        try:
            related = [i for i in items if i is not item][:2]
            data = writer.write_news(Category(category).label, item, related)
            return _save_article(batch, slot, category, data, [item.as_source()], source_url=item.url)
        except writer.WriterError as exc:
            last_error = exc
            log.warning("writer failed on %s: %s", item.url, exc)
    raise last_error or writer.WriterError("no article written")


def request_batch_approval(batch):
    owner = _owner()
    if owner is None:
        raise RuntimeError("no user to approve the batch")
    from core.agent.approvals import _new_code
    from core.agent.channels import DeliveryError, send_to_self

    articles = list(batch.articles.filter(status="draft").order_by("slot"))
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    review = f"{site}{reverse('news:review')}" if site else reverse("news:review")
    lines = [f"📰 *Rasimu za habari — {swahili_date(batch.date)}* ({len(articles)})", ""]
    for a in articles:
        lines.append(f"{a.slot}. [{a.get_category_display()}] {a.title}")
    approval = ApprovalRequest.objects.create(
        user=owner, tool="publish_news", code=_new_code(),
        recipient_name="wILife", body="\n".join(lines),
        context=f"news_batch:{batch.pk}", status="pending",
        expires_at=timezone.now() + timedelta(hours=getattr(settings, "AGENT_APPROVAL_TTL_HOURS", 24)),
    )
    lines += ["", f"Soma/hariri: {review}", f"Jibu *OK {approval.code}* kuchapisha zote",
              f"Jibu *NO {approval.code}* kuzikataa"]
    batch.approval = approval
    batch.status = "pending"
    batch.save(update_fields=["approval", "status"])
    try:
        send_to_self(owner, "\n".join(lines))
    except DeliveryError as exc:
        log.error("news approval message not delivered: %s", exc)
        approval.result = f"preview delivery failed: {exc}"
        approval.save(update_fields=["result"])
    return approval


@transaction.atomic
def publish_batch(batch):
    now = timezone.now()
    count = 0
    for article in batch.articles.filter(status="draft").order_by("slot"):
        # Stagger by a minute so "latest" ordering follows the slot order.
        article.publish(when=now - timedelta(minutes=article.slot))
        count += 1
    batch.status = "published"
    batch.save(update_fields=["status"])
    paths = [a.get_absolute_url() for a in batch.articles.filter(status="published")]
    transaction.on_commit(lambda: _announce(paths))
    return count


def _announce(paths):
    from news import indexnow
    indexnow.submit(paths + ["/", "/habari/"])


def reject_batch(batch):
    batch.articles.filter(status="draft").update(status="rejected")
    batch.status = "rejected"
    batch.save(update_fields=["status"])


def _offer_more(batch):
    """Tell the owner about drafts written after the batch was first sent."""
    if batch.status == "published" or not batch.approval or batch.approval.status != "pending":
        # The first part is already decided: ask again for the new drafts only.
        batch.status = "drafting"
        batch.save(update_fields=["status"])
        request_batch_approval(batch)
        return
    from core.agent.channels import DeliveryError, send_to_self
    drafts = list(batch.articles.filter(status="draft").order_by("slot"))
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    lines = [f"📰 *Rasimu zaidi — {swahili_date(batch.date)}* ({len(drafts)})", ""]
    lines += [f"{a.slot}. [{a.get_category_display()}] {a.title}" for a in drafts]
    lines += ["", f"Soma/hariri: {site}{reverse('news:review')}",
              f"Jibu *OK {batch.approval.code}* kuchapisha zote", f"Jibu *NO {batch.approval.code}* kuzikataa"]
    try:
        send_to_self(batch.approval.user, "\n".join(lines))
    except DeliveryError as exc:
        log.error("news top-up message not delivered: %s", exc)


def run(now=None, deadline=None, dry_run=False, force=False):
    """Agent job: draft today's batch a few articles per tick, then ask for approval."""
    if not getattr(settings, "NEWS_ENABLED", False) and not force:
        return {"job": "news_drafts", "sent": 0, "failed": 0, "detail": "disabled"}
    now = now or timezone.now()
    local = timezone.localtime(now)
    if not force and local.hour < getattr(settings, "NEWS_DRAFT_HOUR", 5):
        return {"job": "news_drafts", "sent": 0, "failed": 0, "detail": "too early"}
    if not writer.enabled():
        return {"job": "news_drafts", "sent": 0, "failed": 0, "detail": "GROQ_API_KEY not set"}
    if dry_run:
        return {"job": "news_drafts", "sent": 0, "failed": 0, "detail": "dry run"}

    batch, _ = NewsBatch.objects.get_or_create(date=local.date())
    if batch.status in ("published", "rejected") and not batch.articles.exists():
        # Closed before anything was written (e.g. "publish" pressed on an empty batch): reopen it.
        batch.status = "drafting"
        batch.save(update_fields=["status"])
    missing = [s for s, _ in plan_for(batch.date) if not batch.articles.filter(slot=s).exists()]
    top_up = (batch.status in ("pending", "published") and missing
              and local.hour < getattr(settings, "NEWS_STOP_HOUR", 16))
    if batch.status != "drafting" and not top_up:
        return {"job": "news_drafts", "sent": 0, "failed": 0, "detail": f"batch {batch.status}"}

    done_slots = set(batch.articles.values_list("slot", flat=True))
    written = failed = 0
    notes = []
    for slot, category in plan_for(batch.date):
        if slot in done_slots:
            continue
        if deadline and time.monotonic() > deadline - 8:  # leave room for one LLM call
            notes.append("deadline — continuing next tick")
            break
        try:
            fill_slot(batch, slot, category)
            written += 1
        except Exception as exc:
            failed += 1
            notes.append(f"slot {slot}: {exc}"[:200])
            log.warning("news slot %s failed: %s", slot, exc)

    remaining = [s for s, _ in plan_for(batch.date)
                 if not batch.articles.filter(slot=s).exists()]
    if top_up:
        # A partial batch already went out; slots that failed earlier are retried
        # until NEWS_STOP_HOUR and offered to the owner as they arrive.
        if written:
            _offer_more(batch)
            notes.append(f"top-up: {written} more draft(s) offered")
    elif not remaining:
        request_batch_approval(batch)
        notes.append("batch complete — approval requested")
    elif failed and local.hour >= getattr(settings, "NEWS_GIVE_UP_HOUR", 10) and batch.articles.exists():
        # Some feeds are dry today: send what we have rather than nothing.
        request_batch_approval(batch)
        notes.append(f"partial batch sent for approval (missing slots {remaining})")

    if notes:
        batch.notes = (batch.notes + "\n" + "; ".join(notes)).strip()[-2000:]
        batch.save(update_fields=["notes"])
    return {"job": "news_drafts", "sent": written, "failed": failed, "detail": "; ".join(notes)}

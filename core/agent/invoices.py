"""
Invoice watch — Step 4 / Step 6.

Two things happen here, and the split between them is the whole point of the
permission model:

    TIER A — telling William an invoice is overdue. Automatic.
    TIER B — messaging the client about it. Drafted, then it waits.

Runs once a day. Nobody needs to be told about the same overdue invoice every
fifteen minutes.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.agent import approvals
from core.agent.brief import already_ran_today
from core.agent.jamiitek import JamiiTekUnavailable, fetch_overdue_invoices, is_configured
from core.agent.tools import TIER_B, draft_invoice_followup
from core.agent.channels import DeliveryError, send_to_self
from core.models import AgentRun

log = logging.getLogger("core.agent")


def _summary(invoices):
    total = sum(Decimal(str(i.get("amount") or 0)) for i in invoices)
    lines = [
        f"🧾 *Invoice zilizochelewa* ({len(invoices)})",
        "",
    ]
    for inv in invoices[:8]:
        lines.append(
            f"  {inv.get('client_name', '?')} — TZS {Decimal(str(inv.get('amount') or 0)):,.0f}"
            f"  _(siku {inv.get('days_late', 0)})_"
        )
    if len(invoices) > 8:
        lines.append(f"  _na nyingine {len(invoices) - 8}_")
    lines.append("")
    lines.append(f"*Jumla: TZS {total:,.0f}*")
    return "\n".join(lines)


def run_invoice_watch(now=None, deadline=None, dry_run=False):
    """
    Report overdue invoices, and optionally prepare follow-up drafts.

    Drafts are only prepared for invoices past AGENT_INVOICE_DRAFT_AFTER_DAYS,
    and only up to AGENT_INVOICE_DRAFT_MAX per day. An agent that produces
    fifteen approval requests in one morning is not helping — William will stop
    reading them, and then the whole gate is theatre.
    """
    now = now or timezone.now()

    if not getattr(settings, "AGENT_INVOICE_WATCH_ENABLED", True):
        return {"job": "invoice_watch", "sent": 0, "failed": 0, "detail": "disabled"}

    if not is_configured():
        return {"job": "invoice_watch", "sent": 0, "failed": 0, "detail": "JamiiTek not configured"}

    hour = getattr(settings, "AGENT_INVOICE_WATCH_HOUR", 8)
    if timezone.localtime(now).hour < hour:
        return {"job": "invoice_watch", "sent": 0, "failed": 0, "detail": "too early"}

    if already_ran_today("invoice_watch", now=now):
        return {"job": "invoice_watch", "sent": 0, "failed": 0, "detail": "already ran today"}

    try:
        invoices = fetch_overdue_invoices()
    except JamiiTekUnavailable as exc:
        log.warning("invoice watch skipped: %s", exc)
        return {"job": "invoice_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    if not invoices:
        # Record the day as done so it doesn't re-query every tick.
        if not dry_run:
            AgentRun.objects.create(job="invoice_watch", finished_at=timezone.now(),
                                    ok=True, processed=0, detail="none overdue")
        return {"job": "invoice_watch", "sent": 0, "failed": 0, "detail": "none overdue"}

    user = User.objects.filter(is_active=True).order_by("pk").first()
    if user is None:
        return {"job": "invoice_watch", "sent": 0, "failed": 1, "detail": "no user"}

    text = _summary(invoices)

    if dry_run:
        log.info("DRY RUN — invoice watch:\n%s", text)
        return {"job": "invoice_watch", "sent": 1, "failed": 0, "detail": "dry run"}

    # --- TIER A: tell William ---------------------------------------------
    try:
        send_to_self(user, text)
        sent = 1
        failed = 0
    except DeliveryError as exc:
        log.error("invoice watch summary failed: %s", exc)
        return {"job": "invoice_watch", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    # --- TIER B: prepare drafts, which then wait --------------------------
    drafts = 0
    if getattr(settings, "AGENT_INVOICE_DRAFTS_ENABLED", False):
        threshold = getattr(settings, "AGENT_INVOICE_DRAFT_AFTER_DAYS", 7)
        cap = getattr(settings, "AGENT_INVOICE_DRAFT_MAX", 3)

        for inv in invoices:
            if drafts >= cap:
                break
            if inv.get("days_late", 0) < threshold:
                continue
            if not inv.get("client_number"):
                continue

            draft = draft_invoice_followup(
                client_name=inv.get("client_name", ""),
                client_number=inv.get("client_number", ""),
                invoice_ref=inv.get("invoice_ref", ""),
                amount=inv.get("amount") or 0,
                days_late=inv.get("days_late", 0),
            )

            # Optional polish. Still a draft either way — the model's output is
            # no closer to being sent than the template's was.
            try:
                from core.agent.brain import polish_draft
                draft["body"] = polish_draft(draft["body"], draft.get("context", ""))
            except Exception:
                pass

            approvals.request_approval(
                user=user,
                tool_name="draft_invoice_followup",
                draft=draft,
                summary=draft.get("context", ""),
            )
            drafts += 1

    AgentRun.objects.create(
        job="invoice_watch", finished_at=timezone.now(), ok=True,
        processed=len(invoices),
        detail=f"{len(invoices)} overdue, {drafts} draft(s) awaiting approval",
    )

    return {
        "job": "invoice_watch",
        "sent": sent,
        "failed": failed,
        "detail": f"{len(invoices)} overdue, {drafts} draft(s) [tier {TIER_B}] awaiting approval",
    }

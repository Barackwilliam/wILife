"""
Agent jobs.

A job is a plain function that wakes up, finds work in the database, does it,
and reports what happened. No LLM is involved at this stage — every decision
here is deterministic Python.

PERMISSION TIER: everything in this file is TIER A (see spec Section 5) —
it only ever sends messages to William himself. Nothing here may be reused to
message a third party. When TIER B lands in Step 3, drafts go through the
dispatcher, not through these functions.
"""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import AgentRun, Schedule
from core.agent import approvals
from core.agent.brief import run_morning_brief
from core.agent.invoices import run_invoice_watch
from core.agent.channels import DeliveryError, send_to_self

log = logging.getLogger("core.agent")

# Claim at most this many reminders per tick. Keeps the request fast and bounds
# the damage if something is badly wrong.
BATCH_SIZE = 10


def _format_reminder(schedule):
    local_start = timezone.localtime(schedule.start_datetime)
    lines = [
        "⏰ *Kumbusho*",
        "",
        f"*{schedule.title}*",
        f"🕐 {local_start.strftime('%H:%M')} — {local_start.strftime('%d/%m/%Y')}",
    ]
    if schedule.location:
        lines.append(f"📍 {schedule.location}")
    if schedule.description:
        lines.append("")
        lines.append(schedule.description[:500])
    return "\n".join(lines)


def run_schedule_reminders(now=None, deadline=None, dry_run=False):
    """
    Send WhatsApp reminders for schedules whose reminder time has arrived.

    Concurrency: rows are claimed inside a short transaction using
    SELECT ... FOR UPDATE SKIP LOCKED and marked sent immediately. Network
    sending happens *outside* the transaction, so a slow WhatsApp call never
    holds a database lock. If a send then fails, the claim is released so the
    next tick retries it. This makes overlapping ticks safe — which matters,
    because an external pinger can fire again before the previous run finished.
    """
    now = now or timezone.now()
    stale_before = now - timedelta(hours=settings.AGENT_STALE_REMINDER_HOURS)

    sent = failed = skipped = 0
    notes = []

    # --- claim phase -------------------------------------------------------
    with transaction.atomic():
        claimed = list(
            Schedule.objects
            .select_for_update(skip_locked=True, of=("self",))
            .filter(
                reminder_datetime__isnull=False,
                reminder_datetime__lte=now,
                reminder_sent=False,
            )
            .select_related("user")
            .order_by("reminder_datetime")[:BATCH_SIZE]
        )
        if claimed and not dry_run:
            Schedule.objects.filter(pk__in=[s.pk for s in claimed]).update(reminder_sent=True)

    if not claimed:
        return {"job": "schedule_reminders", "sent": 0, "failed": 0, "skipped": 0, "detail": "nothing due"}

    # --- send phase --------------------------------------------------------
    for schedule in claimed:
        if deadline and time.monotonic() > deadline:
            # Out of time. Release the rest so the next tick picks them up.
            remaining = [s.pk for s in claimed[claimed.index(schedule):]]
            if not dry_run:
                Schedule.objects.filter(pk__in=remaining).update(reminder_sent=False)
            notes.append(f"deadline reached, released {len(remaining)}")
            break

        # A reminder that is a day late is noise, not help. Swallow it quietly
        # rather than flooding William after any period of downtime.
        if schedule.reminder_datetime < stale_before:
            skipped += 1
            log.info("skipping stale reminder id=%s (%s)", schedule.pk, schedule.title)
            continue

        text = _format_reminder(schedule)

        if dry_run:
            log.info("DRY RUN — would send reminder:\n%s", text)
            sent += 1
            continue

        try:
            send_to_self(schedule.user, text)
            sent += 1
        except DeliveryError as exc:
            failed += 1
            notes.append(f"id={schedule.pk}: {exc}")
            # Release the claim so the next tick tries again.
            Schedule.objects.filter(pk=schedule.pk).update(reminder_sent=False)
            log.error("reminder id=%s failed: %s", schedule.pk, exc)

    return {
        "job": "schedule_reminders",
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "detail": "; ".join(notes)[:2000],
    }


# Registry of jobs the tick runs, in order. Reminders come first: they are
# time-critical, the brief is not.
def run_expire_approvals(now=None, deadline=None, dry_run=False):
    """Housekeeping: time out approval requests William never answered."""
    if dry_run:
        return {"job": "expire_approvals", "sent": 0, "failed": 0, "detail": "dry run"}
    count = approvals.expire_stale(now=now)
    return {"job": "expire_approvals", "sent": 0, "failed": 0,
            "detail": f"expired {count}" if count else "none"}


# Registry of jobs the tick runs, in order. Reminders come first: they are
# time-critical, the rest are not.
JOBS = {
    "schedule_reminders": run_schedule_reminders,
    "morning_brief": run_morning_brief,
    "invoice_watch": run_invoice_watch,
    "expire_approvals": run_expire_approvals,
}


def run_tick(only=None, dry_run=False):
    """
    Run every registered job once, within a time budget.

    Returns a summary dict. Never raises — a failing job is recorded and the
    tick continues, because a crash in one job must not stop the heartbeat.
    """
    started = time.monotonic()
    deadline = started + settings.AGENT_TICK_BUDGET_SECONDS
    now = timezone.now()

    run = AgentRun.objects.create(job=only or "tick")
    results = []
    total_processed = total_failed = 0

    for name, job in JOBS.items():
        if only and name != only:
            continue
        try:
            result = job(now=now, deadline=deadline, dry_run=dry_run)
        except Exception as exc:  # a broken job must not kill the heartbeat
            log.exception("job %s crashed", name)
            result = {"job": name, "sent": 0, "failed": 1, "detail": f"crashed: {exc}"}
        results.append(result)
        total_processed += result.get("sent", 0)
        total_failed += result.get("failed", 0)

    elapsed = round(time.monotonic() - started, 2)

    run.finished_at = timezone.now()
    run.ok = total_failed == 0
    run.processed = total_processed
    run.failed = total_failed
    run.detail = "; ".join(
        f"{r['job']}: sent={r.get('sent', 0)} failed={r.get('failed', 0)} {r.get('detail', '')}".strip()
        for r in results
    )[:4000]
    run.save(update_fields=["finished_at", "ok", "processed", "failed", "detail"])

    summary = {
        "ok": run.ok,
        "elapsed_seconds": elapsed,
        "processed": total_processed,
        "failed": total_failed,
        "jobs": results,
    }
    log.info("tick complete in %ss — processed=%s failed=%s", elapsed, total_processed, total_failed)
    return summary

"""
The morning brief.

Same heartbeat, new job. At the configured hour each morning the agent assembles
one message: today's schedule, what matters most, what slipped, and where the
money stands.

Still no LLM. Every line here is a database query and a template. The model
arrives in Step 5 to phrase this better — not to decide what goes in it.

PERMISSION TIER: A (see spec Section 5). This only ever messages William.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Case, IntegerField, Sum, When
from django.utils import timezone

from core.models import AgentRun, Expense, Income, Schedule, Task
from core.agent.channels import DeliveryError, send_to_self
from core.agent.goals import brief_section as goals_section

log = logging.getLogger("core.agent")

PRIORITY_ORDER = Case(
    When(priority="high", then=0),
    When(priority="medium", then=1),
    When(priority="low", then=2),
    default=3,
    output_field=IntegerField(),
)

PRIORITY_MARK = {"high": "🔴", "medium": "🟡", "low": "⚪"}


def _money(value):
    """Format a Decimal as TZS with thousand separators."""
    return f"{Decimal(value or 0):,.0f}"


def already_ran_today(job_name, now=None):
    """
    True if this job already completed successfully today (local time).

    Uses AgentRun as the marker so no extra table or migration is needed. The
    tick may fire dozens of times a day; the brief must go out once.
    """
    local_today = timezone.localtime(now or timezone.now()).date()
    return AgentRun.objects.filter(
        job=job_name,
        ok=True,
        started_at__date=local_today,
    ).exists()


def _brief_recipients():
    """
    Users who should receive a brief.

    Only users with an explicit whatsapp_number on their profile. The global
    fallback is used ONLY when there is exactly one user in the system — that
    keeps a single-operator setup effortless without ever risking one user's
    brief landing on another user's phone.
    """
    from core.agent.channels import self_channel

    users = list(User.objects.filter(is_active=True).select_related("profile"))

    # On Telegram there is one configured chat — the owner's. Deliver to the
    # first active user and stop; there is no per-user routing to do.
    channel = self_channel()
    if channel in ("telegram", "email", "all"):
        # One configured destination — the owner's. No per-user routing to do.
        return [(users[0], channel)] if users else []

    recipients = []
    for user in users:
        profile = getattr(user, "profile", None)
        number = (getattr(profile, "whatsapp_number", "") or "").strip()
        if number:
            recipients.append((user, number))
    if not recipients and len(users) == 1 and settings.AGENT_DEFAULT_RECIPIENT:
        recipients.append((users[0], settings.AGENT_DEFAULT_RECIPIENT))
    return recipients


def build_brief(user, now=None):
    """
    Assemble the brief for one user. Returns the message text.

    Kept separate from sending so it can be inspected in the shell:
        from core.agent.brief import build_brief
        print(build_brief(User.objects.first()))
    """
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    today = local_now.date()

    day_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    day_end = day_start + timedelta(days=1)

    # --- today's schedule --------------------------------------------------
    schedules = list(
        Schedule.objects.filter(
            user=user,
            start_datetime__gte=day_start,
            start_datetime__lt=day_end,
        ).order_by("start_datetime")
    )

    # --- tasks -------------------------------------------------------------
    todays_tasks = list(
        Task.objects.filter(user=user, status="pending", date=today)
        .annotate(rank=PRIORITY_ORDER)
        .order_by("rank")[:3]
    )
    overdue_tasks = list(
        Task.objects.filter(user=user, status="pending", date__lt=today)
        .annotate(rank=PRIORITY_ORDER)
        .order_by("rank", "date")
    )

    # --- money, this month -------------------------------------------------
    month_start = today.replace(day=1)
    income = Income.objects.filter(user=user, date__gte=month_start, date__lte=today).aggregate(
        total=Sum("amount")
    )["total"] or Decimal(0)
    expense = Expense.objects.filter(user=user, date__gte=month_start, date__lte=today).aggregate(
        total=Sum("amount")
    )["total"] or Decimal(0)
    balance = income - expense

    # --- compose -----------------------------------------------------------
    lines = [
        f"☀️ *Habari za asubuhi, {user.first_name or user.username}*",
        f"_{local_now.strftime('%A, %d %B %Y')}_",
        "",
    ]

    lines.append("📅 *Ratiba ya leo*")
    if schedules:
        for s in schedules:
            start = timezone.localtime(s.start_datetime).strftime("%H:%M")
            place = f" — {s.location}" if s.location else ""
            lines.append(f"  {start}  {s.title}{place}")
    else:
        lines.append("  _Hakuna kilichopangwa_")
    lines.append("")

    lines.append("✅ *Vipaumbele*")
    if todays_tasks:
        for t in todays_tasks:
            lines.append(f"  {PRIORITY_MARK.get(t.priority, '⚪')} {t.title}")
    else:
        lines.append("  _Hakuna task ya leo_")
    lines.append("")

    try:
        lines.extend(goals_section(user))
    except Exception as exc:  # a goals schema surprise must not kill the brief
        log.warning("goals section skipped: %s", exc)

    if overdue_tasks:
        lines.append(f"⚠️ *Zilizopitwa na muda* ({len(overdue_tasks)})")
        for t in overdue_tasks[:3]:
            days_late = (today - t.date).days
            lines.append(f"  {t.title} — siku {days_late}")
        if len(overdue_tasks) > 3:
            lines.append(f"  _na nyingine {len(overdue_tasks) - 3}_")
        lines.append("")

    lines.append(f"💰 *Mwezi huu* (tangu {month_start.strftime('%d/%m')})")
    lines.append(f"  Mapato:   {_money(income)}")
    lines.append(f"  Matumizi: {_money(expense)}")
    if balance < 0:
        lines.append(f"  *Pungufu: {_money(abs(balance))}* ⚠️")
    else:
        lines.append(f"  Salio:    {_money(balance)}")

    return "\n".join(lines)


def run_morning_brief(now=None, deadline=None, dry_run=False):
    """
    Send the morning brief, once per day, at or after the configured hour.

    Deliberately forgiving about timing: it fires on the first tick at or after
    the target hour rather than demanding an exact time. A tick that is late
    because the service was restarting should still deliver the brief, not skip
    the day entirely.
    """
    now = now or timezone.now()
    local_now = timezone.localtime(now)

    if not getattr(settings, "AGENT_MORNING_BRIEF_ENABLED", True):
        return {"job": "morning_brief", "sent": 0, "failed": 0, "detail": "disabled"}

    target_hour = getattr(settings, "AGENT_MORNING_BRIEF_HOUR", 6)
    if local_now.hour < target_hour:
        return {"job": "morning_brief", "sent": 0, "failed": 0, "detail": "too early"}

    # If the service was down all morning, don't deliver a "morning" brief in
    # the evening. Silence beats a message that is obviously wrong.
    cutoff_hour = getattr(settings, "AGENT_MORNING_BRIEF_CUTOFF_HOUR", 12)
    if local_now.hour >= cutoff_hour:
        return {"job": "morning_brief", "sent": 0, "failed": 0, "detail": "past cutoff"}

    if already_ran_today("morning_brief", now=now):
        return {"job": "morning_brief", "sent": 0, "failed": 0, "detail": "already sent today"}

    recipients = _brief_recipients()
    if not recipients:
        return {"job": "morning_brief", "sent": 0, "failed": 1, "detail": "no recipient configured"}

    sent = failed = 0
    notes = []

    for user, number in recipients:
        text = build_brief(user, now=now)

        if dry_run:
            log.info("DRY RUN — morning brief for %s via %s:\n%s", user.username, number, text)
            sent += 1
            continue

        try:
            send_to_self(user, text)
            sent += 1
        except DeliveryError as exc:
            failed += 1
            notes.append(f"{user.username}: {exc}")
            log.error("morning brief failed for %s: %s", user.username, exc)

    # Mark the day done only if everyone got theirs. A partial failure should
    # be retried by the next tick, not written off.
    if sent and not failed and not dry_run:
        AgentRun.objects.create(
            job="morning_brief",
            finished_at=timezone.now(),
            ok=True,
            processed=sent,
            failed=0,
            detail="brief delivered",
        )

    return {
        "job": "morning_brief",
        "sent": sent,
        "failed": failed,
        "detail": "; ".join(notes)[:2000],
    }

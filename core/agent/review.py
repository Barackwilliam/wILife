"""
The weekly review — 80/20 applied to your own records.

Once a week it answers three questions from data you already keep:

    Where did the money come from?
    Where did it go?
    What did you carry all week without finishing?

The value is not the totals — you can see those on the dashboard. It is the
concentration: which few sources produced most of the income, and which few
categories consumed most of the spending. That is the 20% worth your attention,
and it is invisible when you look at a list of transactions one at a time.

Deterministic. No LLM. TIER A — reaches William only.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.utils import timezone

from core.agent.brief import already_ran_today
from core.agent.channels import DeliveryError, send_to_self
from core.models import AgentRun, Expense, Income, Task

log = logging.getLogger("core.agent")


def _money(value):
    return f"{Decimal(value or 0):,.0f}"


def _concentration(rows, total):
    """
    How much of the total comes from the top few rows.

    Returns (top_share_percent, count_needed_for_80_percent).
    """
    if not total or total == 0:
        return 0, 0
    running = Decimal(0)
    needed = 0
    for row in rows:
        running += Decimal(str(row["total"] or 0))
        needed += 1
        if running / Decimal(str(total)) >= Decimal("0.8"):
            break
    top_share = (Decimal(str(rows[0]["total"] or 0)) / Decimal(str(total)) * 100) if rows else 0
    return int(top_share), needed


def build_review(user, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    start = today - timedelta(days=7)

    income_rows = list(
        Income.objects.filter(user=user, date__gte=start, date__lte=today)
        .values("source").annotate(total=Sum("amount"), n=Count("id"))
        .order_by("-total")
    )
    expense_rows = list(
        Expense.objects.filter(user=user, date__gte=start, date__lte=today)
        .values("category").annotate(total=Sum("amount"), n=Count("id"))
        .order_by("-total")
    )

    income_total = sum(Decimal(str(r["total"] or 0)) for r in income_rows)
    expense_total = sum(Decimal(str(r["total"] or 0)) for r in expense_rows)

    done = Task.objects.filter(user=user, status="done", date__gte=start, date__lte=today).count()
    still_pending = list(
        Task.objects.filter(user=user, status="pending", date__lt=today)
        .order_by("date")[:5]
    )

    lines = [
        "📊 *Mapitio ya wiki*",
        f"_{start.strftime('%d/%m')} – {today.strftime('%d/%m')}_",
        "",
    ]

    lines.append(f"💰 *Mapato: {_money(income_total)}*")
    if income_rows:
        for row in income_rows[:4]:
            share = int(Decimal(str(row["total"] or 0)) / income_total * 100) if income_total else 0
            lines.append(f"  {row['source'] or '(bila jina)'} — {_money(row['total'])} ({share}%)")
        top_share, needed = _concentration(income_rows, income_total)
        if len(income_rows) > 1:
            lines.append(f"  _Vyanzo {needed} kati ya {len(income_rows)} vinatoa 80%_")
    else:
        lines.append("  _Hakuna_")
    lines.append("")

    lines.append(f"💸 *Matumizi: {_money(expense_total)}*")
    if expense_rows:
        for row in expense_rows[:4]:
            share = int(Decimal(str(row["total"] or 0)) / expense_total * 100) if expense_total else 0
            lines.append(f"  {row['category']} — {_money(row['total'])} ({share}%)")
    else:
        lines.append("  _Hakuna_")
    lines.append("")

    net = income_total - expense_total
    if net < 0:
        lines.append(f"⚠️ *Umetumia zaidi ya ulivyoingiza kwa {_money(abs(net))}*")
    else:
        lines.append(f"✅ *Umebakiza {_money(net)}*")
    lines.append("")

    lines.append(f"✅ *Task zilizokamilika: {done}*")
    if still_pending:
        lines.append(f"⚠️ *Zinazoendelea kubebwa* ({len(still_pending)})")
        for task in still_pending:
            days = (today - task.date).days
            lines.append(f"  {task.title} — siku {days}")
        lines.append("")
        lines.append("_Zilizokaa zaidi ya wiki mbili: zifute au zipangie tarehe halisi._")

    return "\n".join(lines)


def run_weekly_review(now=None, deadline=None, dry_run=False):
    """
    Send the weekly review, once, on the configured weekday.

    Default is Sunday evening — late enough that the week is actually over,
    early enough that you can still act on it before Monday.
    """
    now = now or timezone.now()

    if not getattr(settings, "AGENT_WEEKLY_REVIEW_ENABLED", False):
        return {"job": "weekly_review", "sent": 0, "failed": 0, "detail": "disabled"}

    local_now = timezone.localtime(now)
    weekday = getattr(settings, "AGENT_WEEKLY_REVIEW_WEEKDAY", 6)   # 0=Mon, 6=Sun
    hour = getattr(settings, "AGENT_WEEKLY_REVIEW_HOUR", 18)

    if local_now.weekday() != weekday:
        return {"job": "weekly_review", "sent": 0, "failed": 0, "detail": "not the day"}
    if local_now.hour < hour:
        return {"job": "weekly_review", "sent": 0, "failed": 0, "detail": "too early"}
    if already_ran_today("weekly_review", now=now):
        return {"job": "weekly_review", "sent": 0, "failed": 0, "detail": "already sent"}

    user = User.objects.filter(is_active=True).order_by("pk").first()
    if user is None:
        return {"job": "weekly_review", "sent": 0, "failed": 1, "detail": "no user"}

    text = build_review(user, now=now)

    if dry_run:
        log.info("DRY RUN — weekly review:\n%s", text)
        return {"job": "weekly_review", "sent": 1, "failed": 0, "detail": "dry run"}

    try:
        send_to_self(user, text)
    except DeliveryError as exc:
        log.error("weekly review failed: %s", exc)
        return {"job": "weekly_review", "sent": 0, "failed": 1, "detail": str(exc)[:200]}

    AgentRun.objects.create(job="weekly_review", finished_at=timezone.now(),
                            ok=True, processed=1, detail="review sent")
    return {"job": "weekly_review", "sent": 1, "failed": 0, "detail": ""}

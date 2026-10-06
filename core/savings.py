"""
Monthly savings goals.

A goal of kind "monthly_savings" says: every month, what is left after
expenses should be at least `target_value`.

    saved this month = income − expenses        (expenses in the "savings"
                                                 category are money put aside,
                                                 so they are not counted)

Month by month, starting at the goal's start month:

    available = saved − debt brought forward
    available <  0  → nothing toward the goal; −available becomes the debt
                      carried into next month (paid first next month)
    available >= 0  → the debt is cleared; `available` counts toward the target
                      (reached, or short by target − available)

Only a real deficit (spending more than you earned) is carried forward. A
month that saved something but less than the target is recorded as missed;
a surplus above the target is recorded but not carried.

Nothing is stored per month: the history is recomputed from income and
expenses every time, so correcting an old entry corrects the whole chain.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import Expense, Goal, Income

SAVINGS_CATEGORY = "savings"
SW_MONTHS = ["Januari", "Februari", "Machi", "Aprili", "Mei", "Juni", "Julai",
             "Agosti", "Septemba", "Oktoba", "Novemba", "Desemba"]
ZERO = Decimal("0")


@dataclass
class Month:
    year: int
    month: int
    income: Decimal
    expenses: Decimal
    saved: Decimal          # income − expenses
    debt_in: Decimal        # deficit carried from the month before
    toward_goal: Decimal    # what counts toward the target after paying debt_in
    debt_out: Decimal       # deficit carried into the next month
    target: Decimal
    is_current: bool

    @property
    def label(self):
        return f"{SW_MONTHS[self.month - 1]} {self.year}"

    @property
    def reached(self):
        return self.toward_goal >= self.target

    @property
    def surplus(self):
        return max(self.toward_goal - self.target, ZERO)

    @property
    def shortfall(self):
        return max(self.target - self.toward_goal, ZERO)

    @property
    def debt_paid(self):
        """How much of the debt brought in was paid off this month."""
        return min(self.debt_in, max(self.saved, ZERO))

    @property
    def percent(self):
        if not self.target:
            return 100
        return int(min(self.toward_goal / self.target * 100, 100))

    @property
    def status(self):
        if self.debt_out > 0:
            return "deficit"
        if self.reached:
            return "reached"
        return "in_progress" if self.is_current else "missed"


def _month_start(d):
    return date(d.year, d.month, 1)


def _next_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_end(d):
    return date.fromordinal(_next_month(d).toordinal() - 1)


def month_totals(user, year, month):
    """(income, expenses) for one calendar month; savings transfers are not expenses."""
    income = Income.objects.filter(user=user, date__year=year, date__month=month).aggregate(t=Sum("amount"))["t"]
    spent = (Expense.objects.filter(user=user, date__year=year, date__month=month)
             .exclude(category=SAVINGS_CATEGORY).aggregate(t=Sum("amount"))["t"])
    return income or ZERO, spent or ZERO


def ledger(goal, today=None):
    """Every month from the goal's start month to the current month, oldest first."""
    today = today or timezone.localdate()
    target = goal.target_value or ZERO
    months, debt = [], ZERO
    cursor, last = _month_start(goal.start_date), _month_start(today)
    while cursor <= last:
        income, expenses = month_totals(goal.user, cursor.year, cursor.month)
        saved = income - expenses
        available = saved - debt
        toward, debt_out = (ZERO, -available) if available < 0 else (available, ZERO)
        months.append(Month(cursor.year, cursor.month, income, expenses, saved, debt, toward,
                            debt_out, target, cursor == last))
        debt = debt_out
        cursor = _next_month(cursor)
    return months


def current_month(goal, today=None):
    rows = ledger(goal, today)
    return rows[-1] if rows else None


def sync(goal, today=None):
    """Keep the stored fields in step with the ledger (used by lists, admin and the agent)."""
    if not goal.is_monthly_savings:
        return None
    today = today or timezone.localdate()
    month = current_month(goal, today)
    if month is None:  # starts in a future month
        return None
    fields = {"current_value": month.toward_goal, "target_date": month_end(today), "unit": goal.unit or "Tsh"}
    changed = [name for name, value in fields.items() if getattr(goal, name) != value]
    for name in changed:
        setattr(goal, name, fields[name])
    if changed:
        goal.save(update_fields=changed)
    return month


def sync_user(user, today=None):
    for goal in Goal.objects.filter(user=user, kind=Goal.KIND_MONTHLY_SAVINGS, status="active"):
        sync(goal, today)


def money(value):
    return f"TZS {value:,.0f}"


def _on_money_change(sender, instance, **kwargs):
    """An income or expense changed: refresh that user's monthly savings goals."""
    sync_user(instance.user)


def connect_signals():
    from django.db.models.signals import post_delete, post_save
    for model in (Income, Expense):
        post_save.connect(_on_money_change, sender=model, dispatch_uid=f"savings-save-{model.__name__}")
        post_delete.connect(_on_money_change, sender=model, dispatch_uid=f"savings-del-{model.__name__}")


# ---------------------------------------------------------------------------
# Agent: morning brief line, month-end report, overspend warning
# ---------------------------------------------------------------------------

def _active_goals(user=None):
    qs = Goal.objects.filter(kind=Goal.KIND_MONTHLY_SAVINGS, status="active").select_related("user")
    return qs.filter(user=user) if user is not None else qs


def brief_lines(user, today=None):
    """Lines for the morning brief, or [] when the user has no monthly savings goal."""
    today = today or timezone.localdate()
    lines = []
    for goal in _active_goals(user):
        month = current_month(goal, today)
        if month is None:
            continue
        left = (month_end(today) - today).days
        lines.append(f"🏦 *Akiba ya mwezi* — {goal.title}")
        if month.debt_in:
            lines.append(f"  Deni la mwezi uliopita: {money(month.debt_paid)} / {money(month.debt_in)} limelipwa")
        if month.debt_out:
            lines.append(f"  ⚠️ Uko hasi kwa {money(month.debt_out)} — siku {left} zimebaki")
        else:
            lines.append(f"  {money(month.toward_goal)} / {money(month.target)} ({month.percent}%) · siku {left} zimebaki")
        lines.append("")
    return lines


def month_report(goal, month, next_target):
    lines = [f"🏦 *Akiba ya {month.label}*", "", f"*{goal.title}*",
             f"  Mapato:   {money(month.income)}", f"  Matumizi: {money(month.expenses)}",
             f"  Akiba:    {money(month.saved)}"]
    if month.debt_in:
        lines.append(f"  Deni lililolipwa: {money(month.debt_paid)} / {money(month.debt_in)}")
    lines.append("")
    if month.debt_out:
        lines += [f"⚠️ *Mwezi umeisha hasi kwa {money(month.debt_out)}.*",
                  f"Hilo ndilo lengo la kwanza la mwezi huu: lilipe kwanza, kisha {money(next_target)} ya akiba."]
    elif month.reached:
        extra = f" — umezidi kwa {money(month.surplus)}" if month.surplus else ""
        lines.append(f"✅ *Lengo limefikiwa*{extra}. Hongera!")
    else:
        lines.append(f"Lengo halijafikiwa — umekosa {money(month.shortfall)}. Mwezi mpya unaanza na lengo kamili.")
    return "\n".join(lines)


def _sent(job, key):
    from core.models import AgentRun
    return AgentRun.objects.filter(job=job, ok=True, detail=key).exists()


def _mark(job, key, count):
    from core.models import AgentRun
    AgentRun.objects.create(job=job, ok=True, processed=count, detail=key, finished_at=timezone.now())


def run_watch(now=None, deadline=None, dry_run=False):
    """
    Agent job. Once per month, report how last month went; once per month,
    warn as soon as the current month goes into the red.
    """
    from core.agent.channels import DeliveryError, send_to_self
    today = timezone.localdate(now) if now else timezone.localdate()
    goals = list(_active_goals())
    if not goals:
        return {"job": "savings_watch", "sent": 0, "failed": 0, "detail": "no monthly savings goals"}
    sent = failed = 0
    notes = []
    this_key = today.strftime("%Y-%m")
    prev_key = date.fromordinal(today.replace(day=1).toordinal() - 1).strftime("%Y-%m")

    for goal in goals:
        rows = ledger(goal, today)
        jobs = []
        if len(rows) >= 2 and not _sent("savings_month_report", f"{goal.pk}:{prev_key}"):
            jobs.append(("savings_month_report", f"{goal.pk}:{prev_key}", month_report(goal, rows[-2], goal.target_value)))
        if rows and rows[-1].debt_out and not _sent("savings_deficit_warning", f"{goal.pk}:{this_key}"):
            m = rows[-1]
            text = (f"⚠️ *Akiba: uko hasi*\n\n*{goal.title}*\n"
                    f"Mwezi huu ({m.label}) matumizi yamezidi mapato kwa {money(m.debt_out)}.\n"
                    f"Mwezi ukiisha hivi, kiasi hicho kitakuwa deni la kulipa kwanza mwezi ujao.")
            jobs.append(("savings_deficit_warning", f"{goal.pk}:{this_key}", text))
        for job, key, text in jobs:
            if dry_run:
                sent += 1
                continue
            try:
                send_to_self(goal.user, text)
                _mark(job, key, 1)
                sent += 1
            except DeliveryError as exc:
                failed += 1
                notes.append(f"{job}: {exc}"[:200])
    return {"job": "savings_watch", "sent": sent, "failed": failed, "detail": "; ".join(notes) or "ok"}

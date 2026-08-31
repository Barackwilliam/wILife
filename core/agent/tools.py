"""
The tool layer — Step 3.

Every capability the agent has lives here as a registered tool with a declared
permission tier. The tier is enforced by the dispatcher, in code. It is not a
line in a prompt, because a prompt can be argued with and a dispatcher cannot.

    TIER A  — runs immediately. Only ever reaches William himself.
    TIER B  — produces a draft and an approval request. CANNOT transmit.
    TIER C  — refuses. Always. There is no override parameter, deliberately.

See spec Section 5. If you are adding a tool and you are unsure of its tier,
it is B.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.utils import timezone

from core.models import Expense, Income, Schedule, Task
from core.agent.whatsapp import resolve_recipient, send_whatsapp

log = logging.getLogger("core.agent")

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

REGISTRY = {}


class ToolError(Exception):
    """A tool failed while doing legitimate work."""


class PermissionDenied(Exception):
    """A tool was invoked in a way its tier does not allow."""


def tool(name, tier, description=""):
    """Register a function as an agent tool with a fixed permission tier."""
    def wrapper(func):
        REGISTRY[name] = {
            "name": name,
            "tier": tier,
            "description": description or (func.__doc__ or "").strip().split("\n")[0],
            "func": func,
        }
        func.tool_name = name
        func.tier = tier
        return func
    return wrapper


def dispatch(name, approval=None, **kwargs):
    """
    The only sanctioned way to invoke a tool.

    TIER A executes. TIER B executes ONLY when handed an approved
    ApprovalRequest whose tool matches. TIER C always refuses.

    Never call a TIER B function directly — go through here, so that the gate
    cannot be bypassed by accident six months from now.
    """
    entry = REGISTRY.get(name)
    if entry is None:
        raise ToolError(f"unknown tool: {name}")

    tier = entry["tier"]

    if tier == TIER_C:
        log.warning("blocked TIER C tool invocation: %s", name)
        raise PermissionDenied(
            f"'{name}' is TIER C — prices, money, delivery promises and deletions "
            f"are never taken by the agent. A human does this."
        )

    if tier == TIER_B:
        if approval is None:
            raise PermissionDenied(
                f"'{name}' is TIER B and needs an approved request before it can act. "
                f"Use core.agent.approvals.request_approval() first."
            )
        if approval.tool != name:
            raise PermissionDenied(
                f"approval #{approval.pk} is for '{approval.tool}', not '{name}'"
            )
        if approval.status != "approved":
            raise PermissionDenied(
                f"approval #{approval.pk} is '{approval.status}', not approved"
            )

    return entry["func"](**kwargs)


def describe_tools():
    """Human-readable listing of every registered tool, for logs and debugging."""
    rows = []
    for entry in sorted(REGISTRY.values(), key=lambda e: (e["tier"], e["name"])):
        rows.append(f"[{entry['tier']}] {entry['name']:<26} {entry['description']}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# TIER A — reaches William only
# ---------------------------------------------------------------------------

@tool("get_finances", TIER_A, "Income, expenses and balance for a period")
def get_finances(user, days=30):
    end = timezone.localtime().date()
    start = end - timedelta(days=days)
    income = Income.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(
        t=Sum("amount"))["t"] or Decimal(0)
    expense = Expense.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(
        t=Sum("amount"))["t"] or Decimal(0)
    return {"start": start, "end": end, "income": income,
            "expense": expense, "balance": income - expense}


@tool("get_tasks", TIER_A, "Pending tasks, optionally only overdue ones")
def get_tasks(user, overdue_only=False, limit=20):
    today = timezone.localtime().date()
    qs = Task.objects.filter(user=user, status="pending")
    if overdue_only:
        qs = qs.filter(date__lt=today)
    return list(qs.order_by("date")[:limit])


@tool("get_schedule", TIER_A, "Scheduled items within the next N days")
def get_schedule(user, days=1):
    now = timezone.now()
    return list(
        Schedule.objects.filter(
            user=user, start_datetime__gte=now,
            start_datetime__lt=now + timedelta(days=days),
        ).order_by("start_datetime")
    )


@tool("create_task", TIER_A, "Create a task for William")
def create_task(user, title, date=None, priority="medium", description=""):
    return Task.objects.create(
        user=user, title=title[:255],
        date=date or timezone.localtime().date(),
        priority=priority if priority in ("low", "medium", "high") else "medium",
        description=description or "",
    )


@tool("reschedule_task", TIER_A, "Move one of William's own tasks to a new date")
def reschedule_task(task_id, user, new_date):
    task = Task.objects.filter(pk=task_id, user=user).first()
    if task is None:
        raise ToolError(f"task {task_id} not found for this user")
    task.date = new_date
    task.save(update_fields=["date"])
    return task


@tool("send_message_to_self", TIER_A, "Send William a WhatsApp message")
def send_message_to_self(user, text):
    number = resolve_recipient(user)
    if not number:
        raise ToolError("no WhatsApp number configured for this user")
    return send_whatsapp(number, text)


# ---------------------------------------------------------------------------
# TIER B — drafts only. These functions must never contain a send call.
# ---------------------------------------------------------------------------

@tool("draft_client_message", TIER_B, "Compose a message to a client for approval")
def draft_client_message(recipient_name, recipient_number, body, context=""):
    """
    Returns a draft. Transmission happens only after approval, in
    approvals.execute_approved(), never here.
    """
    return {
        "recipient_name": recipient_name,
        "recipient_number": recipient_number,
        "body": body,
        "context": context,
    }


@tool("draft_invoice_followup", TIER_B, "Compose an overdue-invoice follow-up for approval")
def draft_invoice_followup(client_name, client_number, invoice_ref, amount, days_late):
    body = (
        f"Habari {client_name},\n\n"
        f"Natumai u mzima. Ningependa kukukumbusha kuhusu invoice {invoice_ref} "
        f"yenye kiasi cha TZS {Decimal(amount):,.0f}, ambayo ilitakiwa kulipwa "
        f"siku {days_late} zilizopita.\n\n"
        f"Kama tayari umeshalipa, tafadhali puuza ujumbe huu.\n\n"
        f"Asante,\nWilliam — JamiiTek"
    )
    return {
        "recipient_name": client_name,
        "recipient_number": client_number,
        "body": body,
        "context": f"invoice {invoice_ref}, {days_late} days late",
    }


# ---------------------------------------------------------------------------
# TIER C — registered so that any attempt is refused loudly and logged.
#
# These exist precisely BECAUSE a future model or a future developer will one
# day try to call them. An unregistered capability fails with a confusing
# "unknown tool"; a registered TIER C one fails with a reason.
# ---------------------------------------------------------------------------

@tool("quote_price", TIER_C, "REFUSED — pricing is William's decision")
def quote_price(*args, **kwargs):
    raise PermissionDenied("pricing is never automated")


@tool("promise_delivery_date", TIER_C, "REFUSED — commitments are William's to make")
def promise_delivery_date(*args, **kwargs):
    raise PermissionDenied("delivery commitments are never automated")


@tool("record_payment", TIER_C, "REFUSED — money movement is never automated")
def record_payment(*args, **kwargs):
    raise PermissionDenied("money movement is never automated")


@tool("delete_client_data", TIER_C, "REFUSED — destructive and irreversible")
def delete_client_data(*args, **kwargs):
    raise PermissionDenied("the agent does not delete client data")

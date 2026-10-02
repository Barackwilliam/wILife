"""
The approval gate — Step 3 / Step 6.

Nothing reaches another human being without William saying yes. This module is
the only place where an approved TIER B draft becomes a sent message.

Flow:
    1. A job builds a draft via a TIER B tool
    2. request_approval() stores it and WhatsApps William a preview + a code
    3. William replies  OK <code>   or   NO <code>
    4. The webhook calls approve() / reject()
    5. approve() calls execute_approved(), which sends

Design note: the code is short because William types it on a phone. It is not a
security token — the webhook is authenticated by Meta's signature, and the
approval only ever transmits a message that was already drafted and shown. A
wrong code approves nothing; it simply does not match.
"""

import logging
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import ApprovalRequest
from core.agent.channels import DeliveryError, send_to_other, send_to_self

log = logging.getLogger("core.agent")

CODE_ALPHABET = string.digits


def _new_code(length=4):
    """Short numeric code, easy to type on a phone."""
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if not ApprovalRequest.objects.filter(code=code, status="pending").exists():
            return code
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length + 2))


def request_approval(user, tool_name, draft, summary=""):
    """
    Store a draft and ask William to approve it.

    Returns the ApprovalRequest. Nothing has been sent to the third party.
    """
    ttl_hours = getattr(settings, "AGENT_APPROVAL_TTL_HOURS", 24)
    approval = ApprovalRequest.objects.create(
        user=user,
        tool=tool_name,
        code=_new_code(),
        recipient_name=draft.get("recipient_name", "")[:120],
        recipient_number=draft.get("recipient_number", "")[:32],
        body=draft.get("body", ""),
        context=summary or draft.get("context", ""),
        status="pending",
        expires_at=timezone.now() + timedelta(hours=ttl_hours),
    )

    preview = (
        f"✍️ *Rasimu inasubiri idhini*\n\n"
        f"*Kwenda kwa:* {approval.recipient_name or approval.recipient_number}\n"
        f"{f'_{approval.context}_' if approval.context else ''}\n"
        f"────────────\n"
        f"{approval.body}\n"
        f"────────────\n\n"
        f"Jibu *OK {approval.code}* kutuma\n"
        f"Jibu *NO {approval.code}* kufuta\n"
        f"_Itaisha baada ya masaa {ttl_hours}_"
    )

    try:
        send_to_self(user, preview)
    except DeliveryError as exc:
        # The request stays pending — William can still approve it from the
        # admin. Losing the notification must not lose the draft.
        log.error("could not deliver approval preview #%s: %s", approval.pk, exc)
        approval.result = f"preview delivery failed: {exc}"
        approval.save(update_fields=["result"])

    return approval


def execute_approved(approval):
    """
    Transmit an approved draft. This is the ONLY function in the codebase that
    sends a message to someone who is not William.
    """
    if approval.status != "approved":
        raise ValueError(f"approval #{approval.pk} is {approval.status}, not approved")

    try:
        message_id = send_to_other(approval.recipient_number, approval.body)
    except DeliveryError as exc:
        approval.status = "failed"
        approval.result = str(exc)[:500]
        approval.save(update_fields=["status", "result"])
        log.error("approved send failed #%s: %s", approval.pk, exc)
        return False

    approval.status = "sent"
    approval.result = f"sent ({message_id})"
    approval.sent_at = timezone.now()
    approval.save(update_fields=["status", "result", "sent_at"])
    log.info("approved message #%s sent to %s", approval.pk, approval.recipient_number)
    return True


@transaction.atomic
@transaction.atomic
def approve(code, user=None):
    """
    Approve by code and send. Returns (ok, message) for the reply to William.

    Locked and re-checked inside the transaction so that two rapid replies
    cannot send the same message twice.
    """
    qs = ApprovalRequest.objects.select_for_update().filter(code=code, status="pending")
    if user is not None:
        qs = qs.filter(user=user)
    approval = qs.first()

    if approval is None:
        return False, f"Msimbo {code} haujapatikana au umeshatumika."

    if approval.expires_at and approval.expires_at < timezone.now():
        approval.status = "expired"
        approval.save(update_fields=["status"])
        return False, f"Rasimu {code} imeisha muda. Iandae upya."

    approval.status = "approved"
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_at"])

    ok = execute_approved(approval)
    if ok:
        return True, f"✅ Imetumwa kwa {approval.recipient_name or approval.recipient_number}."
    return False, f"⚠️ Kutuma kumeshindikana: {approval.result}"


@transaction.atomic
def reject(code, user=None):
    qs = ApprovalRequest.objects.select_for_update().filter(code=code, status="pending")
    if user is not None:
        qs = qs.filter(user=user)
    approval = qs.first()
    if approval is None:
        return False, f"Msimbo {code} haujapatikana."
    approval.status = "rejected"
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_at"])
    return True, f"🗑️ Rasimu {code} imefutwa."


def expire_stale(now=None):
    """Mark timed-out requests expired. Called by the tick."""
    now = now or timezone.now()
    return ApprovalRequest.objects.filter(
        status="pending", expires_at__lt=now
    ).update(status="expired")


def parse_reply(text):
    """
    Read an approval decision out of a WhatsApp reply.

    Accepts: 'OK 1234', 'ok1234', 'NDIYO 1234', 'NO 1234', 'HAPANA 1234'.
    Returns (decision, code) or (None, None).
    """
    if not text:
        return None, None
    cleaned = text.strip().upper().replace("*", "")
    compact = cleaned.replace(" ", "")

    for word in ("NDIYO", "SAWA", "OK"):
        if compact.startswith(word):
            digits = compact[len(word):]
            if digits.isdigit():
                return "approve", digits
    for word in ("HAPANA", "NO"):
        if compact.startswith(word):
            digits = compact[len(word):]
            if digits.isdigit():
                return "reject", digits
    return None, None

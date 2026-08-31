"""
WhatsApp delivery via the Meta Cloud API.

This is the agent's only channel to William. It is deliberately dumb: it sends
text, retries on transient failure, and reports success or failure. It makes no
decisions about what to send or whether sending is allowed.

Retry exists because Render free services are restarted at will and every deploy
is roughly a minute of absence — outbound calls will intermittently fail even
when nothing is wrong.
"""

import logging
import re
import time

import requests
from django.conf import settings

log = logging.getLogger("core.agent")

GRAPH_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"

# HTTP statuses worth trying again. 4xx (except 429) means we sent something
# wrong — retrying will not fix it.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class WhatsAppError(Exception):
    """Raised when a message could not be delivered after all retries."""


def normalise_number(raw):
    """
    Turn a human-entered number into the digits-only form Meta expects.

    '+255 712 345 678' -> '255712345678'
    '0712345678'       -> '255712345678'
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return ""
    cc = settings.AGENT_DEFAULT_COUNTRY_CODE
    if digits.startswith("0"):
        digits = cc + digits[1:]
    elif not digits.startswith(cc) and len(digits) <= 9:
        digits = cc + digits
    return digits


def send_whatsapp(to, text, retries=2, timeout=8):
    """
    Send a plain text WhatsApp message.

    Returns the Meta message id on success.
    Raises WhatsAppError if delivery failed after all attempts.

    Note: outside a 24-hour customer service window Meta only permits approved
    template messages. Since this sends to William's own number and he replies,
    plain text is fine in practice — but if messages stop arriving after a long
    silence, that window is the first thing to check.
    """
    if not settings.AGENT_ENABLED or not settings.WHATSAPP_ENABLED:
        log.info("whatsapp disabled — would have sent to %s: %s", to, text[:80])
        return "disabled"

    number = normalise_number(to)
    if not number:
        raise WhatsAppError("no recipient number")

    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppError("WHATSAPP_TOKEN or WHATSAPP_PHONE_NUMBER_ID not configured")

    url = GRAPH_URL.format(
        version=settings.WHATSAPP_API_VERSION,
        phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "text",
        "text": {"preview_url": False, "body": text[:4000]},
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)

            if response.status_code == 200:
                data = response.json()
                message_id = data.get("messages", [{}])[0].get("id", "sent")
                log.info("whatsapp sent to %s (id=%s)", number, message_id)
                return message_id

            body = response.text[:300]
            last_error = f"HTTP {response.status_code}: {body}"

            if response.status_code not in RETRYABLE_STATUS:
                # Permanent failure — a bad token, a bad number, a malformed body.
                # Retrying only wastes the tick's time budget.
                log.error("whatsapp permanent failure to %s — %s", number, last_error)
                raise WhatsAppError(last_error)

        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries:
            delay = 2 ** attempt
            log.warning(
                "whatsapp attempt %s/%s failed (%s) — retrying in %ss",
                attempt + 1, retries + 1, last_error, delay,
            )
            time.sleep(delay)

    raise WhatsAppError(last_error or "unknown failure")


def resolve_recipient(user):
    """
    Find the WhatsApp number for a user: their profile number, else the
    configured fallback. Returns '' if neither is set.
    """
    number = ""
    profile = getattr(user, "profile", None)
    if profile is not None:
        number = getattr(profile, "whatsapp_number", "") or ""
    return number or settings.AGENT_DEFAULT_RECIPIENT or ""

"""
Email delivery via Resend.

Why an HTTP API and not SMTP: Render's free tier blocks outbound ports 25, 465
and 587, so Django's SMTP backend cannot send there at all. Resend goes over
HTTPS on 443, which is not blocked.

Why email at all: Telegram needs a VPN in Tanzania, and WhatsApp refuses
business-initiated messages outside its 24-hour window without approved
templates. Email has neither problem — it works today, everywhere, for free.

The agent's messages are written in WhatsApp-style markup (*bold*, _italic_).
This module converts that to simple HTML so the same message text works on
every channel without any job knowing which one is in use.
"""

import html
import logging
import re
import time

import requests
from django.conf import settings

log = logging.getLogger("core.agent")

API_URL = "https://api.resend.com/emails"
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class EmailError(Exception):
    """A message could not be delivered after all retries."""


def enabled():
    return bool(getattr(settings, "RESEND_API_KEY", "")) and getattr(
        settings, "EMAIL_CHANNEL_ENABLED", False
    )


def _subject_from(text):
    """
    Use the first meaningful line as the subject.

    The agent's messages already start with a headline like '⏰ *Kumbusho*', so
    this gives a scannable inbox without every job having to supply a subject.
    """
    for line in text.splitlines():
        cleaned = line.replace("*", "").replace("_", " ").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned:
            return cleaned[:120]
    return "wILife"


def to_html(text):
    """Convert WhatsApp-style markup to minimal, readable HTML."""
    escaped = html.escape(text)
    escaped = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"_([^_\n]+)_", r"<em>\1</em>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.6;color:#1a1a1a;max-width:620px;'
        'padding:20px;background:#ffffff;">'
        f"{escaped}"
        '<hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0 12px;">'
        '<div style="font-size:12px;color:#8a8a8a;">wILife</div>'
        "</div>"
    )


def send_email(to, text, subject=None, retries=2, timeout=10):
    """
    Send one message. Returns the Resend message id.
    Raises EmailError if delivery failed after all attempts.
    """
    if not settings.AGENT_ENABLED:
        log.info("agent disabled — would have emailed %s: %s", to, text[:80])
        return "disabled"

    if not enabled():
        raise EmailError("RESEND_API_KEY not configured or EMAIL_CHANNEL_ENABLED is false")

    if not to:
        raise EmailError("no recipient address")

    sender = getattr(settings, "AGENT_EMAIL_FROM", "")
    if not sender:
        raise EmailError("AGENT_EMAIL_FROM is not configured")

    payload = {
        "from": sender,
        "to": [to],
        "subject": subject or _subject_from(text),
        "html": to_html(text),
        "text": text,
    }
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(API_URL, json=payload, headers=headers, timeout=timeout)

            if response.status_code in (200, 201):
                message_id = response.json().get("id", "sent")
                log.info("email sent to %s (id=%s)", to, message_id)
                return message_id

            body = response.text[:300]
            last_error = f"HTTP {response.status_code}: {body}"

            if response.status_code not in RETRYABLE_STATUS:
                log.error("email permanent failure to %s — %s", to, last_error)
                raise EmailError(last_error)

        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries:
            delay = 2 ** attempt
            log.warning("email attempt %s failed (%s) — retrying in %ss",
                        attempt + 1, last_error, delay)
            time.sleep(delay)

    raise EmailError(last_error or "unknown failure")

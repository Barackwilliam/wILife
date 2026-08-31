"""
Telegram delivery.

Why this exists: Meta closes a 24-hour window after your last inbound message,
and outside it only pre-approved templates may be sent. Every useful thing this
agent does is proactive — a 5am reminder, a 6am brief, a draft awaiting your
approval — so on WhatsApp all of it fails with error 131047 unless templated.

Telegram has no such window. For an agent whose only audience is its owner,
this is the correct channel. WhatsApp stays for messages to actual clients.

Formatting note: Telegram's legacy Markdown uses *bold* and _italic_, the same
syntax as WhatsApp. The existing message templates therefore need no changes.
"""

import logging
import time

import requests
from django.conf import settings

log = logging.getLogger("core.agent")

API_URL = "https://api.telegram.org/bot{token}/{method}"
RETRYABLE_STATUS = {420, 429, 500, 502, 503, 504}


class TelegramError(Exception):
    """A message could not be delivered after all retries."""


def enabled():
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", "")) and getattr(
        settings, "TELEGRAM_ENABLED", False
    )


def _post(method, payload, timeout=8):
    url = API_URL.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)
    return requests.post(url, json=payload, timeout=timeout)


def send_telegram(chat_id, text, retries=2, timeout=8):
    """
    Send a message. Returns the message id.

    Falls back to plain text if Markdown parsing fails — an unbalanced asterisk
    in a client name should degrade the formatting, not lose the message.
    """
    if not settings.AGENT_ENABLED:
        log.info("agent disabled — would have sent to %s: %s", chat_id, text[:80])
        return "disabled"

    if not enabled():
        raise TelegramError("TELEGRAM_BOT_TOKEN not configured or TELEGRAM_ENABLED is false")

    if not chat_id:
        raise TelegramError("no chat_id")

    payload = {
        "chat_id": str(chat_id),
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    last_error = None
    for attempt in range(retries + 1):
        try:
            response = _post("sendMessage", payload, timeout=timeout)

            if response.status_code == 200:
                data = response.json()
                message_id = data.get("result", {}).get("message_id", "sent")
                log.info("telegram sent to %s (id=%s)", chat_id, message_id)
                return message_id

            body = response.text[:300]
            last_error = f"HTTP {response.status_code}: {body}"

            # Bad Markdown — resend once as plain text rather than losing it.
            if response.status_code == 400 and "parse" in body.lower() and "parse_mode" in payload:
                log.warning("telegram markdown rejected — resending as plain text")
                payload.pop("parse_mode")
                continue

            if response.status_code not in RETRYABLE_STATUS:
                log.error("telegram permanent failure to %s — %s", chat_id, last_error)
                raise TelegramError(last_error)

        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries:
            delay = 2 ** attempt
            log.warning("telegram attempt %s failed (%s) — retrying in %ss",
                        attempt + 1, last_error, delay)
            time.sleep(delay)

    raise TelegramError(last_error or "unknown failure")


def get_updates(limit=20):
    """
    Read recent updates. Used only by the setup command to discover your chat id.
    Not used at runtime — the webhook delivers messages instead.
    """
    if not getattr(settings, "TELEGRAM_BOT_TOKEN", ""):
        raise TelegramError("TELEGRAM_BOT_TOKEN not set")
    response = _post("getUpdates", {"limit": limit}, timeout=10)
    if response.status_code != 200:
        raise TelegramError(f"HTTP {response.status_code}: {response.text[:200]}")
    return response.json().get("result", [])


def set_webhook(url, secret):
    """Register the webhook with Telegram. Called by the setup command."""
    response = _post("setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message"],
    }, timeout=10)
    if response.status_code != 200:
        raise TelegramError(f"HTTP {response.status_code}: {response.text[:200]}")
    return response.json()

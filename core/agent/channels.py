"""
Channel routing.

One rule, and it maps exactly onto the permission tiers:

    send_to_self()   messages William. Telegram if configured, else WhatsApp.
    send_to_other()  messages a third party. Always WhatsApp, never Telegram —
                     your clients are on WhatsApp, not on your bot.

Every job calls send_to_self(). Only approvals.execute_approved() calls
send_to_other(). Keeping the two apart at the channel layer means a coding
mistake in a job cannot accidentally reach a client: the wrong function simply
does not have their number.
"""

import logging

from django.conf import settings

from core.agent import telegram
from core.agent.whatsapp import WhatsAppError, resolve_recipient, send_whatsapp

log = logging.getLogger("core.agent")


class DeliveryError(Exception):
    """The message could not be delivered on any configured channel."""


def self_channel():
    """Which channel messages to William will use. Useful in logs and setup checks."""
    if telegram.enabled() and getattr(settings, "AGENT_TELEGRAM_CHAT_ID", ""):
        return "telegram"
    return "whatsapp"


def send_to_self(user, text):
    """
    Deliver a message to William.

    Telegram is preferred because it has no 24-hour window — a 6am brief sent
    by a machine is exactly the case WhatsApp refuses without a template.
    """
    if self_channel() == "telegram":
        try:
            return telegram.send_telegram(settings.AGENT_TELEGRAM_CHAT_ID, text)
        except telegram.TelegramError as exc:
            log.error("telegram delivery failed: %s", exc)
            # Only worth falling back if WhatsApp is actually usable.
            if not getattr(settings, "WHATSAPP_ENABLED", False):
                raise DeliveryError(str(exc)) from exc
            log.warning("falling back to WhatsApp")

    number = resolve_recipient(user)
    if not number:
        raise DeliveryError("no WhatsApp number configured and Telegram unavailable")

    try:
        return send_whatsapp(number, text)
    except WhatsAppError as exc:
        raise DeliveryError(str(exc)) from exc


def send_to_other(number, text):
    """
    Deliver a message to someone who is not William. WhatsApp only.

    This is called from exactly one place: approvals.execute_approved().
    """
    if not number:
        raise DeliveryError("no recipient number")
    try:
        return send_whatsapp(number, text)
    except WhatsAppError as exc:
        raise DeliveryError(str(exc)) from exc

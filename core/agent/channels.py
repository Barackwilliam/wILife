"""
Channel routing.

One rule, and it maps onto the permission tiers:

    send_to_self()   messages William
    send_to_other()  messages a third party — WhatsApp only, never anything else

Every job calls send_to_self(). Only approvals.execute_approved() calls
send_to_other(). Keeping them apart at the channel layer means a coding mistake
in a job cannot reach a client: the wrong function does not have their number.

Channel preference for send_to_self, set by AGENT_SELF_CHANNEL:

    email      Resend over HTTPS. Works in Tanzania without a VPN, works on the
               free tier where SMTP ports are blocked, no 24-hour window, no
               template approval. This is the default.
    telegram   No window, good formatting — but blocked without a VPN in TZ.
    whatsapp   Only usable inside Meta's 24-hour window unless you have
               approved templates.

If the preferred channel fails, it falls through to the next configured one
rather than losing the message. A reminder that arrives by the second-choice
channel is still a reminder; one that vanishes is not.
"""

import logging

from django.conf import settings

from core.agent import email_channel, telegram
from core.agent.whatsapp import WhatsAppError, resolve_recipient, send_whatsapp

log = logging.getLogger("core.agent")


class DeliveryError(Exception):
    """The message could not be delivered on any configured channel."""


def _telegram_ready():
    return telegram.enabled() and bool(getattr(settings, "AGENT_TELEGRAM_CHAT_ID", ""))


def _email_ready():
    return email_channel.enabled() and bool(getattr(settings, "AGENT_EMAIL_TO", ""))


def _whatsapp_ready(user):
    return bool(getattr(settings, "WHATSAPP_ENABLED", False)) and bool(resolve_recipient(user))


def self_channel(user=None):
    """Which channel a message to William will actually use, right now."""
    preferred = getattr(settings, "AGENT_SELF_CHANNEL", "email").strip().lower()

    ready = {
        "email": _email_ready(),
        "telegram": _telegram_ready(),
        "whatsapp": _whatsapp_ready(user) if user is not None else bool(
            getattr(settings, "WHATSAPP_ENABLED", False)),
    }

    if ready.get(preferred):
        return preferred
    for name in ("email", "telegram", "whatsapp"):
        if ready.get(name):
            return name
    return "none"


def _send_on(channel, user, text):
    if channel == "email":
        return email_channel.send_email(settings.AGENT_EMAIL_TO, text)
    if channel == "telegram":
        return telegram.send_telegram(settings.AGENT_TELEGRAM_CHAT_ID, text)
    if channel == "whatsapp":
        number = resolve_recipient(user)
        if not number:
            raise WhatsAppError("no WhatsApp number configured")
        return send_whatsapp(number, text)
    raise DeliveryError(f"unknown channel: {channel}")


def send_to_self(user, text):
    """
    Deliver a message to William, trying the preferred channel first and
    falling through to any other configured one on failure.
    """
    preferred = self_channel(user)
    if preferred == "none":
        raise DeliveryError(
            "no self channel configured — set AGENT_EMAIL_TO + RESEND_API_KEY, "
            "or AGENT_TELEGRAM_CHAT_ID, or a WhatsApp number"
        )

    order = [preferred] + [c for c in ("email", "telegram", "whatsapp") if c != preferred]
    errors = []

    for channel in order:
        ready = {
            "email": _email_ready(),
            "telegram": _telegram_ready(),
            "whatsapp": _whatsapp_ready(user),
        }[channel]
        if not ready:
            continue
        try:
            result = _send_on(channel, user, text)
            if channel != preferred:
                log.warning("delivered via fallback channel %s", channel)
            return result
        except Exception as exc:
            errors.append(f"{channel}: {exc}")
            log.error("delivery failed on %s: %s", channel, exc)

    raise DeliveryError("; ".join(errors) or "no channel available")


def send_to_other(number, text):
    """
    Deliver a message to someone who is not William. WhatsApp only.

    Called from exactly one place: approvals.execute_approved().
    Never routed to email or Telegram — your clients are on WhatsApp.
    """
    if not number:
        raise DeliveryError("no recipient number")
    try:
        return send_whatsapp(number, text)
    except WhatsAppError as exc:
        raise DeliveryError(str(exc)) from exc

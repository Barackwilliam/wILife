"""
Inbound WhatsApp webhook.

This is how William approves a draft from his phone: he replies OK 1234 and the
message goes out. Nothing else in this file does anything — an unrecognised
message is acknowledged and ignored, on purpose. This endpoint is public, and a
public endpoint that acts on arbitrary text is a liability.

Two entry points:

    whatsapp_webhook   Meta Cloud API. Register the URL in the Meta app
                       dashboard, with WHATSAPP_VERIFY_TOKEN as the verify token.
    baileys_incoming   The Baileys bridge (whatsapp_bridge/) forwards each text
                       message here, authenticated by WHATSAPP_BRIDGE_KEY. The
                       reply goes back in the response body and the bridge sends
                       it, so it reaches the chat even when WhatsApp hides the
                       sender's number behind a LID.
    whatsapp_qr        Staff-only page showing the bridge's QR code, for when
                       the bridge runs inside this service and is not public.
"""

import hashlib
import hmac
import json
import logging

import requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.agent import approvals, jamiitek_team
from core.agent.whatsapp import normalise_number, send_whatsapp

log = logging.getLogger("core.agent")


def _signature_ok(request):
    """
    Verify Meta's X-Hub-Signature-256 header against the app secret.

    If WHATSAPP_APP_SECRET is unset we refuse rather than accept. An unverified
    public webhook that can trigger sends is not something to leave open with a
    warning in the logs.
    """
    secret = getattr(settings, "WHATSAPP_APP_SECRET", "")
    if not secret:
        log.error("WHATSAPP_APP_SECRET not configured — rejecting webhook")
        return False

    header = request.headers.get("X-Hub-Signature-256", "")
    if not header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def _authorised_user(from_number):
    """
    Only William may approve. Match the sender against profile whatsapp_number
    or the configured default recipient.
    """
    from django.contrib.auth.models import User

    sender = normalise_number(from_number)
    if not sender:
        return None

    for user in User.objects.filter(is_active=True).select_related("profile"):
        profile = getattr(user, "profile", None)
        number = normalise_number(getattr(profile, "whatsapp_number", "") or "")
        if number and number == sender:
            return user

    default = normalise_number(getattr(settings, "AGENT_DEFAULT_RECIPIENT", ""))
    if default and default == sender:
        return User.objects.filter(is_active=True).order_by("pk").first()
    return None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    # --- Meta's one-time verification handshake ---------------------------
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge", "")
        if mode == "subscribe" and token == getattr(settings, "WHATSAPP_VERIFY_TOKEN", ""):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("forbidden", status=403)

    # --- inbound messages --------------------------------------------------
    if not _signature_ok(request):
        return JsonResponse({"detail": "bad signature"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "bad payload"}, status=400)

    # Always return 200 after this point. Meta retries aggressively on errors,
    # and a retry storm on a free Render instance is its own outage.
    try:
        _handle_payload(payload)
    except Exception:
        log.exception("webhook handling failed")

    return JsonResponse({"status": "ok"})


def _handle_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                if message.get("type") != "text":
                    continue
                from_number = message.get("from", "")
                text = message.get("text", {}).get("body", "")
                reply = _handle_message(from_number, text)
                if reply:
                    try:
                        send_whatsapp(from_number, reply)
                    except Exception as exc:
                        log.error("could not confirm decision to %s: %s", from_number, exc)


@csrf_exempt
@require_http_methods(["POST"])
def baileys_incoming(request):
    key = getattr(settings, "WHATSAPP_BRIDGE_KEY", "")
    if not key:
        log.error("WHATSAPP_BRIDGE_KEY not configured — rejecting bridge message")
        return JsonResponse({"detail": "not configured"}, status=503)

    provided = request.headers.get("X-Bridge-Key", "")
    if not hmac.compare_digest(str(provided), str(key)):
        return JsonResponse({"detail": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "bad payload"}, status=400)

    reply = ""
    try:
        reply = _handle_message(str(payload.get("phone", "")), str(payload.get("message", "")))
    except Exception:
        log.exception("bridge message handling failed")

    return JsonResponse({"reply": reply or ""})


@staff_member_required
def whatsapp_qr(request):
    """Show the bridge's QR page without exposing the bridge or its key."""
    base = getattr(settings, "WHATSAPP_BRIDGE_URL", "")
    key = getattr(settings, "WHATSAPP_BRIDGE_KEY", "")
    if not base or not key:
        return HttpResponse("WhatsApp bridge is not configured "
                            "(WHATSAPP_BRIDGE_URL / WHATSAPP_BRIDGE_KEY).", status=503)
    try:
        response = requests.get(base.rstrip("/") + "/qr", params={"key": key}, timeout=10)
    except requests.RequestException as exc:
        return HttpResponse(f"WhatsApp bridge unreachable: {exc}", status=502)
    page = HttpResponse(response.content, status=response.status_code,
                        content_type=response.headers.get("Content-Type", "text/html"))
    page["Cache-Control"] = "no-store"
    return page


def _handle_message(from_number, text):
    """Act on an approval reply. Returns the confirmation text, or '' to stay silent."""
    decision, code = approvals.parse_reply(text)
    if not decision:
        if jamiitek_team.is_team_command(text) and _authorised_user(from_number):
            return jamiitek_team.status_text()
        log.info("ignoring unrecognised inbound message from %s", from_number)
        return ""

    user = _authorised_user(from_number)
    if user is None:
        log.warning("approval attempt from unauthorised number %s", from_number)
        return ""

    if decision == "approve":
        ok, reply = approvals.approve(code, user=user)
    else:
        ok, reply = approvals.reject(code, user=user)

    log.info("approval decision=%s code=%s ok=%s", decision, code, ok)
    return reply

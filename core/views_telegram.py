"""
Inbound Telegram webhook.

Same job as the WhatsApp one: receive 'OK 1234' and let the approval through.
Unlike WhatsApp there is no 24-hour window to worry about, and the endpoint is
authenticated by a secret header Telegram sends on every request.

Like the WhatsApp webhook, an unrecognised message is acknowledged and ignored.
A public endpoint that acts on arbitrary text is a liability.
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.agent import approvals, jamiitek_team
from core.agent.telegram import send_telegram

log = logging.getLogger("core.agent")


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request):
    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        log.error("TELEGRAM_WEBHOOK_SECRET not configured — rejecting webhook")
        return JsonResponse({"detail": "not configured"}, status=503)

    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(str(provided), str(secret)):
        return JsonResponse({"detail": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "bad payload"}, status=400)

    # Always 200 after this point. Telegram retries on errors, and a retry
    # storm against a free Render instance is its own outage.
    try:
        _handle(payload)
    except Exception:
        log.exception("telegram webhook handling failed")

    return JsonResponse({"ok": True})


def _handle(payload):
    message = payload.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = message.get("text", "")

    if chat_id and (message.get("photo") or _image_document(message)):
        expected = str(getattr(settings, "AGENT_TELEGRAM_CHAT_ID", ""))
        if expected and chat_id == expected:
            _start_promo(chat_id, message)
        else:
            log.warning("ignoring telegram photo from unknown chat %s", chat_id)
        return

    if not chat_id or not text:
        return

    # Only the configured owner chat may approve anything.
    expected = str(getattr(settings, "AGENT_TELEGRAM_CHAT_ID", ""))
    if not expected or chat_id != expected:
        log.warning("ignoring telegram message from unknown chat %s", chat_id)
        return

    decision, code = approvals.parse_reply(text)
    if not decision:
        from news import commission
        from django.contrib.auth.models import User
        owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        reply = commission.from_chat(text, owner, "telegram")
        if reply:
            try:
                send_telegram(chat_id, reply)
            except Exception as exc:
                log.error("could not confirm news command: %s", exc)
            return
        if jamiitek_team.is_team_command(text):
            try:
                send_telegram(chat_id, jamiitek_team.status_text())
            except Exception as exc:
                log.error("could not send team status: %s", exc)
            return
        log.info("ignoring unrecognised telegram message")
        return

    from django.contrib.auth.models import User
    user = User.objects.filter(is_active=True).order_by("pk").first()

    if decision == "approve":
        ok, reply = approvals.approve(code, user=user)
    else:
        ok, reply = approvals.reject(code, user=user)

    log.info("telegram approval decision=%s code=%s ok=%s", decision, code, ok)
    try:
        send_telegram(chat_id, reply)
    except Exception as exc:
        log.error("could not confirm telegram decision: %s", exc)


def _image_document(message):
    """Picha iliyotumwa kama 'faili' (ubora kamili, haibanwi na Telegram)."""
    doc = message.get("document") or {}
    return doc if str(doc.get("mime_type", "")).startswith("image/") else None


def _start_promo(chat_id, message):
    """Picha + caption (Instagram/Facebook) kutoka kwa mhariri -> rasimu ya habari (kwenye thread)."""
    import threading

    from django.db import connection

    doc = _image_document(message)
    file_id = doc["file_id"] if doc else message["photo"][-1]["file_id"]   # [-1] = ukubwa mkubwa zaidi
    caption = message.get("caption", "")
    try:
        send_telegram(chat_id, "📸 Nimepokea chapisho — ninaandaa rasimu ya habari…")
    except Exception as exc:
        log.error("could not ack promo: %s", exc)

    def work():
        from django.contrib.auth.models import User

        from core.agent.telegram import download_file
        from news import promo
        from news.commission import _approval, _site
        try:
            image, name = download_file(file_id)
            text, link, account = promo.parse_caption(caption)
            article, note = promo.create_draft(image, name, text, link=link, account=account)
            owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
            approval = _approval(article, owner)
            reply = (f"📝 Rasimu ya habari iko tayari:\n*{article.title}*\n"
                     f"Hariri: {_site(f'/admin/news/article/{article.pk}/change/')}\n"
                     f"Soma: {_site(article.get_absolute_url())}")
            if approval:
                reply += f"\nJibu *OK {approval.code}* kuichapisha bila kuhariri."
            if note:
                reply += f"\n⚠️ {note}"
        except Exception as exc:
            log.exception("telegram promo failed")
            reply = f"⚠️ Habari haikuweza kuandaliwa: {str(exc)[:200]}"
        finally:
            connection.close()
        try:
            send_telegram(chat_id, reply)
        except Exception as exc:
            log.error("could not send promo result: %s", exc)

    threading.Thread(target=work, name="telegram-promo", daemon=True).start()

"""
Check every message channel — email, Telegram, WhatsApp — in one place.

    python manage.py channels_check          # show what is configured
    python manage.py channels_check --send   # send a real test on each channel

Each channel is tested on its own, so one broken channel cannot hide another.
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.agent import email_channel, telegram, whatsapp
from core.agent.channels import ready_channels, self_channel

SAMPLE = "✅ *wILife*\n\nUjumbe wa majaribio — channel hii inafanya kazi."


class Command(BaseCommand):
    help = "Show the status of email, Telegram and WhatsApp, and optionally send a test on each."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Send a real test message on each channel")

    def handle(self, *args, **options):
        user = User.objects.filter(is_active=True).order_by("pk").first()
        number = whatsapp.resolve_recipient(user) if user else settings.AGENT_DEFAULT_RECIPIENT

        rows = [
            ("email", self._email_missing(), lambda: email_channel.send_email(settings.AGENT_EMAIL_TO, SAMPLE)),
            ("telegram", self._telegram_missing(),
             lambda: telegram.send_telegram(settings.AGENT_TELEGRAM_CHAT_ID, SAMPLE)),
            ("whatsapp", self._whatsapp_missing(number), lambda: whatsapp.send_whatsapp(number, SAMPLE)),
        ]

        self.stdout.write(f"AGENT_SELF_CHANNEL = {getattr(settings, 'AGENT_SELF_CHANNEL', 'email')}"
                          f"  ->  in use: {self_channel(user)}  (ready: {', '.join(ready_channels(user)) or 'none'})\n")

        if whatsapp.provider() == "baileys" and settings.WHATSAPP_BRIDGE_URL:
            try:
                status = whatsapp.bridge_status()
                self.stdout.write(f"WhatsApp bridge: {status.get('status')}")
                if not status.get("connected"):
                    where = ("/agent/whatsapp/qr/ on this site (staff login)"
                             if "127.0.0.1" in settings.WHATSAPP_BRIDGE_URL or "localhost" in settings.WHATSAPP_BRIDGE_URL
                             else f"{settings.WHATSAPP_BRIDGE_URL.rstrip('/')}/qr?key=<WHATSAPP_BRIDGE_KEY>")
                    self.stdout.write(self.style.WARNING(f"  not linked — open {where} and scan the QR"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"WhatsApp bridge unreachable: {exc}"))
            self.stdout.write("")

        failures = 0
        for name, missing, send in rows:
            if missing:
                self.stdout.write(self.style.WARNING(f"  {name:9} not configured — missing: {', '.join(missing)}"))
                continue
            if not options["send"]:
                self.stdout.write(self.style.SUCCESS(f"  {name:9} configured"))
                continue
            try:
                result = send()
                self.stdout.write(self.style.SUCCESS(f"  {name:9} sent (id={result})"))
            except Exception as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"  {name:9} FAILED — {exc}"))

        if not options["send"]:
            self.stdout.write("\nRun with --send to deliver a real test message on each configured channel.")
        elif failures:
            self.stdout.write(self.style.ERROR(f"\n{failures} channel(s) failed."))

    def _email_missing(self):
        return [n for n, ok in (
            ("EMAIL_CHANNEL_ENABLED=true", getattr(settings, "EMAIL_CHANNEL_ENABLED", False)),
            ("RESEND_API_KEY", getattr(settings, "RESEND_API_KEY", "")),
            ("AGENT_EMAIL_FROM", getattr(settings, "AGENT_EMAIL_FROM", "")),
            ("AGENT_EMAIL_TO", getattr(settings, "AGENT_EMAIL_TO", "")),
        ) if not ok]

    def _telegram_missing(self):
        return [n for n, ok in (
            ("TELEGRAM_ENABLED=true", getattr(settings, "TELEGRAM_ENABLED", False)),
            ("TELEGRAM_BOT_TOKEN", getattr(settings, "TELEGRAM_BOT_TOKEN", "")),
            ("AGENT_TELEGRAM_CHAT_ID", getattr(settings, "AGENT_TELEGRAM_CHAT_ID", "")),
        ) if not ok]

    def _whatsapp_missing(self, number):
        missing = [] if settings.WHATSAPP_ENABLED else ["WHATSAPP_ENABLED=true"]
        if whatsapp.provider() == "baileys":
            missing += [n for n in ("WHATSAPP_BRIDGE_URL", "WHATSAPP_BRIDGE_KEY") if not getattr(settings, n, "")]
        else:
            missing += [n for n in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID") if not getattr(settings, n, "")]
        if not number:
            missing.append("AGENT_DEFAULT_RECIPIENT (or a WhatsApp number on your profile)")
        return missing

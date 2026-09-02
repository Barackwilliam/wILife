"""
Verify the email channel.

    python manage.py email_setup            # show configuration and active channel
    python manage.py email_setup --test     # send a real test message
    python manage.py email_setup --preview  # print the HTML without sending
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from core.agent import email_channel
from core.agent.channels import self_channel

SAMPLE = """⏰ *Kumbusho*

*Kikao na mteja*
🕐 14:30 — 02/09/2026
📍 Mikocheni, Dar es Salaam

_Huu ni ujumbe wa majaribio kutoka wILife._"""


class Command(BaseCommand):
    help = "Check the email channel configuration and optionally send a test message."

    def add_arguments(self, parser):
        parser.add_argument("--test", action="store_true", help="Send a real test email")
        parser.add_argument("--preview", action="store_true", help="Print the HTML without sending")

    def handle(self, *args, **options):
        key = getattr(settings, "RESEND_API_KEY", "")
        sender = getattr(settings, "AGENT_EMAIL_FROM", "")
        to = getattr(settings, "AGENT_EMAIL_TO", "")
        on = getattr(settings, "EMAIL_CHANNEL_ENABLED", False)

        self.stdout.write("Email channel configuration:\n")
        self.stdout.write(f"  EMAIL_CHANNEL_ENABLED : {on}")
        self.stdout.write(f"  RESEND_API_KEY        : {'set (' + key[:8] + '…)' if key else 'MISSING'}")
        self.stdout.write(f"  AGENT_EMAIL_FROM      : {sender or 'MISSING'}")
        self.stdout.write(f"  AGENT_EMAIL_TO        : {to or 'MISSING'}")
        self.stdout.write(f"\n  preferred channel     : {getattr(settings, 'AGENT_SELF_CHANNEL', 'email')}")
        self.stdout.write(f"  channel actually used : {self_channel()}")

        if options.get("preview"):
            self.stdout.write("\n--- HTML ---\n")
            self.stdout.write(email_channel.to_html(SAMPLE))
            return

        if not options.get("test"):
            missing = [n for n, v in (
                ("RESEND_API_KEY", key), ("AGENT_EMAIL_FROM", sender), ("AGENT_EMAIL_TO", to)
            ) if not v]
            if missing:
                self.stdout.write(self.style.ERROR(f"\nMissing: {', '.join(missing)}"))
            elif not on:
                self.stdout.write(self.style.WARNING("\nSet EMAIL_CHANNEL_ENABLED=true to activate."))
            else:
                self.stdout.write(self.style.SUCCESS("\nConfigured. Run with --test to send."))
            return

        try:
            message_id = email_channel.send_email(to, SAMPLE)
            self.stdout.write(self.style.SUCCESS(f"\nSent (id={message_id}). Check your inbox."))
            self.stdout.write("If it is not there in a minute, check spam — and check that the "
                              "domain in AGENT_EMAIL_FROM is verified in Resend.")
        except email_channel.EmailError as exc:
            self.stdout.write(self.style.ERROR(f"\nFailed: {exc}"))
            self.stdout.write(
                "\nCommon causes:\n"
                "  · AGENT_EMAIL_FROM uses a domain not verified in Resend\n"
                "  · API key is for a different Resend account\n"
                "  · On the free tier without a verified domain, you can only send\n"
                "    to the address you signed up with"
            )

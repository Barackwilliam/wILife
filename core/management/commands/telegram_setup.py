"""
Set up Telegram in two steps.

    python manage.py telegram_setup                      # find your chat id
    python manage.py telegram_setup --webhook https://your-app.onrender.com

Run the first form after messaging your bot. It prints the chat id to put in
AGENT_TELEGRAM_CHAT_ID. Run the second form once deployed, to register the
webhook so approval replies reach the agent.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from core.agent.telegram import TelegramError, get_updates, send_telegram, set_webhook


class Command(BaseCommand):
    help = "Discover your Telegram chat id, send a test message, or register the webhook."

    def add_arguments(self, parser):
        parser.add_argument("--webhook", help="Base URL of the deployed app, e.g. https://app.onrender.com")
        parser.add_argument("--test", action="store_true", help="Send a test message to the configured chat")

    def handle(self, *args, **options):
        if not getattr(settings, "TELEGRAM_BOT_TOKEN", ""):
            self.stdout.write(self.style.ERROR("TELEGRAM_BOT_TOKEN is not set."))
            self.stdout.write("Create a bot with @BotFather on Telegram, then put the token in .env")
            return

        if options.get("webhook"):
            return self._register(options["webhook"])

        if options.get("test"):
            return self._test()

        return self._discover()

    def _discover(self):
        try:
            updates = get_updates()
        except TelegramError as exc:
            self.stdout.write(self.style.ERROR(f"Could not reach Telegram: {exc}"))
            return

        if not updates:
            self.stdout.write(self.style.WARNING("No messages found."))
            self.stdout.write(
                "Open Telegram, find your bot, and send it any message — then run "
                "this again.\n"
                "Note: if a webhook is already registered, getUpdates returns "
                "nothing. Delete the webhook first or read the chat id from your "
                "server logs."
            )
            return

        self.stdout.write(self.style.SUCCESS("Found:\n"))
        seen = set()
        for update in updates:
            chat = (update.get("message") or {}).get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None or chat_id in seen:
                continue
            seen.add(chat_id)
            name = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            self.stdout.write(f"  {name:<24} chat_id = {chat_id}")

        self.stdout.write("\nPut it in .env:\n  AGENT_TELEGRAM_CHAT_ID=<the id above>")

    def _test(self):
        chat_id = getattr(settings, "AGENT_TELEGRAM_CHAT_ID", "")
        if not chat_id:
            self.stdout.write(self.style.ERROR("AGENT_TELEGRAM_CHAT_ID is not set."))
            return
        try:
            send_telegram(chat_id, "✅ *wILife*\n\nMawasiliano yanafanya kazi.")
            self.stdout.write(self.style.SUCCESS("Sent. Check Telegram."))
        except TelegramError as exc:
            self.stdout.write(self.style.ERROR(f"Failed: {exc}"))

    def _register(self, base_url):
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if not secret:
            self.stdout.write(self.style.ERROR("TELEGRAM_WEBHOOK_SECRET is not set."))
            self.stdout.write('Generate one:  python -c "import secrets; print(secrets.token_urlsafe(32))"')
            return

        url = base_url.rstrip("/") + "/agent/telegram/"
        try:
            result = set_webhook(url, secret)
        except TelegramError as exc:
            self.stdout.write(self.style.ERROR(f"Failed: {exc}"))
            return

        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"Webhook registered: {url}"))
            self.stdout.write("Approval replies (OK 1234 / NO 1234) will now reach the agent.")
        else:
            self.stdout.write(self.style.ERROR(f"Telegram refused: {result}"))

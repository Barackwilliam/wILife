"""
Verify the hosting/domain expiry query before enabling the watch.

Read-only. Sends nothing.

    python manage.py check_expiry
"""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from core.agent.expiry import fetch_expiring
from core.agent.jamiitek import JamiiTekUnavailable


class Command(BaseCommand):
    help = "Test the hosting and domain expiry query and show what it finds."

    def handle(self, *args, **options):
        if not getattr(settings, "JAMIITEK_DB_DSN", ""):
            self.stdout.write(self.style.ERROR("JAMIITEK_DB_DSN is not set."))
            return

        try:
            rows = fetch_expiring()
        except JamiiTekUnavailable as exc:
            self.stdout.write(self.style.ERROR(f"Failed: {exc}"))
            self.stdout.write(
                "\nThe query must return: kind, item, client_name, client_number, "
                "expires_on, days_left, amount, will_auto_suspend.\n"
                "Check your table prefix — Django names tables <app_label>_<model>, "
                "so app/models.py gives app_managedwebsite and app_domainrecord.\n"
                "Set JAMIITEK_EXPIRY_SQL if yours differ."
            )
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS(
                "Connected. Nothing expiring inside the warning windows."))
            return

        expired = [r for r in rows if (r.get("days_left") or 0) < 0]
        self.stdout.write(self.style.SUCCESS(f"Connected. {len(rows)} item(s):\n"))

        for row in sorted(rows, key=lambda r: r.get("days_left") or 0):
            days = int(row.get("days_left") or 0)
            when = f"{abs(days)}d ago" if days < 0 else f"in {days}d"
            amount = row.get("amount")
            money = f"TZS {Decimal(str(amount)):,.0f}" if amount else ""
            flag = "  [AUTO-SUSPEND]" if row.get("will_auto_suspend") and days < 0 else ""
            style = self.style.ERROR if days < 0 else self.style.WARNING if days <= 3 else str
            line = (f"  {str(row.get('kind')):<8} {str(row.get('item'))[:30]:<32} "
                    f"{str(row.get('client_name'))[:20]:<22} {when:<12} {money}{flag}")
            self.stdout.write(style(line) if callable(style) else line)

        if expired:
            self.stdout.write(self.style.ERROR(
                f"\n{len(expired)} item(s) already expired. "
                "An expired .co.tz is not always recoverable — handle these first."))

        self.stdout.write(
            "\nIf this matches your records, set AGENT_EXPIRY_WATCH_ENABLED=true."
        )

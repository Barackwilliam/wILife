"""
Verify the JamiiTek connector before anything depends on it.

Read-only. Prints what it finds and sends nothing. Run this until the columns
look right, then enable the invoice watch.

    python manage.py check_jamiitek
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from core.agent.jamiitek import JamiiTekUnavailable, fetch_overdue_invoices, is_configured


class Command(BaseCommand):
    help = "Test the read-only JamiiTek database connection and show overdue invoices."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        if not is_configured():
            self.stdout.write(self.style.ERROR("JAMIITEK_DB_DSN is not set."))
            self.stdout.write(
                "Add the JamiiTek Postgres connection string to your environment.\n"
                "Use a role with SELECT rights only — this connector must never be "
                "able to write."
            )
            return

        try:
            rows = fetch_overdue_invoices(limit=options["limit"])
        except JamiiTekUnavailable as exc:
            self.stdout.write(self.style.ERROR(f"Connection failed: {exc}"))
            self.stdout.write(
                "\nCheck: is the DSN right? Does the role have SELECT on those "
                "tables? Do the column names in JAMIITEK_OVERDUE_INVOICE_SQL "
                "match your schema? The query must return: client_name, "
                "client_number, invoice_ref, amount, due_date."
            )
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS("Connected. No overdue invoices found."))
            return

        self.stdout.write(self.style.SUCCESS(f"Connected. {len(rows)} overdue invoice(s):\n"))
        total = Decimal(0)
        for row in rows:
            amount = Decimal(str(row.get("amount") or 0))
            total += amount
            self.stdout.write(
                f"  {str(row.get('client_name', '?')):<28} "
                f"{str(row.get('invoice_ref', '?')):<14} "
                f"TZS {amount:>12,.0f}   {row.get('days_late', 0)} days late   "
                f"{row.get('client_number') or self.style.WARNING('NO NUMBER')}"
            )
        self.stdout.write(f"\n  Total: TZS {total:,.0f}")
        self.stdout.write(
            "\nIf these look right, set AGENT_INVOICE_WATCH_ENABLED=true.\n"
            "Leave AGENT_INVOICE_DRAFTS_ENABLED=false until you have watched the "
            "daily summary for a week and trust what it reports."
        )

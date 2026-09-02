"""
Verify the read-only LeadScout connection before enabling the lead watch.

    python manage.py check_leadscout
"""

from django.core.management.base import BaseCommand

from core.agent.leadscout import LeadScoutUnavailable, fetch_new_leads, is_configured


class Command(BaseCommand):
    help = "Test the read-only LeadScout database connection and show recent leads."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        if not is_configured():
            self.stdout.write(self.style.ERROR("LEADSCOUT_DB_DSN is not set."))
            self.stdout.write("Use a role with SELECT rights only — this connector must never write.")
            return

        try:
            rows = fetch_new_leads(limit=options["limit"])
        except LeadScoutUnavailable as exc:
            self.stdout.write(self.style.ERROR(f"Connection failed: {exc}"))
            self.stdout.write(
                "\nThe query must return: business_name, website, score, contact, found_at.\n"
                "Set LEADSCOUT_NEW_LEADS_SQL if your schema differs."
            )
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS("Connected. No new leads in the window."))
            return

        self.stdout.write(self.style.SUCCESS(f"Connected. {len(rows)} lead(s):\n"))
        for row in rows:
            self.stdout.write(
                f"  {str(row.get('business_name', '?')):<32} "
                f"score {str(row.get('score', '-')):<6} "
                f"{row.get('website') or ''}"
            )
        self.stdout.write("\nIf these look right, set AGENT_LEAD_WATCH_ENABLED=true.")

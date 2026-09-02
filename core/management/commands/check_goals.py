"""
Show what the goals module detected in your schema.

Run this BEFORE enabling AGENT_GOALS_ENABLED. The module guesses field names;
this command shows you the guess so you can confirm or override it.

    python manage.py check_goals
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.agent.goals import brief_section, describe, upcoming_milestones


class Command(BaseCommand):
    help = "Show which Goal/GoalMilestone fields the agent detected, and what it would report."

    def handle(self, *args, **options):
        info = describe()

        for model_name, data in info.items():
            if not data.get("found"):
                self.stdout.write(self.style.WARNING(f"{model_name}: not found in core.models"))
                continue
            self.stdout.write(self.style.SUCCESS(f"\n{model_name}: found"))
            self.stdout.write(f"  fields available : {', '.join(data['fields'])}")
            self.stdout.write(f"  title  -> {data['title_field']}")
            self.stdout.write(f"  date   -> {data['date_field']}")
            self.stdout.write(f"  done   -> {data['done_field']}")
            self.stdout.write(f"  status -> {data['status_field']}")
            self.stdout.write(f"  has user field   : {data['has_user']}")

        user = User.objects.filter(is_active=True).order_by("pk").first()
        if user is None:
            self.stdout.write(self.style.WARNING("\nNo user to test against."))
            return

        self.stdout.write(self.style.SUCCESS("\nWhat it would report:\n"))
        items = upcoming_milestones(user)
        if not items:
            self.stdout.write("  (nothing upcoming, or fields not identified)")
        else:
            for item in items:
                self.stdout.write(f"  {item['title']:<40} due {item['due']}  ({item['days_left']} days)")

        self.stdout.write("\nBrief section preview:\n")
        section = brief_section(user)
        self.stdout.write("\n".join(section) if section else "  (disabled — set AGENT_GOALS_ENABLED=true)")

        self.stdout.write(self.style.WARNING(
            "\nIf a field was matched wrongly, override it in settings:\n"
            "  AGENT_GOAL_TITLE_FIELD / AGENT_GOAL_DATE_FIELD / AGENT_GOAL_DONE_FIELD\n"
            "Do not edit core/agent/goals.py."
        ))

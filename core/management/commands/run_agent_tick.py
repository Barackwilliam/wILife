"""
The heartbeat.

This is the command that makes wILife stop being a record book. It wakes up,
looks for work, does it, and goes back to sleep. Nothing in it depends on
William being present.

Usage:
    python manage.py run_agent_tick
    python manage.py run_agent_tick --dry-run
    python manage.py run_agent_tick --job schedule_reminders
"""

import json

from django.core.management.base import BaseCommand

from core.agent.jobs import JOBS, run_tick


class Command(BaseCommand):
    help = "Run one agent tick: process due reminders and any other registered jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Find work and log what would be sent, without sending anything.",
        )
        parser.add_argument(
            "--job",
            choices=sorted(JOBS.keys()),
            help="Run only one named job instead of all of them.",
        )

    def handle(self, *args, **options):
        summary = run_tick(only=options.get("job"), dry_run=options.get("dry_run", False))
        self.stdout.write(json.dumps(summary, indent=2, default=str))
        if summary["failed"]:
            self.stdout.write(self.style.WARNING(f"{summary['failed']} item(s) failed — see logs"))
        else:
            self.stdout.write(self.style.SUCCESS("tick ok"))

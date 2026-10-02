"""
Draft today's news batch now, without waiting for the agent tick.

    python manage.py news_generate            # write the missing drafts, then ask for approval
    python manage.py news_generate --publish  # ...and publish immediately (no approval)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from news import pipeline
from news.models import NewsBatch


class Command(BaseCommand):
    help = "Draft today's news batch now."

    def add_arguments(self, parser):
        parser.add_argument("--publish", action="store_true", help="Publish immediately, skipping approval")

    def handle(self, *args, **options):
        result = pipeline.run(force=True)
        self.stdout.write(f"written={result['sent']} failed={result['failed']}  {result.get('detail', '')}")
        batch = NewsBatch.objects.filter(date=timezone.localdate()).first()
        if not batch:
            return
        for a in batch.articles.order_by("slot"):
            self.stdout.write(f"  {a.slot}. [{a.get_category_display()}] {a.title}")
        if options["publish"] and batch.status in ("drafting", "pending"):
            n = pipeline.publish_batch(batch)
            self.stdout.write(self.style.SUCCESS(f"Published {n}."))

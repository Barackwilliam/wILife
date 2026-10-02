"""
Check every configured news feed: reachable, parseable, how many fresh items.

    python manage.py news_check
"""

from django.core.management.base import BaseCommand

from news import feeds
from news.models import Category


class Command(BaseCommand):
    help = "Show which news feeds work and how many recent stories each has."

    def handle(self, *args, **options):
        for value, label in Category.choices:
            if value == "jamiitek":
                continue
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{label}"))
            for url in feeds.feeds_for(value):
                try:
                    items = feeds.fetch(url)
                    newest = max((i.published for i in items if i.published), default=None)
                    self.stdout.write(self.style.SUCCESS(
                        f"  ✓ {len(items):3d} items  newest {newest:%Y-%m-%d %H:%M}  {url}" if newest
                        else f"  ✓ {len(items):3d} items  {url}"))
                    if items:
                        self.stdout.write(f"      e.g. {items[0].title[:90]}")
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  ✗ {url}\n      {exc}"))
        self.stdout.write("\nOverride feeds with NEWS_FEEDS='{\"tanzania\": [\"https://.../feed/\"]}'.")

"""
Move cover images from the database into the Supabase Storage bucket.

    python manage.py news_move_covers          # upload, then clear the DB copies
    python manage.py news_move_covers --check  # just test the bucket connection

Run once after setting the SUPABASE_S3_* variables. Safe to re-run.
"""

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from news.models import Article


class Command(BaseCommand):
    help = "Upload database-stored cover images to the storage bucket."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Only test uploading to the bucket")

    def handle(self, *args, **options):
        if not getattr(settings, "USE_SUPABASE_STORAGE", False):
            raise CommandError("Storage bucket not configured — set SUPABASE_S3_ACCESS_KEY, "
                               "SUPABASE_S3_SECRET_KEY, SUPABASE_S3_ENDPOINT and SUPABASE_BUCKET_NAME.")
        name = default_storage.save("habari/_check.txt", ContentFile(b"wILife storage check"))
        url = default_storage.url(name)
        default_storage.delete(name)
        self.stdout.write(self.style.SUCCESS(f"Bucket OK — files will be served from e.g. {url}"))
        if options["check"]:
            return

        moved = 0
        for a in Article.objects.filter(cover_image="").exclude(cover_png=None).iterator():
            png = bytes(a.cover_png)
            art = bytes(a.cover_thumb) if a.cover_thumb else None
            if art is None:
                from news import covers
                art = covers.thumbnail(png)
            a.store_cover(png, art)
            a.save(update_fields=["cover_image", "cover_art", "cover_png", "cover_thumb"])
            moved += 1
        remaining = Article.objects.filter(cover_image="").count()
        self.stdout.write(self.style.SUCCESS(f"Moved {moved} article cover(s) to the bucket."))
        if remaining:
            self.stdout.write(f"{remaining} article(s) have no cover at all — they get one when re-rendered.")

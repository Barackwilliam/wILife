from django.db import migrations


def backfill(apps, schema_editor):
    from news import covers
    Article = apps.get_model("news", "Article")
    for a in Article.objects.exclude(cover_png=None).filter(cover_thumb=None).iterator():
        try:
            a.cover_thumb = covers.thumbnail(bytes(a.cover_png))
            a.save(update_fields=["cover_thumb"])
        except Exception:
            pass


class Migration(migrations.Migration):
    dependencies = [("news", "0003_cover_thumb")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]

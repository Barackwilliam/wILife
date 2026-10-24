from django.db import migrations


def requeue(apps, schema_editor):
    """Picha za mechi zitengenezwe upya kwa muundo mpya (nembo kubwa, kadi ya mraba)."""
    apps.get_model("news", "LiveMatch").objects.filter(fixture_id__isnull=False).update(cover_ok=False)


class Migration(migrations.Migration):
    dependencies = [("news", "0015_match_cover_ok")]
    operations = [migrations.RunPython(requeue, migrations.RunPython.noop)]

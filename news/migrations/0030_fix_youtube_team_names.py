import re

from django.db import migrations

_PREFIX = re.compile(r"(?i)^\W*(magoli|mabao|goals?)\b")


def fix(apps, schema_editor):
    """Video za YouTube zenye kichwa 'Magoli | A 2-1 B | …' zilihifadhiwa na timu ya nyumbani 'Magoli | A'."""
    from news.scorebat import team_key
    from news.youtube import teams_from_title
    Highlight = apps.get_model("news", "Highlight")
    for h in Highlight.objects.filter(provider="youtube"):
        if not _PREFIX.match(h.home or ""):
            continue
        home, away = teams_from_title(h.title)
        if home and away:
            Highlight.objects.filter(pk=h.pk).update(home=home[:80], away=away[:80], home_key=team_key(home)[:80],
                                                     away_key=team_key(away)[:80])


class Migration(migrations.Migration):
    dependencies = [("news", "0029_soka360_channel_posts")]
    operations = [migrations.RunPython(fix, migrations.RunPython.noop)]

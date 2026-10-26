from django.db import migrations

# Chaneli RASMI za YouTube (zimehakikiwa: maelezo ya chaneli yenyewe + takwimu za vidIQ/Social Blade, Okt 2026).
# Zinathibitishwa kwenye YouTube na news/youtube.run() pale YOUTUBE_API_KEY ikiwepo.
CHANNELS = [
    # (kiungo, slug ya timu au None)
    ("https://www.youtube.com/@simbasctanzania255", "simba"),     # Simba SC Tanzania — chaneli rasmi ya klabu
    ("https://www.youtube.com/@YoungAfricansSCTV", "yanga"),      # Yanga TV — chaneli ya Young Africans SC
    ("https://www.youtube.com/@azamtvtz", None),                  # Azam TV — mrushaji wa NBC Premier League
]


def seed(apps, schema_editor):
    Team = apps.get_model("news", "Team")
    VideoChannel = apps.get_model("news", "VideoChannel")
    for link, slug in CHANNELS:
        VideoChannel.objects.get_or_create(link=link, defaults={
            "team": Team.objects.filter(slug=slug).first() if slug else None, "highlights_only": True})


class Migration(migrations.Migration):
    dependencies = [("news", "0027_youtube_channels"), ("news", "0026_seed_teams")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

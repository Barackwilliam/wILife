from django.db import migrations

TEAMS = [
    # (jina, slug, majina kwenye data ya mechi, maneno ya habari)
    ("Simba SC", "simba", "Simba", "Simba SC, Simba Sports Club, klabu ya Simba, timu ya Simba, Wekundu wa Msimbazi"),
    ("Young Africans (Yanga)", "yanga", "Young Africans, Yanga", "Yanga, Young Africans"),
    ("Azam FC", "azam", "Azam", "Azam FC, klabu ya Azam, timu ya Azam"),
    ("Singida Fountain Gate", "singida-fountain-gate", "Singida Fountain Gate", "Singida Fountain Gate, Singida FG"),
    ("Taifa Stars", "taifa-stars", "Tanzania", "Taifa Stars, timu ya taifa ya Tanzania"),
]


def seed(apps, schema_editor):
    Team = apps.get_model("news", "Team")
    for i, (name, slug, names, terms) in enumerate(TEAMS):
        Team.objects.get_or_create(slug=slug, defaults={"name": name, "match_names": names,
                                                         "news_terms": terms, "sort": i})


class Migration(migrations.Migration):
    dependencies = [("news", "0025_team")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

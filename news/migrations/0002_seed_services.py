"""Starter JamiiTek services. Edit them in Django admin — the text here is a first draft."""

from django.db import migrations

SERVICES = [
    {
        "order": 1, "slug": "utengenezaji-wa-tovuti", "icon": "bi-globe2",
        "name": "Utengenezaji wa Tovuti",
        "tagline": "Tovuti za kisasa, zenye kasi na zinazoonekana Google — kwa biashara, taasisi na watu binafsi.",
        "description": "JamiiTek inabuni na kutengeneza tovuti zinazofanya kazi vizuri kwenye simu na kompyuta, "
                       "zenye mfumo wa kusimamia maudhui ili mteja aweze kubadilisha taarifa mwenyewe.",
        "benefits": "Wateja wanakupata mtandaoni saa 24\nMuonekano wa kitaalamu unaojenga imani\n"
                    "Inafanya kazi vizuri kwenye simu\nUnasimamia maudhui mwenyewe bila kuhitaji fundi kila mara",
        "opportunities": "Biashara ndogo na za kati zinazotaka wateja wapya\nShule, makanisa, misikiti na NGOs\n"
                         "Watoa huduma binafsi: mawakili, madaktari, washauri",
    },
    {
        "order": 2, "slug": "hosting-na-domain", "icon": "bi-hdd-network",
        "name": "Hosting na Domain",
        "tagline": "Jina la tovuti (.co.tz, .com) na seva salama zinazosimamiwa kwa ajili yako.",
        "description": "Tunasajili na kusimamia domain na hosting ya tovuti yako, pamoja na kufuatilia tarehe za "
                       "kuisha ili huduma yako isikatike bila taarifa.",
        "benefits": "Hakuna usumbufu wa kiufundi — tunasimamia kila kitu\nUnakumbushwa kabla domain au hosting haijaisha\n"
                    "Barua pepe za kitaalamu kwa jina la biashara yako",
        "opportunities": "Biashara inayoanza kujenga uwepo mtandaoni\nTaasisi zinazotaka email rasmi (jina@biashara.co.tz)",
    },
    {
        "order": 3, "slug": "whatsapp-bots-na-ai", "icon": "bi-whatsapp",
        "name": "WhatsApp Bots na AI",
        "tagline": "Wasaidizi wa WhatsApp wanaojibu wateja wako papo hapo, kwa Kiswahili.",
        "description": "JamiiTek inajenga bots za WhatsApp zinazotumia akili bandia (AI) kujibu maswali, kutoa "
                       "ushauri na kupokea maombi — mfano mmoja ni Kilimoni, bot ya ushauri wa kilimo kwa wakulima.",
        "benefits": "Wateja wanajibiwa hata usiku na siku za mapumziko\nInapunguza muda wa wafanyakazi kwenye maswali yanayojirudia\n"
                    "Inazungumza Kiswahili — lugha ya wateja wako\nInakusanya taarifa za wateja kwa ajili ya ufuatiliaji",
        "opportunities": "Maduka na huduma zenye maswali mengi kutoka kwa wateja\nSekta ya kilimo, afya na elimu\n"
                         "Taasisi zinazotaka kuwafikia watu wengi kwa gharama ndogo",
    },
    {
        "order": 4, "slug": "mifumo-ya-usimamizi", "icon": "bi-diagram-3",
        "name": "Mifumo ya Usimamizi",
        "tagline": "Mifumo maalum ya kusimamia mauzo, stoo, ankara, wanachama na zaidi.",
        "description": "Tunatengeneza mifumo ya mtandaoni inayolingana na jinsi biashara yako inavyofanya kazi — "
                       "kutoka kufuatilia mauzo na ankara hadi ripoti za kila siku.",
        "benefits": "Taarifa zote za biashara mahali pamoja\nRipoti za papo hapo kusaidia maamuzi\n"
                    "Kupunguza makosa ya kuandika kwa mkono\nInapatikana popote kupitia simu au kompyuta",
        "opportunities": "Maduka ya jumla na rejareja\nShule, SACCOS na vikundi vya kuweka akiba\nWatoa huduma wenye wateja wengi",
    },
    {
        "order": 5, "slug": "seo-na-masoko-ya-kidijitali", "icon": "bi-graph-up-arrow",
        "name": "SEO na Masoko ya Kidijitali",
        "tagline": "Ionekane juu Google na mitandao ya kijamii — wateja wakutafute wakupate.",
        "description": "Tunaboresha tovuti yako ili ionekane kwenye matokeo ya utafutaji (SEO), na kupanga maudhui "
                       "na matangazo ya mtandaoni yanayolenga wateja sahihi.",
        "benefits": "Wateja wapya kutoka Google bila kulipia kila bofya\nMaudhui yanayojenga jina la biashara\n"
                    "Takwimu za kuonyesha nini kinafanya kazi",
        "opportunities": "Biashara zenye tovuti lakini hazipati wateja kupitia mtandao\n"
                         "Wanaoanza biashara mtandaoni (e-commerce)",
    },
]


def seed(apps, schema_editor):
    Service = apps.get_model("news", "JamiiTekService")
    for data in SERVICES:
        Service.objects.get_or_create(slug=data["slug"], defaults=data)


def unseed(apps, schema_editor):
    apps.get_model("news", "JamiiTekService").objects.filter(slug__in=[s["slug"] for s in SERVICES]).delete()


class Migration(migrations.Migration):
    dependencies = [("news", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]

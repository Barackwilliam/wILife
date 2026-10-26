from urllib.parse import urlsplit

from django.contrib.sitemaps import Sitemap as _Sitemap
from django.urls import reverse

from news.models import Article, Category, JamiiTekService
from news.seo import site_root


class Sitemap(_Sitemap):
    """URL zote kwenye domain rasmi (SITE_URL), si host iliyotumika kusoma sitemap."""

    def get_protocol(self, protocol=None):
        root = site_root()
        return urlsplit(root).scheme if root else super().get_protocol(protocol)

    def get_domain(self, site=None):
        root = site_root()
        return urlsplit(root).netloc if root else super().get_domain(site)


class ArticleSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return Article.published.defer("cover_png", "body").order_by("-published_at")[:5000]

    def lastmod(self, obj):
        return obj.updated_at


class SectionSitemap(Sitemap):
    changefreq = "hourly"
    priority = 1.0

    def items(self):
        return ["home", "live_hub", "matches_today", "results_yesterday", "predictions", "videos", "teams",
                "leagues", "matches_tomorrow", "latest"] + list(Category.values)

    def location(self, item):
        if item in ("home", "live_hub", "matches_today", "results_yesterday", "predictions", "videos", "teams",
                    "leagues", "matches_tomorrow", "latest"):
            return reverse(f"news:{item}")
        return reverse("news:category", args=[item])


class ServiceSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return JamiiTekService.objects.filter(active=True)


class StaticSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.4

    def items(self):
        return ["news:services", "news:about", "news:policy", "news:corrections"]

    def location(self, item):
        return reverse(item)


class TopicSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.5

    def items(self):
        from news.views import TOPIC_MIN_ARTICLES, _topics
        return [k for _, k in _topics(limit=200, min_count=TOPIC_MIN_ARTICLES)]

    def location(self, item):
        return reverse("news:topic", args=[item])


class DaySitemap(Sitemap):
    """Kurasa za matokeo ya siku zilizopita (/mechi/<tarehe>/) — leo na jana zina anwani zao za kudumu."""
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        from datetime import timedelta
        from django.utils import timezone
        from news.fixtures import days_with_fixtures
        yesterday = timezone.localdate() - timedelta(days=1)
        return [d for d in days_with_fixtures() if d < yesterday]

    def location(self, item):
        return reverse("news:matches_day", args=[item.isoformat()])


class VideoSitemap(Sitemap):
    """Kurasa za video za magoli (/video/<slug>/)."""
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        from news.models import Highlight
        return Highlight.objects.order_by("-date")[:1000]

    def lastmod(self, obj):
        return obj.updated_at


class TeamSitemap(Sitemap):
    """Kurasa za timu (/timu/<slug>/) — zinabadilika kila mechi na kila habari."""
    changefreq = "daily"
    priority = 0.8

    def items(self):
        from news.models import Team
        return Team.objects.filter(active=True)


class LeagueSitemap(Sitemap):
    """Kurasa za ligi (/ligi/<slug>/) — msimamo na matokeo yanabadilika kila siku."""
    changefreq = "daily"
    priority = 0.8

    def items(self):
        from news.models import League
        return League.objects.filter(active=True)


class FixtureSitemap(Sitemap):
    """Kurasa za mechi zisizo na LIVE (/mechi/<tarehe>/<id>-…/) — hakikisho kabla, matokeo baadaye."""
    changefreq = "hourly"
    priority = 0.6

    def items(self):
        from datetime import timedelta
        from django.utils import timezone
        from news.models import Fixture, LiveMatch
        since = timezone.localdate() - timedelta(days=30)
        live = set(LiveMatch.objects.filter(article__status="published", fixture_id__isnull=False)
                   .values_list("fixture_id", flat=True))
        return [f for f in Fixture.objects.filter(day__gte=since).order_by("-day", "rank")[:3000]
                if f.fixture_id not in live]          # mechi zenye LIVE: ukurasa wa habari yake uko kwenye articles

    def lastmod(self, obj):
        return obj.updated_at


class AuthorSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.4

    def items(self):
        from news.models import Author
        return Author.objects.filter(active=True)


SITEMAPS = {"days": DaySitemap, "topics": TopicSitemap, "sections": SectionSitemap, "articles": ArticleSitemap,
            "services": ServiceSitemap, "pages": StaticSitemap, "videos": VideoSitemap,
            "teams": TeamSitemap, "leagues": LeagueSitemap, "fixtures": FixtureSitemap,
            "authors": AuthorSitemap}

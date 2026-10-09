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
        return ["home", "latest"] + list(Category.values)

    def location(self, item):
        if item in ("home", "latest"):
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
        return ["news:services", "news:about"]

    def location(self, item):
        return reverse(item)


class TopicSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.5

    def items(self):
        from news.views import _topics
        return [k for _, k in _topics(limit=200)]

    def location(self, item):
        return reverse("news:topic", args=[item])


SITEMAPS = {"topics": TopicSitemap, "sections": SectionSitemap, "articles": ArticleSitemap,
            "services": ServiceSitemap, "pages": StaticSitemap}

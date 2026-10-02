from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from news.models import Article, Category, JamiiTekService


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
        return ["home"] + list(Category.values)

    def location(self, item):
        return reverse("news:home") if item == "home" else reverse("news:category", args=[item])


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


SITEMAPS = {"sections": SectionSitemap, "articles": ArticleSitemap,
            "services": ServiceSitemap, "pages": StaticSitemap}

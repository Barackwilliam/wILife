from django.contrib.syndication.views import Feed
from django.http import Http404
from django.urls import reverse

from news.models import Article, Category


class LatestFeed(Feed):
    description = "Habari kuu za Dunia, Afrika na Tanzania kwa Kiswahili — kila siku."
    language = "sw"

    def get_object(self, request, category=None):
        if category and category not in Category.values:
            raise Http404
        return category

    def title(self, category):
        return f"wILife Habari — {Category(category).label}" if category else "wILife Habari"

    def link(self, category):
        return reverse("news:category", args=[category]) if category else reverse("news:home")

    def items(self, category):
        qs = Article.published.defer("cover_png")
        if category:
            qs = qs.filter(category=category)
        return qs[:30]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.excerpt

    def item_pubdate(self, item):
        return item.published_at

    def item_categories(self, item):
        return [item.get_category_display()]

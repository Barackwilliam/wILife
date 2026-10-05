from django.contrib.sitemaps.views import sitemap
from django.urls import path

from news import views
from news.rss import LatestFeed
from news.sitemaps import SITEMAPS

app_name = "news"

urlpatterns = [
    path("", views.home, name="home"),
    path("habari/", views.latest, name="latest"),
    path("habari/picha/<int:pk>.png", views.cover, name="cover"),
    path("habari/picha/<int:pk>-sanaa.webp", views.cover_thumb, name="cover_thumb"),
    path("mada/<slug:slug>/", views.topic, name="topic"),
    path("habari/<str:category>/", views.category, name="category"),
    path("habari/<str:category>/<slug:slug>/", views.article, name="article"),
    path("huduma/", views.services, name="services"),
    path("huduma/<slug:slug>/", views.service_detail, name="service"),
    path("tafuta/", views.search, name="search"),
    path("kuhusu/", views.about, name="about"),
    path("rss/", LatestFeed(), name="rss"),
    path("rss/<str:category>/", LatestFeed(), name="rss_category"),
    path("robots.txt", views.robots, name="robots"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("site.webmanifest", views.webmanifest, name="webmanifest"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("news-sitemap.xml", views.news_sitemap, name="news_sitemap"),
    path("habari-admin/rasimu/", views.review, name="review"),
    path("habari-admin/rasimu/action/", views.review_action, name="review_action"),
    path("<str:key>.txt", views.indexnow_key, name="indexnow_key"),
]

from django.utils import timezone


def news(request):
    """Swahili date and footer services for every public page."""
    from news.models import JamiiTekService
    from news.pipeline import swahili_date
    from django.conf import settings
    social = []
    for url in getattr(settings, "SOCIAL_LINKS", []):
        u = url.lower()
        name = next((n for n in ("facebook", "instagram", "youtube", "linkedin", "whatsapp")
                     if n in u), "twitter-x" if ("twitter" in u or "x.com" in u) else "link-45deg")
        social.append({"url": url, "icon": name})
    import os
    return {
        # Toleo la deploy (Render) kwenye ?v= ya CSS — kivinjari kisitumie news.css ya zamani baada ya deploy
        "asset_v": os.environ.get("RENDER_GIT_COMMIT", "")[:8],
        "live_now": _live_now(),
        "social_links": social,
        "sw_today": swahili_date(timezone.localdate()),
        "footer_services": JamiiTekService.objects.filter(active=True).only("name", "slug")[:5],
    }


def _live_now():
    """Habari ngapi ziko LIVE sasa — kitufe cha LIVE kwenye menyu kinawaka zikiwepo."""
    from django.core.cache import cache
    from news.models import Article
    n = cache.get("news:live_now")
    if n is None:
        n = Article.published.filter(live_started_at__isnull=False, live_ended_at__isnull=True).count()
        cache.set("news:live_now", n, 60)
    return n

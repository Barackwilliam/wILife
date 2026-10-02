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
    return {
        "social_links": social,
        "sw_today": swahili_date(timezone.localdate()),
        "footer_services": JamiiTekService.objects.filter(active=True).only("name", "slug")[:5],
    }

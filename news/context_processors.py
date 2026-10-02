from django.utils import timezone


def news(request):
    """Swahili date and footer services for every public page."""
    from news.models import JamiiTekService
    from news.pipeline import swahili_date
    return {
        "sw_today": swahili_date(timezone.localdate()),
        "footer_services": JamiiTekService.objects.filter(active=True).only("name", "slug")[:5],
    }

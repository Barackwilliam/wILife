from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST

from news import pipeline
from news.models import CATEGORY_BLURBS, Article, Category, JamiiTekService, NewsBatch

CARD_FIELDS = ("id", "title", "slug", "category", "excerpt", "published_at", "body", "views")


def _published():
    return Article.published.defer("cover_png")


def home(request):
    latest = list(_published()[:13])
    lead, rest = (latest[0], latest[1:]) if latest else (None, [])
    sections = []
    for value, label in Category.choices:
        items = list(_published().filter(category=value)[:4])
        if items:
            sections.append({"key": value, "label": label, "blurb": CATEGORY_BLURBS[value], "items": items})
    week_ago = timezone.now() - timedelta(days=7)
    return render(request, "news/home.html", {
        "lead": lead,
        "top": rest[:6],
        "more": rest[6:12],
        "sections": sections,
        "popular": _published().filter(published_at__gte=week_ago).order_by("-views")[:5],
        "services": JamiiTekService.objects.filter(active=True)[:6],
        "today": timezone.localdate(),
    })


def category(request, category):
    if category not in Category.values:
        raise Http404
    page = Paginator(_published().filter(category=category), 12).get_page(request.GET.get("page"))
    return render(request, "news/category.html", {
        "category": category,
        "label": Category(category).label,
        "blurb": CATEGORY_BLURBS[category],
        "page": page,
        "services": JamiiTekService.objects.filter(active=True)[:4],
    })


def article(request, category, slug):
    qs = Article.objects.defer("cover_png") if request.user.is_staff else _published()
    item = get_object_or_404(qs, category=category, slug=slug)
    if item.status == "published" and not request.user.is_staff:
        Article.objects.filter(pk=item.pk).update(views=F("views") + 1)
    related = _published().filter(category=item.category).exclude(pk=item.pk)[:4]
    service = item.service or JamiiTekService.objects.filter(active=True).order_by("?").first()
    return render(request, "news/article.html", {
        "article": item,
        "related": related,
        "latest": _published().exclude(pk=item.pk)[:5],
        "service": service,
        "is_draft": item.status != "published",
    })


@cache_control(public=True, max_age=86400)
def cover(request, pk):
    item = get_object_or_404(Article.objects.only("cover_png", "status", "published_at"), pk=pk)
    if item.status != "published" and not request.user.is_staff:
        raise Http404
    if not item.cover_png:
        raise Http404
    return HttpResponse(bytes(item.cover_png), content_type="image/png")


def services(request):
    return render(request, "news/services.html", {
        "services": JamiiTekService.objects.filter(active=True),
    })


def service_detail(request, slug):
    service = get_object_or_404(JamiiTekService, slug=slug, active=True)
    return render(request, "news/service.html", {
        "service": service,
        "articles": _published().filter(service=service)[:4],
        "others": JamiiTekService.objects.filter(active=True).exclude(pk=service.pk)[:4],
    })


def search(request):
    q = (request.GET.get("q") or "").strip()[:100]
    results = _published().filter(
        Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(keywords__icontains=q)
    ) if q else Article.objects.none()
    page = Paginator(results, 12).get_page(request.GET.get("page"))
    return render(request, "news/search.html", {"q": q, "page": page})


def about(request):
    return render(request, "news/about.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", "")})


def robots(request):
    sitemap = request.build_absolute_uri("/sitemap.xml")
    news_map = request.build_absolute_uri("/news-sitemap.xml")
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /dashboard/",
        "Disallow: /agent/",
        "Disallow: /habari-admin/",
        "",
        f"Sitemap: {sitemap}",
        f"Sitemap: {news_map}",
    ])
    return HttpResponse(body + "\n", content_type="text/plain")


def news_sitemap(request):
    """Google News sitemap: articles from the last 48 hours."""
    since = timezone.now() - timedelta(hours=48)
    items = _published().filter(published_at__gte=since)[:1000]
    return render(request, "news/news_sitemap.xml", {"items": items}, content_type="application/xml")


# ---------------------------------------------------------------------------
# Staff review
# ---------------------------------------------------------------------------

@staff_member_required
def review(request):
    batches = NewsBatch.objects.prefetch_related("articles")[:10]
    return render(request, "news/review.html", {
        "batches": batches,
        "enabled": getattr(settings, "NEWS_ENABLED", False),
        "has_key": bool(getattr(settings, "GROQ_API_KEY", "")),
        "services": JamiiTekService.objects.count(),
    })


@staff_member_required
@require_POST
def review_action(request):
    action = request.POST.get("action")
    if action in ("publish_batch", "reject_batch"):
        batch = get_object_or_404(NewsBatch, pk=request.POST.get("batch"))
        if action == "publish_batch":
            n = pipeline.publish_batch(batch)
            if batch.approval and batch.approval.status == "pending":
                batch.approval.status = "sent"
                batch.approval.result = f"Published from review page ({n})"
                batch.approval.save(update_fields=["status", "result"])
            messages.success(request, f"Habari {n} zimechapishwa.")
        else:
            pipeline.reject_batch(batch)
            if batch.approval and batch.approval.status == "pending":
                batch.approval.status = "rejected"
                batch.approval.save(update_fields=["status"])
            messages.info(request, "Rasimu zimekataliwa.")
    elif action in ("publish_one", "reject_one"):
        item = get_object_or_404(Article, pk=request.POST.get("article"))
        if action == "publish_one":
            item.publish()
            messages.success(request, f"Imechapishwa: {item.title}")
        else:
            item.status = "rejected"
            item.save(update_fields=["status", "updated_at"])
            messages.info(request, f"Imekataliwa: {item.title}")
    elif action == "generate":
        import time
        result = pipeline.run(force=True, deadline=time.monotonic() + 45)
        messages.info(request, f"Rasimu {result['sent']} zimeandikwa. {result.get('detail', '')}")
    return redirect("news:review")

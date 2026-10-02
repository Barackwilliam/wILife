from collections import Counter
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import cache_control, cache_page
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_POST

from news import indexnow, pipeline
from news.models import CATEGORY_BLURBS, Article, Category, JamiiTekService, NewsBatch

LIGHT = ("cover_png", "cover_thumb")


def _published():
    return Article.published.defer(*LIGHT)


def public_page(view):
    """gzip always; cache the rendered page for anonymous visitors (who are almost all traffic)."""
    cached = cache_page(getattr(settings, "NEWS_CACHE_SECONDS", 300))(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated or request.GET.get("preview"):
            return view(request, *args, **kwargs)
        return cached(request, *args, **kwargs)
    return gzip_page(wrapper)


def _topics(limit=14):
    counts, names = Counter(), {}
    for kw in _published().values_list("keywords", flat=True)[:120]:
        for raw in kw.split(","):
            tag = raw.strip()
            key = slugify(tag)
            if tag and key and len(tag) <= 30:
                counts[key] += 1
                names.setdefault(key, tag)
    return [(names[k], k) for k, _ in counts.most_common(limit)]


@public_page
def home(request):
    latest = list(_published()[:40])
    lead = latest[0] if latest else None
    stack = latest[1:4]
    used = {a.pk for a in latest[:4]}
    by_cat = {}
    for a in latest:
        by_cat.setdefault(a.category, []).append(a)

    def section(cat, n):
        items = [a for a in by_cat.get(cat, []) if a.pk not in used][:n]
        if len(items) < 2:
            items = by_cat.get(cat, [])[:n]
        return items

    week_ago = timezone.now() - timedelta(days=7)
    return render(request, "news/home.html", {
        "lead": lead,
        "stack": stack,
        "rail": latest[4:12],
        "tanzania": section("tanzania", 5),
        "afrika": section("afrika", 4),
        "dunia": section("dunia", 3),
        "jamiitek": by_cat.get("jamiitek", [])[:3],
        "popular": _published().filter(published_at__gte=week_ago).order_by("-views")[:6],
        "topics": _topics(),
        "services": JamiiTekService.objects.filter(active=True)[:6],
        "ticker": latest[:10],
    })


@public_page
def latest(request):
    page = Paginator(_published(), 20).get_page(request.GET.get("page"))
    return render(request, "news/latest.html", {"page": page, "topics": _topics()})


@public_page
def category(request, category):
    if category not in Category.values:
        raise Http404
    page = Paginator(_published().filter(category=category), 13).get_page(request.GET.get("page"))
    week_ago = timezone.now() - timedelta(days=7)
    return render(request, "news/category.html", {
        "category": category,
        "label": Category(category).label,
        "blurb": CATEGORY_BLURBS[category],
        "page": page,
        "popular": _published().filter(category=category, published_at__gte=week_ago).order_by("-views")[:5],
        "services": JamiiTekService.objects.filter(active=True)[:2],
    })


@public_page
def topic(request, slug):
    candidates = _published().filter(keywords__icontains=slug.replace("-", " "))
    matches = [a for a in candidates[:300] if any(k == slug for _, k in a.tag_list())]
    if not matches:
        raise Http404
    name = next(n for n, k in matches[0].tag_list() if k == slug)
    page = Paginator(matches, 13).get_page(request.GET.get("page"))
    return render(request, "news/topic.html", {"name": name, "slug": slug, "page": page, "topics": _topics()})


def article(request, category, slug):
    qs = Article.objects.defer(*LIGHT) if request.user.is_staff else _published()
    item = get_object_or_404(qs, category=category, slug=slug)
    if item.status == "published" and not request.user.is_staff:
        Article.objects.filter(pk=item.pk).update(views=F("views") + 1)
    related = list(_published().filter(category=item.category).exclude(pk=item.pk)[:4])
    service = item.service or JamiiTekService.objects.filter(active=True).order_by("?").first()
    response = render(request, "news/article.html", {
        "article": item,
        "related": related,
        "latest": _published().exclude(pk=item.pk)[:6],
        "service": service,
        "is_draft": item.status != "published",
    })
    return gzip_page(lambda r: response)(request)


@cache_control(public=True, max_age=604800, immutable=True)
def cover(request, pk):
    item = get_object_or_404(Article.objects.only("cover_png", "status", "published_at"), pk=pk)
    if (item.status != "published" and not request.user.is_staff) or not item.cover_png:
        raise Http404
    return HttpResponse(bytes(item.cover_png), content_type="image/png")


@cache_control(public=True, max_age=604800, immutable=True)
def cover_thumb(request, pk):
    item = get_object_or_404(Article.objects.only("cover_thumb", "cover_png", "status"), pk=pk)
    if item.status != "published" and not request.user.is_staff:
        raise Http404
    if item.cover_thumb:
        return HttpResponse(bytes(item.cover_thumb), content_type="image/webp")
    if item.cover_png:
        return HttpResponse(bytes(item.cover_png), content_type="image/png")
    raise Http404


@public_page
def services(request):
    return render(request, "news/services.html", {"services": JamiiTekService.objects.filter(active=True)})


@public_page
def service_detail(request, slug):
    service = get_object_or_404(JamiiTekService, slug=slug, active=True)
    return render(request, "news/service.html", {
        "service": service,
        "articles": _published().filter(service=service)[:4],
        "others": JamiiTekService.objects.filter(active=True).exclude(pk=service.pk)[:4],
    })


@gzip_page
def search(request):
    q = (request.GET.get("q") or "").strip()[:100]
    results = _published().filter(
        Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(keywords__icontains=q)
    ) if q else Article.objects.none()
    page = Paginator(results, 12).get_page(request.GET.get("page"))
    return render(request, "news/search.html", {"q": q, "page": page})


@public_page
def about(request):
    return render(request, "news/about.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", "")})


def robots(request):
    root = request.build_absolute_uri("/").rstrip("/")
    body = "\n".join([
        "User-agent: *", "Allow: /",
        "Disallow: /admin/", "Disallow: /dashboard/", "Disallow: /agent/", "Disallow: /habari-admin/",
        "Disallow: /tafuta/", "",
        f"Sitemap: {root}/sitemap.xml", f"Sitemap: {root}/news-sitemap.xml",
    ])
    return HttpResponse(body + "\n", content_type="text/plain")


def indexnow_key(request, key):
    if not key or key != getattr(settings, "INDEXNOW_KEY", ""):
        raise Http404
    return HttpResponse(key, content_type="text/plain")


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
    return render(request, "news/review.html", {
        "batches": NewsBatch.objects.prefetch_related("articles")[:10],
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
            indexnow.submit([item.get_absolute_url()])
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

from collections import Counter
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.templatetags.static import static
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


RIVER_PAGE = 16


def _count(items, since=None):
    """Count a queryset or a list of articles, optionally only those published since a time."""
    if isinstance(items, list):
        return sum(1 for a in items if since is None or a.published_at >= since)
    return (items.filter(published_at__gte=since) if since else items).count()


def _river(request, items, template, context, lead=True):
    """
    Shared by every list page: paginate, pick a lead story for page 1, and
    serve only the rows when the "load more" button asks for ?partial=1.
    """
    page = Paginator(items, RIVER_PAGE).get_page(request.GET.get("page"))
    rows = list(page.object_list)
    partial = request.GET.get("partial") == "1"
    lead_item = rows.pop(0) if lead and rows and page.number == 1 and not partial else None
    context = {**context, "page": page, "rows": rows, "lead": lead_item}
    if partial:
        response = render(request, "news/_river_rows.html", context)
        response["X-Robots-Tag"] = "noindex"
        return response
    week_ago = timezone.now() - timedelta(days=7)
    popular = list(_published().filter(published_at__gte=week_ago).order_by("-views")[:5]) or \
        list(_published().order_by("-views")[:5])
    counts = dict(Article.published.order_by().values_list("category").annotate(n=Count("id")))
    context.setdefault("popular", popular)
    context.setdefault("topics", _topics(12))
    context.setdefault("services", JamiiTekService.objects.filter(active=True).order_by("?")[:1])
    context.update({
        "chips": [(value, label, counts.get(value, 0)) for value, label in Category.choices],
        "all_count": sum(counts.values()),
        "total": _count(items),
        "day_count": _count(items, timezone.now() - timedelta(hours=24)),
        "week_count": _count(items, week_ago),
        "updated": rows[0].published_at if rows else (lead_item.published_at if lead_item else None),
    })
    if lead_item and lead_item.published_at and (not rows or lead_item.published_at > rows[0].published_at):
        context["updated"] = lead_item.published_at
    return render(request, template, context)


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
    return _river(request, _published(), "news/latest.html", {})


@public_page
def category(request, category):
    if category not in Category.values:
        raise Http404
    week_ago = timezone.now() - timedelta(days=7)
    return _river(request, _published().filter(category=category), "news/category.html", {
        "category": category,
        "label": Category(category).label,
        "blurb": CATEGORY_BLURBS[category],
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
    return _river(request, matches, "news/topic.html", {"name": name, "slug": slug})


def article(request, category, slug):
    qs = Article.objects.defer(*LIGHT) if request.user.is_staff else _published()
    item = get_object_or_404(qs, category=category, slug=slug)
    views = item.views
    if item.status == "published" and not request.user.is_staff:
        Article.objects.filter(pk=item.pk).update(views=F("views") + 1)
        views += 1
    related = list(_published().filter(category=item.category).exclude(pk=item.pk)[:4])
    service = item.service or JamiiTekService.objects.filter(active=True).order_by("?").first()
    response = render(request, "news/article.html", {
        "article": item,
        "related": related,
        "latest": _published().exclude(pk=item.pk)[:6],
        "service": service,
        "is_draft": item.status != "published",
        "views": views,
        # Makala za JamiiTek (au zinazoitaja) — majina ya huduma yanakuwa viungo vya jamiitek.com
        "jt_links": item.category == Category.JAMIITEK or "jamiitek" in item.body.lower(),
    })
    return gzip_page(lambda r: response)(request)


@cache_control(public=True, max_age=604800, immutable=True)
def cover(request, pk):
    item = get_object_or_404(Article.objects.only("cover_png", "cover_image", "status"), pk=pk)
    if item.status != "published" and not request.user.is_staff:
        raise Http404
    if item.cover_image:
        return redirect(item.cover_image.url, permanent=True)
    if not item.cover_png:
        raise Http404
    return HttpResponse(bytes(item.cover_png), content_type="image/png")


@cache_control(public=True, max_age=604800, immutable=True)
def cover_thumb(request, pk):
    item = get_object_or_404(Article.objects.only("cover_thumb", "cover_art", "cover_png", "status"), pk=pk)
    if item.status != "published" and not request.user.is_staff:
        raise Http404
    if item.cover_art:
        return redirect(item.cover_art.url, permanent=True)
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
    return _river(request, results, "news/search.html", {"q": q}, lead=False)


@public_page
def about(request):
    return render(request, "news/about.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", "")})


def robots(request):
    from news.seo import site_root
    root = site_root(request)
    body = "\n".join([
        "User-agent: *", "Allow: /",
        "Disallow: /admin/", "Disallow: /dashboard/", "Disallow: /agent/", "Disallow: /habari-admin/",
        "Disallow: /tafuta/", "",
        f"Sitemap: {root}/sitemap.xml", f"Sitemap: {root}/news-sitemap.xml",
    ])
    return HttpResponse(body + "\n", content_type="text/plain")


def favicon(request):
    """Google and browsers ask for /favicon.ico at the site root."""
    path = finders.find("news/brand/favicon.ico")
    response = FileResponse(open(path, "rb"), content_type="image/x-icon")
    response["Cache-Control"] = "public, max-age=604800"
    return response


def webmanifest(request):
    icons = [
        {"src": static("news/brand/icon-192.png"), "sizes": "192x192", "type": "image/png"},
        {"src": static("news/brand/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        {"src": static("news/brand/maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    response = JsonResponse({
        "name": "wILife — Habari, Matukio, Ratiba, Maisha Yako", "short_name": "wILife",
        "start_url": "/", "display": "standalone", "lang": "sw",
        "background_color": "#000000", "theme_color": "#0f1a3a", "icons": icons,
    })
    response["Content-Type"] = "application/manifest+json"
    response["Cache-Control"] = "public, max-age=86400"
    return response


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
        if action == "publish_batch" and not batch.articles.filter(status="draft").exists():
            messages.warning(request, "Hakuna rasimu za kuchapisha bado.")
        elif action == "publish_batch":
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
            _settle_request(item, "published")
            messages.success(request, f"Imechapishwa: {item.title}")
        else:
            item.status = "rejected"
            item.save(update_fields=["status", "updated_at"])
            _settle_request(item, "failed", "Imekataliwa na mhariri")
            messages.info(request, f"Imekataliwa: {item.title}")
    elif action == "generate":
        import time
        result = pipeline.run(force=True, deadline=time.monotonic() + 45)
        messages.info(request, f"Rasimu {result['sent']} zimeandikwa. {result.get('detail', '')}")
    if request.POST.get("next") in ("commission", "promo"):
        return redirect(f"news:{request.POST['next']}")
    return redirect("news:review")


def _settle_request(article, status, error=""):
    """Habari iliyoagizwa ikichapishwa/kukataliwa kwenye ukurasa, code yake ya WhatsApp inafungwa."""
    from news.models import NewsRequest
    for req in NewsRequest.objects.filter(article=article).select_related("approval"):
        req.status = status
        if error:
            req.error = error
        req.save(update_fields=["status", "error"])
        if req.approval and req.approval.status == "pending":
            req.approval.status = "sent" if status == "published" else "rejected"
            req.approval.result = "Imeamuliwa kwenye ukurasa wa mhariri"
            req.approval.save(update_fields=["status", "result"])


MAX_WATCH_HOURS = 7 * 24
WATCH_CHOICES = [(2, "Saa 2"), (4, "Saa 4"), (6, "Saa 6"), (12, "Saa 12"), (24, "Siku 1"),
                 (48, "Siku 2"), (72, "Siku 3"), (120, "Siku 5"), (168, "Siku 7")]


def _local_datetime(raw):
    """'2026-10-10T20:00' kutoka <input type=datetime-local> -> saa ya Dar es Salaam."""
    from datetime import datetime
    try:
        naive = datetime.strptime((raw or "").strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return timezone.make_aware(naive, timezone.get_current_timezone())


@staff_member_required
def commission(request):
    """Agiza habari + mwongozo wa mhariri kwa AI."""
    from news import commission as service
    from news.models import EditorSettings, NewsRequest

    cfg = EditorSettings.load()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "settings":
            cfg.focus_topics = request.POST.get("focus_topics", "").strip()[:2000]
            cfg.avoid_topics = request.POST.get("avoid_topics", "").strip()[:2000]
            cfg.editor_notes = request.POST.get("editor_notes", "").strip()[:3000]
            cfg.sports_slot = bool(request.POST.get("sports_slot"))
            cfg.save()
            messages.success(request, "Mwongozo wa mhariri umehifadhiwa.")
        elif action == "commission":
            topic = request.POST.get("topic", "").strip()[:300]
            category = request.POST.get("category") or ""
            if not topic:
                messages.warning(request, "Andika mada ya habari.")
            elif not getattr(settings, "GROQ_API_KEY", ""):
                messages.warning(request, "GROQ_API_KEY haijawekwa — AI haiwezi kuandika.")
            else:
                req = NewsRequest(
                    topic=topic, instructions=request.POST.get("instructions", "").strip()[:1500],
                    category=category if category in Category.values else service.guess_category(topic),
                    publish_now=bool(request.POST.get("publish_now")), via="web", requested_by=request.user)
                when = _local_datetime(request.POST.get("start_at"))
                try:
                    req.watch_hours = max(2, min(MAX_WATCH_HOURS, int(request.POST.get("watch_hours") or 6)))
                except ValueError:
                    req.watch_hours = 6
                if when and when > timezone.now() + timedelta(minutes=1):
                    service.schedule(req, when).save()
                    local = timezone.localtime(when)
                    messages.success(request, f"⏰ “{topic}” — AI itaanza saa {local:%H:%M}, {local:%d/%m/%Y} "
                                              f"na kufuatilia kwa {service.watch_label(req.watch_hours)}. "
                                              "Utapata rasimu WhatsApp/Telegram.")
                else:
                    req.save()
                    service.start(req)
                    messages.info(request, f"Ninatafuta vyanzo na kuandika: “{topic}”. Ukurasa utajisasisha.")
        elif action == "retry":
            req = NewsRequest.objects.filter(pk=request.POST.get("request"), status="failed").first()
            if req and req.article_id is None:
                NewsRequest.objects.filter(pk=req.pk).update(status="queued", error="", finished_at=None)
                service.start(req)
                messages.info(request, f"Ninajaribu tena: “{req.topic}”.")
        elif action == "cancel":
            n = NewsRequest.objects.filter(pk=request.POST.get("request"), status__in=("scheduled", "watching")) \
                .update(status="failed", error="Imesitishwa na mhariri", next_try_at=None, finished_at=timezone.now())
            if n:
                messages.info(request, "Habari iliyopangwa imesitishwa.")
        return redirect("news:commission")

    requests_ = list(NewsRequest.objects.select_related("article", "approval")[:20])
    return render(request, "news/commission.html", {
        "cfg": cfg, "requests": requests_,
        "busy": any(r.status in ("queued", "working") for r in requests_),
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "watch_choices": WATCH_CHOICES,
        "categories": [c for c in Category.choices if c[0] != Category.JAMIITEK],
        "has_key": bool(getattr(settings, "GROQ_API_KEY", "")),
    })


@staff_member_required
def promo(request):
    """Weka habari: chapisho la Instagram/Facebook -> rasimu ya kuhariri."""
    from news import promo as service

    if request.method == "POST":
        caption = request.POST.get("caption", "").strip()[:5000]
        link = request.POST.get("link", "").strip()[:500]
        account = request.POST.get("account", "").strip()[:40]
        upload = request.FILES.get("image")
        if caption and not (link and account):
            parsed, found_link, found_handle = service.parse_caption(caption)
            link, account = link or found_link, account or found_handle
        try:
            article, note = service.create_draft(
                upload.read() if upload else None, upload.name if upload else "",
                caption, link=link, account=account, category=request.POST.get("category", ""),
                rewrite=bool(request.POST.get("rewrite")), platform=request.POST.get("platform", ""))
        except ValueError as exc:
            messages.warning(request, str(exc))
            return redirect("news:promo")
        messages.success(request, f"Rasimu imeundwa: “{article.title}”. Ihariri kisha uichapishe.")
        if note:
            messages.warning(request, note)
        return redirect("news:promo")

    return render(request, "news/promo.html", {
        "drafts": Article.objects.filter(origin__in=list(service.PLATFORMS)).defer(*LIGHT).order_by("-created_at")[:20],
        "platforms": service.PLATFORMS.items(),
        "categories": [c for c in Category.choices if c[0] != Category.JAMIITEK],
        "has_key": bool(getattr(settings, "GROQ_API_KEY", "")),
    })

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
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_POST

from news import indexnow, live, pipeline
from news.writer import enabled as writer_enabled
from news.models import CATEGORY_BLURBS, Article, Category, JamiiTekService, NewsBatch

LIGHT = ("cover_png", "cover_thumb")


def _published():
    return Article.published.defer(*LIGHT)


STALE_FACTOR = 12          # nakala ya zamani inahifadhiwa hadi mara 12 ya muda wake (ukurasa wa 5 dk: saa 1)


def _render_and_store(view, request, args, kwargs, key, seconds):
    import time
    from django.db import connection
    db = {"n": 0, "t": 0.0}

    def timed(execute, sql, params, many, context):           # muda wa database (Server-Timing)
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            db["n"] += 1
            db["t"] += time.perf_counter() - start
    began = time.perf_counter()
    with connection.execute_wrapper(timed):
        response = view(request, *args, **kwargs)
        if hasattr(response, "render") and not getattr(response, "is_rendered", True):
            response.render()
    # Inaonekana kwenye DevTools -> Network -> Timing: kinachochelewesha ukurasa (database au kutengeneza)
    response["Server-Timing"] = (f'db;dur={db["t"] * 1000:.0f};desc="{db["n"]} queries", '
                                 f'render;dur={(time.perf_counter() - began) * 1000:.0f}')
    if response.status_code == 200 and not response.streaming and not response.cookies:
        from django.core.cache import cache
        cache.set(key, (response.content, response.get("Content-Type", "text/html"), timezone.now().timestamp()),
                  seconds * STALE_FACTOR)
    return response


def _refresh_later(view, request, args, kwargs, key, seconds):
    """Tengeneza nakala mpya baada ya kumjibu mgeni (thread) — mgeni hasubiri ukurasa kutengenezwa."""
    import threading
    from django.db import connections

    background = getattr(settings, "PAGE_CACHE_BACKGROUND", True)

    def run():
        try:
            _render_and_store(view, request, args, kwargs, key, seconds)
        except Exception as exc:                       # nakala ya zamani inabaki; jaribio lijalo litafanya kazi
            import logging
            logging.getLogger("news.cache").warning("page refresh %s failed: %s", request.path, exc)
        finally:
            # Funga muunganisho wa thread hii SASA: close_old_connections() inafunga tu uliozeeka (CONN_MAX_AGE),
            # hivyo kila thread ingeacha muunganisho wazi hadi database ikatae mipya.
            if background:
                connections.close_all()
    if background:
        threading.Thread(target=run, daemon=True).start()
    else:                                              # majaribio: papo hapo
        run()


def _page_cache(view, seconds):
    """
    Nakala ya ukurasa kwa wageni wasioingia (karibu wasomaji wote), moja kwa kila URL na mwanga/giza.
    cache_page ya Django ilitenganisha nakala kwa kila cookie (Vary: Cookie) — kila mgeni mwenye cookie
    yoyote (theme, takwimu za tovuti…) alikosa nakala na kusubiri ukurasa kutengenezwa upya (~sekunde 3).

    Nakala ikipitwa na muda, mgeni anapewa nakala iliyopo papo hapo, na mpya inatengenezwa nyuma
    (stale-while-revalidate): hakuna mgeni anayesubiri sekunde 3–7 za ukurasa wa mwanzo kutengenezwa,
    isipokuwa ukurasa usiofunguliwa kabisa kwa muda mrefu (STALE_FACTOR).
    """
    import hashlib
    from django.core.cache import cache
    from django.utils.cache import patch_response_headers, patch_vary_headers

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if (request.method not in ("GET", "HEAD") or request.GET.get("preview")
                or settings.SESSION_COOKIE_NAME in request.COOKIES):         # aliyeingia (mhariri) anaona ukurasa mpya
            return view(request, *args, **kwargs)
        dark = request.COOKIES.get("theme") == "dark"                         # <html data-bs-theme> inategemea cookie hii
        key = "page:" + hashlib.sha1(f"{request.get_host()}|{request.get_full_path()}|{dark}".encode()).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            content, ctype = hit[0], hit[1]
            stored = hit[2] if len(hit) > 2 else 0
            if timezone.now().timestamp() - stored > seconds and cache.add(key + ":refresh", 1, 60):
                _refresh_later(view, request, args, kwargs, key, seconds)   # mmoja tu anaanzisha kutengeneza upya
            response = HttpResponse(content, content_type=ctype)
            response["Server-Timing"] = 'cache;desc="hit"'
        else:
            response = _render_and_store(view, request, args, kwargs, key, seconds)
        patch_response_headers(response, seconds)
        patch_vary_headers(response, ("Cookie",))          # kivinjari: mwanga/giza ukibadilika, kiombe upya
        return response
    return wrapper


def public_page(view):
    """gzip always; cache the rendered page for anonymous visitors (who are almost all traffic)."""
    return gzip_page(_page_cache(view, getattr(settings, "NEWS_CACHE_SECONDS", 300)))


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


# Mada yenye habari chache ni nakala ya kadi zilizo kwenye makala yenyewe: Google
# inaiona ni "thin content" na inashusha imani yake kwa tovuti nzima. Mada inaingia
# kwenye sitemap na kuruhusiwa kuorodheshwa ikiwa na habari angalau hizi tu.
TOPIC_MIN_ARTICLES = 3


def _topics(limit=14, min_count=1):
    counts, names = Counter(), {}
    for kw in _published().values_list("keywords", flat=True)[:120]:
        for raw in kw.split(","):
            tag = raw.strip()
            key = slugify(tag)
            if tag and key and len(tag) <= 30:
                counts[key] += 1
                names.setdefault(key, tag)
    return [(names[k], k) for k, n in counts.most_common(limit) if n >= min_count]


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
        "live_strip": _live_strip(),
        "videos": _home_videos(),
        "live_v": _live_version(*_live_sets()),
    })


def _home_videos():
    from news.models import Highlight
    try:
        return list(Highlight.objects.all()[:4])
    except Exception:                 # jedwali bado (migration) — ukurasa wa mwanzo usivunjike
        return []


def _live_version(on, soon, done):
    """Alama ya orodha ya LIVE: mechi mpya ikianza/kuisha, ukurasa wa LIVE na wa mwanzo vinajibadilisha."""
    import hashlib
    key = [(a.pk, getattr(getattr(a, "match", None), "status", ""), a.is_live) for a in on + soon] + [a.pk for a in done[:8]]
    return hashlib.md5(repr(key).encode()).hexdigest()[:12]


def _live_strip():
    on, soon, done = _live_sets()
    items = on + soon
    return items[:10] if items else done[:4]


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
    return _river(request, matches, "news/topic.html", {
        "name": name, "slug": slug, "thin": len(matches) < TOPIC_MIN_ARTICLES})


def article(request, category, slug):
    qs = Article.objects.defer(*LIGHT) if request.user.is_staff else _published()
    item = get_object_or_404(qs, category=category, slug=slug)
    views = item.views
    if item.status == "published" and not request.user.is_staff and "_" not in request.GET:   # '_' = ukurasa unajibadilisha
        Article.objects.filter(pk=item.pk).update(views=F("views") + 1)
        views += 1
    related = list(_published().select_related("match").filter(category=item.category).exclude(pk=item.pk)[:4])
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
        "updates": list(item.live_updates.all()[:live.MAX_UPDATES]) if item.live_started_at else [],
        "now": timezone.now(),
        "page_v": live.version(item) if (item.live_started_at or hasattr(item, "match")) else "",
        "page_active": _page_active(item),
        "other_matches": _other_matches(item) if hasattr(item, "match") else [],
    })
    return gzip_page(lambda r: response)(request)


def _live_page(view):
    """Ukurasa wa LIVE: gzip, na nakala ya sekunde 30 kwa wageni (matokeo yanabadilika haraka)."""
    return gzip_page(_page_cache(view, 30))


def _live_sets(now=None):
    """(zinaendelea sasa, zinakuja, zilizoisha) kwa ukurasa wa LIVE na ukurasa wa mwanzo."""
    now = now or timezone.now()
    base = _published().select_related("match")
    open_ = list(base.filter(live_started_at__isnull=False, live_ended_at__isnull=True).order_by("-live_started_at")[:30])
    ended = lambda a: hasattr(a, "match") and (a.match.is_finished or a.match.status in a.match.OFF)
    # mechi isiyo na matokeo ya mwisho, au iliyokwisha ambayo LIVE yake haijafungwa bado, si "LIVE sasa"
    on = [a for a in open_ if not (hasattr(a, "match") and a.match.is_stale) and not ended(a)]
    soon = list(base.filter(live_started_at__isnull=True, match__status="NS",
                            match__kickoff__gte=now - timedelta(hours=1), match__kickoff__lte=now + timedelta(hours=36))
                .order_by("match__kickoff")[:30])
    done = list(base.filter(live_ended_at__isnull=False, live_ended_at__gte=now - timedelta(days=7))
                .order_by("-live_ended_at")[:24])
    done += [a for a in open_ if ended(a) and a.match.kickoff and a.match.kickoff >= now - timedelta(days=7)]
    return on, soon, done


def _by_day(items):
    """[(siku, [makala…])] mpya kwanza — matokeo ya siku 7 yanapangwa kwa siku (Leo, Jana, …), si mchanganyiko."""
    days = {}
    for a in items:
        when = a.match.kickoff or a.live_ended_at
        days.setdefault(timezone.localtime(when).date() if when else timezone.localdate(), []).append(a)
    return [(day, sorted(rows, key=lambda a: a.match.kickoff or a.live_ended_at, reverse=True))
            for day, rows in sorted(days.items(), reverse=True)]


@_live_page
def live_hub(request):
    on, soon, done = _live_sets()
    # Matokeo ni ya mechi zilizoisha (au kuahirishwa) tu; LIVE iliyofungwa bila matokeo ya mwisho si "matokeo",
    # na habari za LIVE zisizo za mechi (mf. Bunge) zina sehemu yao — hazichanganywi na ubao wa matokeo.
    results = [a for a in done if hasattr(a, "match") and (a.match.is_finished or a.match.status in a.match.OFF)]
    return render(request, "news/live_hub.html", {
        "on_matches": [a for a in on if hasattr(a, "match")],
        "on_other": [a for a in on if not hasattr(a, "match")],
        "soon": soon,
        "done": done,
        "result_days": _by_day(results),
        "done_other": [a for a in done if not hasattr(a, "match")],
        "live_count": len(on),
        "live_v": _live_version(on, soon, done),
    })


def _day_version(rows):
    """Alama ya ukurasa wa siku: matokeo/hali ikibadilika, ukurasa unajibadilisha bila kurefresh."""
    import hashlib
    key = [(r.key, r.status, r.home_goals, r.away_goals, r.minute) for r in rows]
    return hashlib.md5(repr(key).encode()).hexdigest()[:12]


def _day_page(request, day, kind):
    """Mechi za siku moja: ratiba, matokeo (LIVE ikiendelea) na makadirio, kwa ligi."""
    from news import fixtures, predict
    from news.pipeline import swahili_date
    today = timezone.localdate()
    rows = fixtures.day_rows(day)
    if day < today:              # siku iliyopita: matokeo ya mwisho tu — mechi isiyo na matokeo haionyeshwi
        rows = [r for r in rows if r.is_finished or r.is_off]
    if kind == "siku" and not rows:
        raise Http404
    counts = {"on": sum(r.is_playing for r in rows), "done": sum(r.is_finished for r in rows),
              "soon": sum(r.status == "NS" for r in rows)}
    canonical = {today: "news:matches_today", today - timedelta(days=1): "news:results_yesterday",
                 today + timedelta(days=1): "news:matches_tomorrow"}.get(day)
    groups = fixtures.groups(rows)
    names = [name for name, _, _ in groups]
    leagues = ", ".join(names[:5])
    if len(names) > 1:                       # "A, B na C" — ligi zilizochezwa kweli siku hiyo
        leagues = ", ".join(names[:min(len(names), 5) - 1]) + " na " + names[min(len(names), 5) - 1]
    if len(names) > 5:
        leagues += " pamoja na mashindano mengine"
    return render(request, "news/day.html", {
        "kind": kind, "day": day, "date_text": swahili_date(day), "rows": rows, "groups": groups, "leagues": leagues,
        "has_pages": any(r.article for r in rows),
        "counts": counts, "is_today": day == today, "is_past": day < today,
        "has_tomorrow": day != today + timedelta(days=1) and fixtures.has_day(today + timedelta(days=1)),
        "prev_day": day - timedelta(days=1) if fixtures.has_day(day - timedelta(days=1)) else None,   # siku isiyo na data haina kitufe
        "next_day": day + timedelta(days=1) if day + timedelta(days=2) < today else None,   # jana/leo zina vitufe vyao
        "canonical": reverse(canonical) if canonical else request.path,
        "page_v": _day_version(rows), "has_live": bool(counts["on"]),
        "record": predict.record(30),
    })


@_live_page
def matches_today(request):
    return _day_page(request, timezone.localdate(), "leo")


@_live_page
def matches_tomorrow(request):
    return _day_page(request, timezone.localdate() + timedelta(days=1), "kesho")


@_live_page
def results_yesterday(request):
    return _day_page(request, timezone.localdate() - timedelta(days=1), "jana")


@_live_page
def matches_day(request, day):
    from datetime import date
    try:
        when = date.fromisoformat(day)
    except ValueError:
        raise Http404
    today = timezone.localdate()
    if when > today + timedelta(days=1) or when < today - timedelta(days=400):
        raise Http404
    return _day_page(request, when, "siku")


@_live_page
def fixture_detail(request, day, pk, slug):
    """Mechi ya ratiba isiyo na LIVE yake: matokeo, matukio na takwimu (zinaombwa msomaji akifungua)."""
    from news import fixtures
    from news.models import Fixture, LiveMatch
    from news.pipeline import swahili_date
    f = get_object_or_404(Fixture, pk=pk)
    m = LiveMatch.objects.select_related("article").filter(provider=f.provider, fixture_id=f.fixture_id,
                                                           article__status="published").first()
    if m:                                                     # mechi ina LIVE kamili: ukurasa wake
        return redirect(m.article.get_absolute_url())
    if request.path != f.get_absolute_url():
        return redirect(f.get_absolute_url(), permanent=True)
    fixtures.refresh_detail(f)
    others = [r for r in fixtures.day_rows(f.day) if r.key != f"{f.provider}{f.fixture_id}"][:8]
    from news import leagues
    pre = leagues.preview(f) if f.status == "NS" else {}
    return render(request, "news/fixture.html", {"f": f, "x": f.extra or {}, "others": others, "now": timezone.now(),
                                                 "pre": pre,
                                                 "date_text": swahili_date(f.day)})


@_live_page
def league_hub(request):
    """Ligi zote zenye kurasa zao."""
    from news.models import League
    return render(request, "news/leagues.html", {"leagues": list(League.objects.filter(active=True))})


@_live_page
def league_detail(request, slug):
    """Ukurasa wa ligi: msimamo, mechi za leo/kesho, matokeo, habari na video — vyote automatic."""
    from news import leagues
    from news.models import League
    league = get_object_or_404(League, slug=slug, active=True)
    return render(request, "news/league.html", {**leagues.summary(league), "year": timezone.localdate().year})


@_live_page
def team_hub(request):
    """Timu zenye kurasa zao."""
    from news.models import Team
    teams = list(Team.objects.filter(active=True))
    return render(request, "news/teams.html", {"teams": teams})


@_live_page
def team_detail(request, slug):
    """Ukurasa wa timu: habari, mechi ijayo/LIVE, matokeo, fomu, takwimu na video — vyote automatic."""
    from news import teams
    from news.models import Team
    team = get_object_or_404(Team, slug=slug, active=True)
    return render(request, "news/team.html", teams.summary(team))


def video_hub(request):
    """Video za magoli na highlights za karibuni (ScoreBat) — kwa ligi."""
    from django.db.models import Count
    from news.models import Highlight
    league = (request.GET.get("ligi") or "").strip()[:120]
    qs = Highlight.objects.all()
    leagues = list(qs.values("competition").exclude(competition="").annotate(n=Count("id")).order_by("-n")[:12])
    if league:
        qs = qs.filter(competition=league)
    items = list(qs[:48])
    for row in leagues:                       # 'ENGLAND: Premier League' -> 'Premier League · England'
        row["label"] = Highlight(competition=row["competition"]).league
    return render(request, "news/videos.html", {"items": items, "first": items[0] if items else None,
                                                "rest": items[1:], "leagues": leagues, "league": league,
                                                "league_label": Highlight(competition=league).league})


def video_detail(request, slug):
    """Video za magoli za mechi moja, na kiungo cha ukurasa wake wa matokeo."""
    from news import scorebat
    from news.models import Highlight
    h = get_object_or_404(Highlight, slug=slug)
    more = list(Highlight.objects.exclude(pk=h.pk).filter(competition=h.competition)[:6]) if h.competition else []
    if len(more) < 6:
        more += list(Highlight.objects.exclude(pk__in=[h.pk] + [x.pk for x in more])[:6 - len(more)])
    match = scorebat.match_page(h)
    return render(request, "news/video.html", {"h": h, "match": match, "more": more,
                                               "seo_title": scorebat.title(h, match),
                                               "seo_desc": scorebat.description(h, match)})


@_live_page
def predictions(request):
    """Makadirio ya mechi zijazo + rekodi ya wazi ya yalivyopatia."""
    from news import predict
    upcoming = predict.upcoming()
    return render(request, "news/predictions.html", {
        "upcoming": upcoming, "week": predict.record(7), "record": predict.record(30),
        "page_v": _day_version([]) + str(len(upcoming)),
    })


def _client_ip(request):
    return (request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0]
            or request.META.get("REMOTE_ADDR", "")).strip()


@csrf_exempt     # fomu iko kwenye kurasa zilizohifadhiwa (cache); usalama ni uthibitisho wa email (double opt-in)
@never_cache
def digest_join(request):
    """Jiunge na ujumbe wa michezo kwa email: fomu -> link ya kuthibitisha."""
    from django.core.cache import cache
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from news import digest
    state, email = "", ""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()[:254]
        ip_key = f"digest-join:{_client_ip(request)}"
        if request.POST.get("website"):                    # mtego wa roboti (sehemu iliyofichwa)
            state = "sent"
        elif not digest.email_ready():
            state = "off"
        elif (cache.get(ip_key) or 0) >= 5:
            state = "slow"
        else:
            try:
                validate_email(email)
            except ValidationError:
                state = "invalid"
            else:
                cache.set(ip_key, (cache.get(ip_key) or 0) + 1, 3600)
                state = digest.subscribe_email(email)[0]
    response = render(request, "news/digest_join.html", {
        "state": state, "email": email, "email_ready": digest.email_ready(), "channel": digest.channel_link()})
    response["X-Robots-Tag"] = "noindex" if state else "index"
    return response


@never_cache
def digest_confirm(request, token):
    from news import digest
    sub = digest.confirm_email(token)
    if not sub:
        raise Http404
    response = render(request, "news/digest_join.html", {"state": "confirmed", "email": sub.email,
                                                         "channel": digest.channel_link(),
                                                         "email_ready": digest.email_ready()})
    response["X-Robots-Tag"] = "noindex"
    return response


@csrf_exempt     # kujiondoa kwa kubofya mara moja (List-Unsubscribe-Post) hakuna csrf token
@never_cache
def digest_leave(request, token):
    from news import digest
    from news.models import EmailSubscriber
    sub = EmailSubscriber.objects.filter(token=token).first()
    if not sub:
        raise Http404
    if request.method == "POST":           # GET haiondoi: programu za email hufungua link zenyewe
        digest.unsubscribe_email(token)
        sub.refresh_from_db()
    response = render(request, "news/digest_join.html", {"state": "left" if not sub.active else "leave",
                                                         "email": sub.email, "token": token,
                                                         "email_ready": digest.email_ready()})
    response["X-Robots-Tag"] = "noindex"
    return response


def _page_active(item):
    """Je, ukurasa uendelee kuuliza seva? LIVE inaendelea, mechi ya karibuni, au video zinasubiriwa."""
    now = timezone.now()
    if item.is_live:
        return True
    m = getattr(item, "match", None)
    if not m or not m.kickoff:
        return False
    if m.is_finished or m.status in m.OFF:                   # video za magoli huja hadi saa ~3 baadaye
        return bool(item.live_ended_at and now - item.live_ended_at < timedelta(hours=4))
    return now - timedelta(hours=4) < m.kickoff < now + timedelta(hours=24)


def _other_matches(item):
    """Mechi nyingine za leo (zinazoendelea, zinazokuja, zilizoisha leo) — viungo vya ndani na wasomaji wabaki.
    Matokeo ya siku zilizopita hayaingii: sehemu inaitwa "Mechi nyingine za leo"."""
    on, soon, done = _live_sets()
    today = timezone.localdate()
    done = [a for a in done if hasattr(a, "match") and a.match.kickoff and timezone.localtime(a.match.kickoff).date() == today]
    return [a for a in on + soon + done if a.pk != item.pk and hasattr(a, "match")][:6]


@never_cache
def live_scores(request):
    """Matokeo ya sasa ya mechi zote za LIVE na zinazokuja — ukurasa wa LIVE na wa mwanzo unauliza kila sekunde 30."""
    on, soon, done = _live_sets()
    return JsonResponse({"matches": [a.match.as_json() for a in on + soon if hasattr(a, "match")],
                         "live": len(on), "v": _live_version(on, soon, done)})


@never_cache
def live_feed(request, pk):
    """Masasisho mapya ya habari ya LIVE (ukurasa unauliza kila sekunde 30)."""
    item = get_object_or_404(Article.objects.defer(*LIGHT).filter(Q(live_started_at__isnull=False) | Q(match__isnull=False)),
                             pk=pk, status="published")
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    return JsonResponse(live.feed(item, after))


@staff_member_required
@require_POST
def live_action(request, pk):
    """Mhariri: anza LIVE, ongeza sasisho, badilisha matokeo, maliza LIVE."""
    item = get_object_or_404(Article.objects.defer(*LIGHT), pk=pk)
    action = request.POST.get("action")
    if action == "start":
        live.start(item)
        messages.success(request, "LIVE imeanza. Ongeza masasisho hapa chini.")
    elif action == "end":
        live.end(item)
        messages.success(request, "LIVE imeisha. Masasisho yanabaki kwenye makala.")
    elif action == "delete":
        live.remove(item)
        messages.success(request, f"“{item.title}” imeondolewa kwenye tovuti.")
        nxt = request.POST.get("next", "")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "news:live_hub")
    elif action == "delete_update":
        update = item.live_updates.filter(pk=request.POST.get("update")).first()
        if update:
            live.delete_update(update)
            messages.success(request, "Sasisho limefutwa.")
    elif action == "update":
        text = request.POST.get("text", "").strip()
        score = request.POST.get("score")
        if score is not None and score.strip() == item.live_score:
            score = None                  # matokeo hayakubadilika
        if not text and score is None:
            messages.error(request, "Andika sasisho.")
        elif not item.live_started_at:
            messages.error(request, "Anza LIVE kwanza.")
        else:
            if text:
                live.add(item, text, request.POST.get("label", ""), score=score)
            else:
                live.add_score(item, score)
            messages.success(request, "Sasisho limewekwa.")
    return redirect(item.get_absolute_url() + "#live")


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
    from news.models import Author
    return render(request, "news/about.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", ""),
                                               "authors": list(Author.objects.filter(active=True))})


@public_page
def editorial_policy(request):
    """Sera ya uhariri kwa undani (Google: publishingPrinciples / ethicsPolicy)."""
    return render(request, "news/policy.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", "")})


@public_page
def corrections(request):
    """Sera ya masahihisho na habari zilizosahihishwa (Google: correctionsPolicy)."""
    fixed = list(_published().exclude(correction="").order_by("-updated_at")[:30])
    return render(request, "news/corrections.html", {"contact_email": getattr(settings, "JAMIITEK_EMAIL", ""),
                                                     "fixed": fixed})


@public_page
def author_detail(request, slug):
    """Ukurasa wa mhariri: wasifu wake na habari alizozihakiki."""
    from news.models import Author
    author = get_object_or_404(Author, slug=slug, active=True)
    articles = list(_published().filter(author=author).order_by("-published_at")[:24])
    return render(request, "news/author.html", {"author": author, "articles": articles})


def robots(request):
    from news.seo import site_root
    root = site_root(request)
    body = "\n".join([
        "User-agent: *", "Allow: /",
        "Disallow: /admin/", "Disallow: /dashboard/", "Disallow: /agent/", "Disallow: /habari-admin/",
        "Disallow: /tafuta/", "",
        f"Sitemap: {root}/sitemap.xml", f"Sitemap: {root}/news-sitemap.xml", f"Sitemap: {root}/video-sitemap.xml",
    ])
    return HttpResponse(body + "\n", content_type="text/plain")


def video_sitemap(request):
    """Google Video sitemap: kila ukurasa wa video na player wake, picha, kichwa na tarehe."""
    from xml.sax.saxutils import escape
    from news import scorebat
    from news.models import Highlight
    from news.seo import site_root
    root = site_root(request)
    rows = []
    for h in Highlight.objects.order_by("-date")[:1000]:
        v = h.main
        if not (v and h.thumbnail):
            continue                                  # Google inahitaji picha ya video
        desc = scorebat.description(h)
        tags = "".join(f"<video:tag>{escape(k)}</video:tag>" for k in h.keywords[:8] if k)
        rows.append(
            f"<url><loc>{escape(root + h.get_absolute_url())}</loc><video:video>"
            f"<video:thumbnail_loc>{escape(h.thumbnail)}</video:thumbnail_loc>"
            f"<video:title>{escape(scorebat.title(h)[:100])}</video:title>"
            f"<video:description>{escape(desc[:2048])}</video:description>"
            f"<video:player_loc>{escape(v['src'])}</video:player_loc>"
            f"<video:publication_date>{h.date.isoformat()}</video:publication_date>"
            + (f"<video:duration>{h.duration}</video:duration>" if h.duration and h.duration <= 28800 else "")
            + f"<video:family_friendly>yes</video:family_friendly>{tags}"
            f"</video:video></url>")
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">' + "".join(rows) + "</urlset>")
    return HttpResponse(body, content_type="application/xml")


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
    items = list(_published().filter(published_at__gte=since)[:1000])
    # An empty <urlset> is an error in Search Console ("Missing XML tag: url"), so on a quiet
    # 48 hours the newest article stays listed; Google News simply ignores it, it is not an error.
    if not items:
        items = list(_published()[:1])
    return render(request, "news/news_sitemap.xml", {"items": items}, content_type="application/xml")


# ---------------------------------------------------------------------------
# Staff review
# ---------------------------------------------------------------------------

@staff_member_required
def review(request):
    return render(request, "news/review.html", {
        "batches": NewsBatch.objects.prefetch_related("articles")[:10],
        "enabled": getattr(settings, "NEWS_ENABLED", False),
        "has_key": writer_enabled(),
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
            _sign(batch.articles.filter(status="published", author=None), request.user)
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
            _sign(Article.objects.filter(pk=item.pk, author=None), request.user)
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


def _sign(articles, user):
    """Habari zilizochapishwa zipate jina la mhariri aliyezihakiki (kama ana ukurasa wa Author)."""
    from news.models import Author
    author = Author.objects.filter(user=user, active=True).first() if getattr(user, "is_authenticated", False) else None
    if author:
        articles.update(author=author)


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


def _ai_models(cfg):
    from core import llm
    return [{"value": v, "name": n, "note": note, "on": v == cfg.ai_model,
             "ready": llm.provider_ready(v.split(":", 1)[0]),
             "key": "GEMINI_API_KEY" if v.startswith("gemini") else "GROQ_API_KEY"}
            for v, n, note in llm.MODELS]


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
        elif action == "ai_model":
            from core import llm
            choice = request.POST.get("ai_model", "")
            if choice in {m[0] for m in llm.MODELS}:
                cfg.ai_model = choice
                cfg.save(update_fields=["ai_model", "updated_at"])
                provider = choice.split(":", 1)[0]
                if llm.provider_ready(provider):
                    messages.success(request, f"AI sasa ni {llm.label(choice)}.")
                else:
                    key = "GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY"
                    messages.warning(request, f"{llm.label(choice)} imechaguliwa, lakini {key} haijawekwa — "
                                              "mfumo utatumia mtoa huduma mwingine mpaka uiweke.")
        elif action == "ai_test":
            from core import llm
            choice = request.POST.get("ai_model") or cfg.ai_model
            ok, text, secs = llm.test(choice)
            (messages.success if ok else messages.error)(
                request, f"{llm.label(choice)} ({secs}s): {text}")
        elif action == "commission":
            topic = request.POST.get("topic", "").strip()[:300]
            category = request.POST.get("category") or ""
            if not topic:
                messages.warning(request, "Andika mada ya habari.")
            elif not writer_enabled():
                messages.warning(request, "Hakuna AI iliyowekwa (GEMINI_API_KEY au GROQ_API_KEY).")
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
        "ai_models": _ai_models(cfg),
        "categories": [c for c in Category.choices if c[0] != Category.JAMIITEK],
        "has_key": writer_enabled(),
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
        "has_key": writer_enabled(),
    })

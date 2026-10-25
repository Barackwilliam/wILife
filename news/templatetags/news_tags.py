import json

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import escape
from django.utils.safestring import mark_safe

from news.pipeline import swahili_date

register = template.Library()


def _abs(request, path):
    from news.seo import absolute
    return absolute(request, path)


@register.filter
def render_body(text, links=""):
    """
    Paragraphs / '## ' subheadings / '- ' lists → safe HTML. Everything is escaped first.

    With `|render_body:"jamiitek"`, the first mention of each JamiiTek product
    (JamiiBot, web builder, templates, domain, JamiiTek) links to its page on
    jamiitek.com.
    """
    from news.jamiitek_links import Linker
    out, bullets = [], []
    link = Linker() if links else escape

    def flush():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for block in (text or "").replace("\r\n", "\n").split("\n"):
        line = block.strip()
        if not line:
            flush()
            continue
        if line.startswith(("- ", "• ", "* ")):
            bullets.append(link(line[2:].strip()))
            continue
        flush()
        if line.startswith("## "):
            out.append(f"<h2>{escape(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            out.append(f"<h2>{escape(line[2:].strip())}</h2>")
        else:
            out.append(f"<p>{link(line)}</p>")
    flush()
    return mark_safe("\n".join(out))


@register.filter
def sw_date(value):
    if not value:
        return ""
    try:
        from django.utils import timezone
        value = timezone.localtime(value).date() if hasattr(value, "hour") else value
    except (ValueError, TypeError):
        pass
    return swahili_date(value)


@register.simple_tag(takes_context=True)
def absolute(context, path):
    from news.seo import absolute as _absolute
    return _absolute(context.get("request"), path)


@register.simple_tag(takes_context=True)
def abs_url(context, url):
    """Absolute URL for a path or an already-absolute (bucket) URL."""
    from news.seo import absolute as _absolute
    return _absolute(context.get("request"), url)


@register.simple_tag(takes_context=True)
def article_jsonld(context, article):
    request = context["request"]
    url = _abs(request, article.get_absolute_url())
    data = [{
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": article.title[:110],
        "description": article.excerpt,
        "inLanguage": "sw",
        "mainEntityOfPage": url,
        "url": url,
        "image": [{"@type": "ImageObject", "url": abs_url(context, article.og_url),
                   "width": 1200, "height": 630}],
        "wordCount": len(article.body.split()),
        "datePublished": article.published_at.isoformat() if article.published_at else None,
        "dateModified": article.updated_at.isoformat(),
        "articleSection": article.get_category_display(),
        "keywords": article.keywords,
        "author": {"@type": "Organization", "name": "Dawati la Habari, wILife", "url": _abs(request, "/kuhusu/")},
        "publisher": {"@type": "Organization", "name": "wILife",
                      "logo": {"@type": "ImageObject", "url": _abs(request, static("news/brand/logo-600.png")),
                               "width": 600, "height": 466}},
        "isBasedOn": [s.get("url") for s in article.sources if s.get("url", "").startswith("http")] or None,
    }]
    if article.live_started_at:
        # Habari ya LIVE (news/live.py): Google inaweza kuipa alama ya "LIVE" kwenye Top stories
        updates = list(article.live_updates.all()[:50])
        data[0].update({
            "@type": "LiveBlogPosting",
            "coverageStartTime": article.live_started_at.isoformat(),
            "coverageEndTime": article.live_ended_at.isoformat() if article.live_ended_at else None,
            "liveBlogUpdate": [{
                "@type": "BlogPosting",
                "headline": (f"{u.label} " if u.label else "") + u.text[:100],
                "articleBody": u.text,
                "datePublished": u.created_at.isoformat(),
                "url": f"{url}#u{u.id}",
            } for u in updates],
        })
        if updates:
            data[0]["dateModified"] = max(updates[0].created_at, article.updated_at).isoformat()
    match = getattr(article, "match", None)
    if match:
        data[0]["about"] = sports_event(match, url)
        data[0]["about"]["image"] = abs_url(context, article.og_url)
        data[0]["isAccessibleForFree"] = True
        videos = [{
            "@type": "VideoObject", "name": v["title"], "description": f"{match.home} vs {match.away} — {v['title']}",
            "thumbnailUrl": [v["img"]], "uploadDate": (match.kickoff or article.updated_at).isoformat(),
            **({"embedUrl": v["embed"]} if v.get("embed") else {"contentUrl": v["url"]}),
        } for v in (match.extra or {}).get("highlights") or [] if v.get("img")]
        if videos:
            data[0]["video"] = videos[:5]
    data += [{
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": _abs(request, "/")},
            {"@type": "ListItem", "position": 2, "name": article.get_category_display(),
             "item": _abs(request, f"/habari/{article.category}/")},
            {"@type": "ListItem", "position": 3, "name": article.title, "item": url},
        ],
    }]
    return _script(data)


EVENT_STATUS = {"PST": "EventPostponed", "CANC": "EventCancelled", "ABD": "EventCancelled"}


def sports_event(match, url):
    """Mechi kama `SportsEvent` (schema.org) — timu, muda, uwanja na hali."""
    def team(name, logo):
        return {"@type": "SportsTeam", "name": name, "sport": "Soccer", **({"logo": logo} if logo else {})}
    home, away = team(match.home, match.home_logo), team(match.away, match.away_logo)
    event = {
        "@type": "SportsEvent",
        "name": f"{match.home} vs {match.away}",
        "url": url,
        "sport": "Soccer",
        "eventStatus": "https://schema.org/" + EVENT_STATUS.get(match.status, "EventScheduled"),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "homeTeam": home,
        "awayTeam": away,
        "competitor": [home, away],
    }
    if match.kickoff:
        event["startDate"] = match.kickoff.isoformat()
    if match.competition:
        event["superEvent"] = {"@type": "SportsEvent", "name": match.competition}
        event["description"] = f"{match.home} dhidi ya {match.away} — {match.competition}" + (
            f", {match.round}" if match.round else "")
    if getattr(match, "venue", ""):
        event["location"] = {"@type": "Place", "name": match.venue,
                             **({"address": match.city} if getattr(match, "city", "") else {})}
    return event


def _collection(request, url, name, description, items, crumbs):
    return [{
        "@context": "https://schema.org", "@type": "CollectionPage", "name": name, "description": description,
        "url": url, "inLanguage": "sw",
        "isPartOf": {"@type": "WebSite", "name": "wILife", "url": _abs(request, "/")},
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(items), "itemListElement": items[:100]},
    }, {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": _abs(request, p)}
                            for i, (n, p) in enumerate(crumbs)],
    }]


@register.simple_tag(takes_context=True)
def day_jsonld(context, rows, kind, date_text, canonical):
    """Mechi za siku: CollectionPage + orodha ya SportsEvent (kila mechi, hata isiyo na ukurasa wake)."""
    request = context["request"]
    page = _abs(request, canonical)
    items = []
    for r in rows:
        link = _abs(request, r.url) if r.url else f"{page}#m-{r.key}"
        items.append({"@type": "ListItem", "position": len(items) + 1, "url": link, "item": sports_event(r, link)})
    name = {"leo": "Mechi za Leo", "jana": "Matokeo ya Jana"}.get(kind, "Matokeo ya Mechi") + f" — {date_text}"
    crumbs = [("Mwanzo", "/"), ("LIVE", "/live/"), (name.split(" — ")[0], canonical)]
    return _script(_collection(request, page, name, f"Ratiba na matokeo ya mechi za {date_text} kwa Kiswahili.",
                               items, crumbs))


@register.simple_tag(takes_context=True)
def predictions_jsonld(context, matches):
    request = context["request"]
    page = _abs(request, request.path)
    items = []
    for m in matches:
        link = _abs(request, m.article.get_absolute_url())
        items.append({"@type": "ListItem", "position": len(items) + 1, "url": link, "item": sports_event(m, link)})
    return _script(_collection(request, page, "Utabiri wa Mechi za Leo",
                               "Uwezekano wa matokeo ya mechi kubwa kutoka fomu na rekodi za timu.",
                               items, [("Mwanzo", "/"), ("LIVE", "/live/"), ("Utabiri", request.path)]))


@register.simple_tag(takes_context=True)
def fixture_jsonld(context, f):
    """Mechi ya ratiba: SportsEvent + breadcrumb."""
    request = context["request"]
    url = _abs(request, f.get_absolute_url())
    event = {"@context": "https://schema.org", **sports_event(f, url)}
    if f.has_score and f.is_finished:
        event["description"] = f"{f.home} {f.home_goals}-{f.away_goals} {f.away} — {f.competition}"
    day_url = f"/mechi/{f.day.isoformat()}/"
    return _script([event, {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": _abs(request, "/")},
            {"@type": "ListItem", "position": 2, "name": "Mechi za siku", "item": _abs(request, day_url)},
            {"@type": "ListItem", "position": 3, "name": f"{f.home} dhidi ya {f.away}", "item": url},
        ]}])


@register.simple_tag
def join_options():
    """Njia zilizo wazi za kupata ujumbe wa michezo: {'wa': wa.me link, 'email': bool, 'channel': link}."""
    from news import digest
    wa = wa_join_link()
    return {"wa": "" if wa and digest.whatsapp_full() else wa, "email": digest.email_ready(),
            "channel": digest.channel_link(), "channel_name": digest.channel_name()}


@register.simple_tag
def wa_join_link():
    """wa.me ya namba ya wILife (NEWS_WHATSAPP_NUMBER) yenye neno JIUNGE — tupu kama haijawekwa."""
    from core.agent.whatsapp import normalise_number
    number = normalise_number(getattr(settings, "NEWS_WHATSAPP_NUMBER", ""))
    return f"https://wa.me/{number}?text=JIUNGE" if number else ""


@register.simple_tag(takes_context=True)
def live_hub_jsonld(context, *groups):
    """Ukurasa wa LIVE: CollectionPage + orodha ya mechi na habari zake + breadcrumb."""
    request = context["request"]
    url = _abs(request, request.path)
    items, seen = [], set()
    for group in groups:
        for a in group:
            if a.pk in seen:
                continue
            seen.add(a.pk)
            link = _abs(request, a.get_absolute_url())
            match = getattr(a, "match", None)
            items.append({"@type": "ListItem", "position": len(items) + 1, "url": link,
                          **({"item": sports_event(match, link)} if match else {"name": a.title})})
    data = [{
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Matokeo ya soka leo — mechi zinazoendelea",
        "description": "Matokeo ya soka ya leo na mechi zinazoendelea sasa kwa Kiswahili.",
        "url": url,
        "inLanguage": "sw",
        "isPartOf": {"@type": "WebSite", "name": "wILife", "url": _abs(request, "/")},
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(items), "itemListElement": items[:50]},
    }, {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": _abs(request, "/")},
            {"@type": "ListItem", "position": 2, "name": "LIVE", "item": url},
        ],
    }]
    return _script(data)


@register.simple_tag(takes_context=True)
def site_jsonld(context):
    request = context["request"]
    root = _abs(request, "/")
    data = [{
        "@context": "https://schema.org", "@type": "NewsMediaOrganization", "name": "wILife",
        "url": root, "logo": {"@type": "ImageObject", "url": _abs(request, static("news/brand/icon-512.png")),
                              "width": 512, "height": 512},
        "image": _abs(request, static("news/brand/og-default.jpg")),
        "parentOrganization": {"@type": "Organization", "name": "JamiiTek Digital Agency"},
        "sameAs": getattr(settings, "SOCIAL_LINKS", []) or None,
        "publishingPrinciples": _abs(request, "/kuhusu/"),
        "areaServed": "TZ", "knowsLanguage": "sw",
    }, {
        "@context": "https://schema.org", "@type": "WebSite", "name": "wILife", "url": root,
        "inLanguage": "sw",
        "potentialAction": {"@type": "SearchAction", "target": root + "tafuta/?q={search_term_string}",
                            "query-input": "required name=search_term_string"},
    }]
    return _script(data)


@register.simple_tag(takes_context=True)
def service_jsonld(context, service):
    request = context["request"]
    data = {
        "@context": "https://schema.org", "@type": "Service", "name": service.name,
        "description": service.tagline, "areaServed": "TZ",
        "url": _abs(request, service.get_absolute_url()),
        "provider": {"@type": "Organization", "name": "JamiiTek Digital Agency"},
    }
    return _script(data)


def _prune(value):
    """Ondoa sehemu tupu (None, [], "") — schema.org haikubali `null`."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v not in (None, [], "", {})}
    if isinstance(value, list):
        return [_prune(v) for v in value if v not in (None, [], "", {})]
    return value


def _script(data):
    payload = json.dumps(_prune(data), ensure_ascii=False).replace("</", "<\\/")
    return mark_safe(f'<script type="application/ld+json">{payload}</script>')


@register.simple_tag
def whatsapp_link(text=""):
    number = getattr(settings, "JAMIITEK_WHATSAPP", "")
    if not number:
        return ""
    from urllib.parse import quote
    return f"https://wa.me/{number}" + (f"?text={quote(text)}" if text else "")


@register.filter
def sw_ago(value):
    """'dakika 5 zilizopita', 'saa 3 zilizopita', 'siku 2 zilizopita' — falls back to the date."""
    if not value:
        return ""
    from django.utils import timezone
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 60:
        return "sasa hivi"
    if seconds < 3600:
        return f"dakika {seconds // 60} zilizopita"
    if seconds < 86400:
        return f"saa {seconds // 3600} zilizopita"
    if seconds < 7 * 86400:
        days = seconds // 86400
        return "jana" if days == 1 else f"siku {days} zilizopita"
    return sw_date(value)


@register.simple_tag
def icon(name, cls=""):
    """{% icon "search" %} → inline SVG. Accepts 'bi-globe2' too (service icons stored in the DB)."""
    from news.icons import ICONS
    key = name[3:] if name.startswith("bi-") else name
    inner = ICONS.get(key) or ICONS["stars"]
    return mark_safe(f'<svg class="i {cls}" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">{inner}</svg>')


def _as_date(value):
    from datetime import date
    from django.utils import timezone
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return timezone.localtime(value).date() if hasattr(value, "hour") else value


@register.filter
def sw_day(value):
    """'Leo', 'Jana', or the weekday name — for day headings in the news river."""
    from datetime import timedelta
    from django.utils import timezone
    from news.pipeline import SWAHILI_DAYS
    day = _as_date(value)
    if not day:
        return ""
    today = timezone.localdate()
    if day == today:
        return "Leo"
    if day == today - timedelta(days=1):
        return "Jana"
    return SWAHILI_DAYS[day.weekday()]


@register.filter
def sw_daydate(value):
    """'4 Oktoba' (year added only when it is not this year)."""
    from django.utils import timezone
    from news.pipeline import SWAHILI_MONTHS
    day = _as_date(value)
    if not day:
        return ""
    text = f"{day.day} {SWAHILI_MONTHS[day.month - 1]}"
    return text if day.year == timezone.localdate().year else f"{text} {day.year}"


@register.filter
def views_short(value):
    """1234 → '1.2K' (kwa kadi); chini ya 1000 namba kamili."""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    for size, suffix in ((1_000_000, "M"), (1_000, "K")):
        if n >= size:
            short = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{short}{suffix}"
    return str(n)


@register.simple_tag
def jamiitek_url(path="/", campaign="site"):
    from news.jamiitek_links import url
    return url(path, campaign)


@register.simple_tag
def jamiitek_site():
    from news.jamiitek_links import site
    return site()


@register.filter
def jamiitek_service_url(service):
    from news.jamiitek_links import service_url
    return service_url(service)


# ── Video za magoli (ScoreBat, news/scorebat.py) ─────────────────────────────

def _video_objects(request, h):
    from django.templatetags.static import static
    from news import scorebat
    url = _abs(request, h.get_absolute_url())
    match = scorebat.match_page(h)
    name, desc = scorebat.title(h, match), scorebat.description(h, match)
    about = {"@type": "SportsEvent", "name": h.name, "startDate": h.date.isoformat(),
             "competitor": [{"@type": "SportsTeam", "name": t} for t in (h.home, h.away) if t]}
    return [{"@type": "VideoObject", "name": f"{name} — {v['title']}" if i else name,
             "description": desc, "thumbnailUrl": [h.thumbnail] if h.thumbnail else None,
             "uploadDate": h.date.isoformat(), "embedUrl": v["src"], "url": url, "inLanguage": "sw",
             "isFamilyFriendly": True, "keywords": ", ".join(k for k in h.keywords if k), "about": about,
             "publisher": {"@type": "Organization", "name": "wILife", "url": _abs(request, "/"),
                           "logo": {"@type": "ImageObject", "url": _abs(request, static("news/brand/logo-600.png"))}}}
            for i, v in enumerate(h.videos[:4])]


@register.simple_tag(takes_context=True)
def video_jsonld(context, h, crumbs=True):
    """VideoObject (Google Video) kwa kila video ya mechi, + breadcrumb kwenye ukurasa wa video."""
    request = context["request"]
    data = [{"@context": "https://schema.org", **v} for v in _video_objects(request, h)]
    if crumbs:
        data.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": _abs(request, "/")},
            {"@type": "ListItem", "position": 2, "name": "Video za magoli", "item": _abs(request, "/video/")},
            {"@type": "ListItem", "position": 3, "name": h.name, "item": _abs(request, h.get_absolute_url())}]})
    return _script(data)


@register.simple_tag(takes_context=True)
def videos_jsonld(context, items):
    request = context["request"]
    return _script({"@context": "https://schema.org", "@type": "ItemList", "name": "Video za magoli na highlights",
                    "itemListElement": [{"@type": "ListItem", "position": i, "url": _abs(request, h.get_absolute_url())}
                                        for i, h in enumerate(items, 1)]})


@register.simple_tag
def match_video(m):
    """Video za magoli za mechi iliyokwisha (LiveMatch/Fixture), au None."""
    if not (m and getattr(m, "is_finished", False)):
        return None
    from news import scorebat
    try:
        return scorebat.for_match(m.home, m.away, m.kickoff)
    except Exception:          # ukurasa wa mechi usivunjike kwa sababu ya video
        return None



# ── Kurasa za timu (news/teams.py) ─────────────────────────────────────────────

@register.simple_tag(takes_context=True)
def team_jsonld(context, team, logo="", next_match=None, articles=()):
    """SportsTeam + mechi ijayo (SportsEvent) + breadcrumb + orodha ya habari."""
    request = context["request"]
    url = _abs(request, team.get_absolute_url())
    data = [{"@context": "https://schema.org", "@type": "SportsTeam", "name": team.name, "sport": "Soccer", "url": url,
             **({"logo": logo} if logo else {}), **({"description": team.intro[:300]} if team.intro else {})},
            {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": _abs(request, "/")},
                {"@type": "ListItem", "position": 2, "name": "Timu", "item": _abs(request, "/timu/")},
                {"@type": "ListItem", "position": 3, "name": team.name, "item": url}]}]
    if next_match is not None and next_match.url:
        data.append({"@context": "https://schema.org", **sports_event(next_match, _abs(request, next_match.url))})
    if articles:
        data.append({"@context": "https://schema.org", "@type": "ItemList", "name": f"Habari za {team.name}",
                     "itemListElement": [{"@type": "ListItem", "position": i, "url": _abs(request, a.get_absolute_url())}
                                         for i, a in enumerate(articles[:10], 1)]})
    return _script(data)


@register.simple_tag
def teams_for(*names):
    """Timu zenye kurasa zao kati ya majina haya (viungo kwenye kurasa za mechi)."""
    from news import teams
    try:
        return teams.for_names(*names)
    except Exception:
        return []

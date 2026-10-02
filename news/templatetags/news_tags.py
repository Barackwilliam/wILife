import json

from django import template
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

from news.pipeline import swahili_date

register = template.Library()


@register.filter
def render_body(text):
    """Paragraphs / '## ' subheadings / '- ' lists → safe HTML. Everything is escaped first."""
    out, bullets = [], []

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
            bullets.append(escape(line[2:].strip()))
            continue
        flush()
        if line.startswith("## "):
            out.append(f"<h2>{escape(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            out.append(f"<h2>{escape(line[2:].strip())}</h2>")
        else:
            out.append(f"<p>{escape(line)}</p>")
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
    request = context.get("request")
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    if base:
        return base + path
    return request.build_absolute_uri(path) if request else path


@register.simple_tag(takes_context=True)
def abs_url(context, url):
    """Absolute URL for a path or an already-absolute (bucket) URL."""
    if not url or url.startswith(("http://", "https://")):
        return url
    request = context.get("request")
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return base + url if base else (request.build_absolute_uri(url) if request else url)


@register.simple_tag(takes_context=True)
def article_jsonld(context, article):
    request = context["request"]
    url = request.build_absolute_uri(article.get_absolute_url())
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
        "author": {"@type": "Organization", "name": "Dawati la Habari, wILife", "url": request.build_absolute_uri("/kuhusu/")},
        "publisher": {"@type": "Organization", "name": "wILife",
                      "logo": {"@type": "ImageObject", "url": request.build_absolute_uri("/static/news/logo.png")}},
        "isBasedOn": [s.get("url") for s in article.sources if s.get("url", "").startswith("http")] or None,
    }, {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Mwanzo", "item": request.build_absolute_uri("/")},
            {"@type": "ListItem", "position": 2, "name": article.get_category_display(),
             "item": request.build_absolute_uri(f"/habari/{article.category}/")},
            {"@type": "ListItem", "position": 3, "name": article.title, "item": url},
        ],
    }]
    return _script(data)


@register.simple_tag(takes_context=True)
def site_jsonld(context):
    request = context["request"]
    root = request.build_absolute_uri("/")
    data = [{
        "@context": "https://schema.org", "@type": "NewsMediaOrganization", "name": "wILife",
        "url": root, "logo": request.build_absolute_uri("/static/news/logo.png"),
        "parentOrganization": {"@type": "Organization", "name": "JamiiTek Digital Agency"},
        "sameAs": getattr(settings, "SOCIAL_LINKS", []) or None,
        "publishingPrinciples": request.build_absolute_uri("/kuhusu/"),
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
        "url": request.build_absolute_uri(service.get_absolute_url()),
        "provider": {"@type": "Organization", "name": "JamiiTek Digital Agency"},
    }
    return _script(data)


def _script(data):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
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

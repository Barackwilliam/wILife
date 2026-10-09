"""
Domain rasmi ya wILife kwa SEO.

Canonical, JSON-LD, sitemap na robots.txt zote zinatumia SITE_URL
(https://www.wlife.online), hata ukurasa ukisomwa kupitia anwani ya Render
(*.onrender.com) au domain bila www — vinginevyo Google inaona nakala.
SITE_URL ikikosekana (development, tests), host ya ombi inatumika.
"""

from django.conf import settings


def site_root(request=None):
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if base:
        return base
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return ""


def absolute(request, path):
    if not path or path.startswith(("http://", "https://")):
        return path
    return site_root(request) + path


class PrivatePagesNoIndexMiddleware:
    """
    Kila ukurasa usio wa tovuti ya habari (app ya maisha binafsi: /app/,
    /login/, /register/, /password-reset/, admin...) unapata
    `X-Robots-Tag: noindex, nofollow`. Kurasa hizo zinaunganishwa kutoka
    kwenye tovuti ya habari ("Ingia wILife"), kwa hiyo Google ingeziorodhesha.
    robots.txt haizizuii kwa makusudi: Google lazima iweze kusoma noindex.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if (match is not None and match.namespace != "news"
                and "X-Robots-Tag" not in response
                and response.get("Content-Type", "").startswith("text/html")):
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response

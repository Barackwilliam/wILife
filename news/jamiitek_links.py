"""
Links from wILife to the real JamiiTek pages.

Articles and service pages used to describe JamiiTek's products without a way
to reach them. Every mention now leads somewhere: the service page on
jamiitek.com (JamiiBot, the web builder, templates, the domain checker...),
tagged with utm_source=wilife so the visits show up in JamiiTek's analytics.
"""

import re
from html import escape
from urllib.parse import urlencode

from django.conf import settings

# Where each wILife service lives on jamiitek.com (by service slug).
SERVICE_PATHS = {
    "utengenezaji-wa-tovuti": "/proposals/",
    "hosting-na-domain": "/domain-check/",
    "whatsapp-bots-na-ai": "/bot/",
    "mifumo-ya-usimamizi": "/proposals/",
    "seo-na-masoko-ya-kidijitali": "/service/",
}

# Words in an article body that become links, most specific first. Each target
# is linked once per article — the first mention only, so the text stays readable.
TERMS = [
    (r"JamiiBot", "/bot/"),
    (r"WhatsApp\s+(?:chat)?bots?|chatbots?(?:\s+ya\s+WhatsApp)?", "/bot/"),
    (r"web\s+builder|kijenzi\s+cha\s+tovuti", "/get-started/"),
    (r"templates?\s+za\s+tovuti|website\s+templates?", "/templates/"),
    (r"kikoa|domain(?:\s+name)?", "/domain-check/"),
    (r"JamiiTek(?:\s+Digital\s+Agency)?", "/"),
]
TERM_RE = re.compile("|".join(f"(?P<t{i}>\\b(?:{pat})\\b)" for i, (pat, _) in enumerate(TERMS)), re.I)


def site():
    return getattr(settings, "JAMIITEK_SITE", "https://www.jamiitek.com").rstrip("/")


def url(path, campaign="article"):
    query = urlencode({"utm_source": "wilife", "utm_medium": "referral", "utm_campaign": campaign})
    return f"{site()}{path}{'&' if '?' in path else '?'}{query}"


def service_url(service):
    """Explicit CTA URL first, then the service's page on jamiitek.com, else the home page."""
    if service.cta_url:
        return service.cta_url
    return url(SERVICE_PATHS.get(service.slug, "/"), campaign=f"service-{service.slug}")


class Linker:
    """Turns escaped paragraph text into HTML with JamiiTek links, once per target."""

    def __init__(self, campaign="article"):
        self.campaign = campaign
        self.used = set()

    def __call__(self, text):
        """`text` is raw (unescaped); returns safe HTML."""
        out, last = [], 0
        for m in TERM_RE.finditer(text):
            index = int(m.lastgroup[1:])
            path = TERMS[index][1]
            if path in self.used:
                continue
            self.used.add(path)
            out.append(escape(text[last:m.start()]))
            out.append(f'<a class="jt-link" href="{escape(url(path, self.campaign))}" '
                       f'target="_blank" rel="noopener">{escape(m.group(0))}</a>')
            last = m.end()
        out.append(escape(text[last:]))
        return "".join(out)

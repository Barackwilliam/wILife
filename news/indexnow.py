"""
IndexNow — tell Bing, Yandex, Seznam, Naver (and the AI search products built
on them) about new URLs the moment they are published, instead of waiting for
a crawl. Google does not use IndexNow; it reads the news sitemap.

Needs INDEXNOW_KEY and SITE_URL. The key file is served at /<key>.txt.
"""

import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

log = logging.getLogger("news")


def submit(paths):
    key = getattr(settings, "INDEXNOW_KEY", "")
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    if not key or not site or not paths:
        return False
    payload = {
        "host": urlparse(site).netloc,
        "key": key,
        "keyLocation": f"{site}/{key}.txt",
        "urlList": [site + p for p in paths][:10000],
    }
    try:
        r = requests.post("https://api.indexnow.org/indexnow", json=payload, timeout=8)
        log.info("indexnow %s for %d urls", r.status_code, len(paths))
        return r.status_code in (200, 202)
    except requests.RequestException as exc:
        log.warning("indexnow failed: %s", exc)
        return False

"""
Read news headlines from RSS/Atom feeds.

Only the headline, the short summary the publisher puts in the feed and the
link are used. Articles are never scraped: the AI writes an original Swahili
piece from those facts and links back to the source.

Feeds are configured per category in settings.NEWS_FEEDS. A feed that fails is
skipped and logged — one broken publisher must not stop the day's batch.
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from django.conf import settings

log = logging.getLogger("news")

DEFAULT_FEEDS = {
    "dunia": [
        "https://feeds.bbci.co.uk/swahili/rss.xml",
        "https://rss.dw.com/rdf/rss-sw-all",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "afrika": [
        "https://feeds.bbci.co.uk/news/world/africa/rss.xml",
        "https://rss.dw.com/rdf/rss-sw-all",
        "https://feeds.bbci.co.uk/swahili/rss.xml",
    ],
    "tanzania": [
        "https://habarileo.co.tz/feed/",
        "https://dailynews.co.tz/feed/",
        "https://www.thecitizen.co.tz/service/rss/tanzania",
        "https://www.mwananchi.co.tz/service/rss/mw",
    ],
}

USER_AGENT = "wILifeHabariBot/1.0 (+https://wilife.onrender.com)"
_TAG = re.compile(r"<[^>]+>")


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    publisher: str
    published: datetime | None = None
    feed: str = ""
    extra: dict = field(default_factory=dict)

    def as_source(self):
        return {"title": self.title, "url": self.url, "publisher": self.publisher}


def feeds_for(category):
    configured = getattr(settings, "NEWS_FEEDS", None) or {}
    return configured.get(category) or DEFAULT_FEEDS.get(category, [])


def clean(text, limit=800):
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _publisher(url, channel_title=""):
    if channel_title:
        return channel_title[:80]
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("feeds.")
    return host or "Chanzo"


def _date(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt_timezone.utc)


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node, *names):
    for child in node:
        if _local(child.tag) in names:
            if _local(child.tag) == "link" and child.get("href"):
                return child.get("href")
            if (child.text or "").strip():
                return child.text.strip()
    return ""


def parse(xml_bytes, feed_url=""):
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom into FeedItems."""
    root = ET.fromstring(xml_bytes)
    channel_title = ""
    for node in root.iter():
        if _local(node.tag) in ("channel", "feed"):
            channel_title = clean(_child_text(node, "title"), 80)
            break

    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = clean(_child_text(node, "title"), 220)
        url = _child_text(node, "link", "guid").strip()
        if not title or not url.startswith("http"):
            continue
        summary = clean(_child_text(node, "description", "summary", "content", "encoded"))
        published = _date(_child_text(node, "pubdate", "published", "updated", "date"))
        items.append(FeedItem(title=title, url=url, summary=summary,
                              publisher=_publisher(feed_url, channel_title),
                              published=published, feed=feed_url))
    return items


def fetch(url, timeout=10):
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse(response.content, url)


def collect(category, max_age_hours=36, exclude_urls=(), timeout=10):
    """
    Recent, unused items for a category from all its feeds, newest first.
    Returns (items, errors).
    """
    cutoff = datetime.now(dt_timezone.utc) - timedelta(hours=max_age_hours)
    seen, items, errors = set(exclude_urls), [], []
    for url in feeds_for(category):
        try:
            for item in fetch(url, timeout=timeout):
                if item.url in seen:
                    continue
                if item.published and item.published < cutoff:
                    continue
                seen.add(item.url)
                items.append(item)
        except Exception as exc:  # network, HTTP, XML — skip this feed only
            log.warning("feed failed %s: %s", url, exc)
            errors.append(f"{url}: {exc}")
    items.sort(key=lambda i: i.published or cutoff, reverse=True)
    return items, errors

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

from urllib.parse import quote_plus

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
        # Google News Tanzania: habari kuu zinazoripotiwa na vyombo vingi sasa hivi
        "https://news.google.com/rss?hl=sw&gl=TZ&ceid=TZ:sw",
        "https://habarileo.co.tz/feed/",
        "https://dailynews.co.tz/feed/",
        "https://www.thecitizen.co.tz/service/rss/tanzania",
        "https://www.mwananchi.co.tz/service/rss/mw",
    ],
}

# Slot ya michezo ya kila siku (si kundi jipya: makala zinakaa "Tanzania")
SPORTS_QUERY = ('Simba OR Yanga OR "Taifa Stars" OR "Azam FC" OR "NBC Premier League" '
                'OR "Ligi Kuu Tanzania" OR "Singida" OR "Twiga Stars"')


def google_news_url(query, days=2):
    """Google News search kwa Kiswahili/Tanzania — habari mpya za mada yoyote."""
    q = f"{query} when:{days}d" if days else query
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=sw&gl=TZ&ceid=TZ:sw"


DEFAULT_FEEDS["michezo"] = [google_news_url(SPORTS_QUERY, 1)]

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
        # Google News: kila habari ina <source> yake (chombo halisi), na kichwa
        # kinaishia na " - Mwananchi". Chombo halisi ndicho kinachotajwa.
        source = clean(_child_text(node, "source"), 80)
        if source:
            title = re.sub(r"\s+[-–—|]\s+" + re.escape(source) + r"\s*$", "", title)
            if summary.startswith(title):
                summary = summary[len(title):].strip(" -–—")
            summary = re.sub(r"\s*" + re.escape(source) + r"\s*$", "", summary)
        items.append(FeedItem(title=title, url=url, summary=summary,
                              publisher=source or _publisher(feed_url, channel_title),
                              published=published, feed=feed_url))
    return items


def fetch(url, timeout=10):
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse(response.content, url)


def collect(category, max_age_hours=36, exclude_urls=(), timeout=10, urls=None):
    """
    Recent, unused items for a category (or an explicit list of feed urls),
    newest first. Returns (items, errors).
    """
    cutoff = datetime.now(dt_timezone.utc) - timedelta(hours=max_age_hours)
    seen, items, errors = set(exclude_urls), [], []
    seen_titles = set()
    for url in (urls if urls is not None else feeds_for(category)):
        try:
            for item in fetch(url, timeout=timeout):
                if item.url in seen:
                    continue
                if item.published and item.published < cutoff:
                    continue
                key = _title_key(item.title)
                if key in seen_titles:      # habari ileile kutoka feed mbili
                    continue
                seen.add(item.url)
                seen_titles.add(key)
                items.append(item)
        except Exception as exc:  # network, HTTP, XML — skip this feed only
            log.warning("feed failed %s: %s", url, exc)
            errors.append(f"{url}: {exc}")
    items.sort(key=lambda i: i.published or cutoff, reverse=True)
    return items, errors


# ---------------------------------------------------------------------------
# Uzito wa habari: ipi inafuatiliwa zaidi?
# ---------------------------------------------------------------------------

STOPWORDS = set("""
na wa ya za la kwa ni katika kwenye kutoka hadi juu chini baada kabla leo jana kesho
huu hii hizi hayo hao yake yao wake wao pia tu sana zaidi bado sasa kama ili lakini au
amesema wamesema asema alisema mwaka siku wiki mwezi mpya kubwa kuu
the a an of to in on for and or at by with from as is are was were be has have had
it its this that these those after before over into new says said will would could
""".split())


def _title_key(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:80]


def tokens(text):
    words = re.findall(r"[A-Za-zÀ-ÿ0-9']+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def _terms(raw):
    """'Simba, Yanga\nTaifa Stars' -> ['simba', 'yanga', 'taifa stars']"""
    parts = re.split(r"[,\n;]+", raw or "") if isinstance(raw, str) else list(raw or [])
    return [p.strip().lower() for p in parts if p and p.strip()]


def matches(item, terms):
    text = f"{item.title} {item.summary}".lower()
    return [t for t in terms if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text)]


def rank(items, focus=(), avoid=(), now=None):
    """
    Panga habari kwa uzito, si kwa muda tu:

      * vyombo vingine vingapi vinaripoti habari hiyo hiyo (inafuatiliwa)
      * inagusa mada za kipaumbele za mhariri (mf. Simba, Yanga)
      * ni mpya kiasi gani

    Habari zinazogusa mada za "kuepuka" zinaondolewa kabisa.
    """
    focus, avoid = _terms(focus), _terms(avoid)
    now = now or datetime.now(dt_timezone.utc)
    pool = [i for i in items if not (avoid and matches(i, avoid))]
    toks = [tokens(f"{i.title} {i.summary[:200]}") for i in pool]
    title_toks = [tokens(i.title) for i in pool]
    for idx, item in enumerate(pool):
        others = set()
        for j, other in enumerate(pool):
            if j == idx or other.publisher == item.publisher:
                continue
            shared = title_toks[idx] & toks[j]
            if len(shared) >= 2 or (title_toks[idx] and len(shared) / len(title_toks[idx]) >= 0.5):
                others.add(other.publisher)
        hits = matches(item, focus) if focus else []
        age_h = ((now - item.published).total_seconds() / 3600) if item.published else 24
        freshness = max(0.0, 1 - age_h / 36)
        item.extra["coverage"] = len(others)
        item.extra["focus"] = hits
        item.extra["score"] = round(len(others) * 2 + len(hits) * 3 + freshness, 3)
    pool.sort(key=lambda i: (i.extra["score"], i.published or now), reverse=True)
    return pool

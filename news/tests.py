import json
from datetime import date, datetime, timezone as dt_tz
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.agent import approvals
from news import feeds, pipeline, writer
from news.models import Article, JamiiTekService, NewsBatch
from news.templatetags.news_tags import render_body

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Habari Test</title>
<item><title>Bunge lapitisha bajeti</title><link>https://example.tz/a</link>
<description>&lt;p&gt;Bunge limepitisha bajeti ya mwaka.&lt;/p&gt;</description>
<pubDate>Fri, 02 Oct 2026 06:00:00 GMT</pubDate></item>
<item><title>No link</title></item></channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>World</title>
<entry><title>Summit opens</title><link href="https://example.com/b"/><summary>Leaders meet.</summary>
<updated>2026-10-02T05:00:00Z</updated></entry></feed>"""

ARTICLE_JSON = {
    "title": "Kichwa cha habari",
    "excerpt": "Muhtasari mfupi wa habari.",
    "body": "Aya ya kwanza. " * 60 + "\n\n## Kwa nini ni muhimu\n\nAya ya pili.",
    "keywords": "habari, tanzania",
}


def fake_item(n, cat):
    return feeds.FeedItem(title=f"{cat} story {n}", url=f"https://example.com/{cat}/{n}",
                          summary="Summary", publisher="Example",
                          published=datetime(2026, 10, 2, 5, tzinfo=dt_tz.utc))


def fake_collect(category, exclude_urls=(), **kw):
    items = [fake_item(i, category) for i in range(6)]
    return [i for i in items if i.url not in exclude_urls], []


class FeedParsingTests(TestCase):
    def test_rss2(self):
        items = feeds.parse(RSS, "https://example.tz/feed/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Bunge lapitisha bajeti")
        self.assertEqual(items[0].summary, "Bunge limepitisha bajeti ya mwaka.")
        self.assertEqual(items[0].publisher, "Habari Test")
        self.assertEqual(items[0].published.year, 2026)

    def test_atom(self):
        items = feeds.parse(ATOM, "https://example.com/feed")
        self.assertEqual(items[0].url, "https://example.com/b")
        self.assertEqual(items[0].summary, "Leaders meet.")


class BodyRenderingTests(TestCase):
    def test_escapes_and_structures(self):
        html = render_body("Aya <script>x</script>\n\n## Kichwa\n- moja\n- mbili")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<h2>Kichwa</h2>", html)
        self.assertIn("<ul><li>moja</li><li>mbili</li></ul>", html)


@override_settings(NEWS_ENABLED=True, GROQ_API_KEY="k", AGENT_ENABLED=True,
                   AGENT_SELF_CHANNEL="email", EMAIL_CHANNEL_ENABLED=False, TELEGRAM_ENABLED=False,
                   WHATSAPP_ENABLED=False)
class PipelineTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "o@x.com", "x")

    def _run(self, day):
        when = timezone.make_aware(datetime(day.year, day.month, day.day, 6, 0))
        with mock.patch("news.feeds.collect", side_effect=fake_collect), \
             mock.patch("news.writer._call", return_value=json.dumps(ARTICLE_JSON)), \
             mock.patch("core.agent.channels.send_to_self") as send:
            result = pipeline.run(now=when)
        return result, send

    def test_tuesday_batch_has_six_articles_and_asks_for_approval(self):
        result, _ = self._run(date(2026, 10, 6))  # Tuesday
        batch = NewsBatch.objects.get(date=date(2026, 10, 6))
        self.assertEqual(result["sent"], 6)
        self.assertEqual(batch.status, "pending")
        cats = list(batch.articles.order_by("slot").values_list("category", flat=True))
        self.assertEqual(cats, ["dunia", "afrika", "afrika", "tanzania", "tanzania", "tanzania"])
        self.assertTrue(all(a.cover_png for a in batch.articles.all()))
        self.assertEqual(batch.approval.tool, "publish_news")
        self.assertFalse(Article.published.exists())

    def test_monday_adds_jamiitek_article(self):
        self._run(date(2026, 10, 5))  # Monday
        batch = NewsBatch.objects.get(date=date(2026, 10, 5))
        jt = batch.articles.get(slot=7)
        self.assertEqual(jt.category, "jamiitek")
        self.assertIsNotNone(jt.service)
        self.assertIsNotNone(JamiiTekService.objects.get(pk=jt.service.pk).last_featured_at)

    def test_ok_reply_publishes_batch_and_no_rejects(self):
        self._run(date(2026, 10, 6))
        batch = NewsBatch.objects.get()
        ok, reply = approvals.approve(batch.approval.code, user=self.owner)
        self.assertTrue(ok)
        self.assertIn("zimechapishwa", reply)
        self.assertEqual(Article.published.count(), 6)

        self._run(date(2026, 10, 7))
        batch2 = NewsBatch.objects.get(date=date(2026, 10, 7))
        approvals.reject(batch2.approval.code, user=self.owner)
        batch2.refresh_from_db()
        self.assertEqual(batch2.status, "rejected")
        self.assertEqual(Article.published.count(), 6)

    def test_same_story_never_used_twice(self):
        self._run(date(2026, 10, 6))
        self._run(date(2026, 10, 7))
        urls = list(Article.objects.values_list("source_url", flat=True))
        self.assertEqual(len(urls), len(set(urls)))

    def test_disabled_and_early_do_nothing(self):
        with self.settings(NEWS_ENABLED=False):
            self.assertEqual(pipeline.run()["detail"], "disabled")
        early = timezone.make_aware(datetime(2026, 10, 6, 3, 0))
        self.assertEqual(pipeline.run(now=early)["detail"], "too early")

    def test_writer_rejects_bad_json(self):
        with mock.patch("news.writer._call", return_value="not json"):
            with self.assertRaises(writer.WriterError):
                writer.write_news("Tanzania", fake_item(1, "tanzania"))


class PublicSiteTests(TestCase):
    def setUp(self):
        self.pub = Article.objects.create(title="Habari njema Dodoma", category="tanzania",
                                          excerpt="Muhtasari", body="Aya moja.\n\nAya mbili.",
                                          status="published", published_at=timezone.now(),
                                          sources=[{"title": "Chanzo", "url": "https://example.tz/x", "publisher": "Ex"}],
                                          cover_png=b"\x89PNG fake", keywords="Dodoma, habari")
        self.draft = Article.objects.create(title="Rasimu ya siri", category="dunia", excerpt="x", body="y")

    def test_home_and_category(self):
        r = self.client.get("/")
        self.assertContains(r, "Habari njema Dodoma")
        self.assertNotContains(r, "Rasimu ya siri")
        self.assertContains(r, 'lang="sw"')
        self.assertEqual(self.client.get("/habari/tanzania/").status_code, 200)
        self.assertEqual(self.client.get("/habari/haipo/").status_code, 404)

    def test_article_seo(self):
        r = self.client.get(self.pub.get_absolute_url())
        self.assertContains(r, '"@type": "NewsArticle"')
        self.assertContains(r, '<link rel="canonical"')
        self.assertContains(r, 'property="og:image"')
        self.assertContains(r, "https://example.tz/x")
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.views, 1)

    def test_drafts_hidden_from_public_visible_to_staff(self):
        self.assertEqual(self.client.get(self.draft.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(f"/habari/picha/{self.draft.pk}.png").status_code, 404)
        staff = User.objects.create_user("ed", password="x", is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(self.draft.get_absolute_url())
        self.assertContains(r, "noindex")

    def test_cover_image(self):
        r = self.client.get(f"/habari/picha/{self.pub.pk}.png")
        self.assertEqual(r["Content-Type"], "image/png")

    def test_sitemaps_robots_rss(self):
        self.assertContains(self.client.get("/sitemap.xml"), self.pub.get_absolute_url())
        self.assertNotContains(self.client.get("/sitemap.xml"), self.draft.slug)
        self.assertContains(self.client.get("/news-sitemap.xml"), "<news:language>sw</news:language>")
        self.assertContains(self.client.get("/robots.txt"), "Sitemap:")
        self.assertContains(self.client.get("/rss/"), "Habari njema Dodoma")

    def test_services_pages(self):
        s = JamiiTekService.objects.first()
        self.assertContains(self.client.get("/huduma/"), s.name)
        self.assertContains(self.client.get(s.get_absolute_url()), '"@type": "Service"')

    def test_search(self):
        self.assertContains(self.client.get("/tafuta/?q=Dodoma"), "Habari njema Dodoma")

    def test_review_page_is_staff_only(self):
        self.assertEqual(self.client.get("/habari-admin/rasimu/").status_code, 302)


class SeoAndSpeedTests(TestCase):
    def setUp(self):
        self.a = Article.objects.create(title="Bunge la Tanzania lapitisha bajeti", category="tanzania",
                                        excerpt="Muhtasari", body="Aya.", keywords="Bunge, Dodoma, bajeti",
                                        status="published", published_at=timezone.now())
        self.a.render_cover()
        self.a.save()

    def test_no_old_brand_or_schedule_on_public_pages(self):
        for url in ("/", "/habari/", "/kuhusu/", self.a.get_absolute_url()):
            body = self.client.get(url).content.decode()
            self.assertNotIn("wILife Habari", body, url)
            self.assertNotIn("kuu sita", body.lower(), url)

    def test_public_pages_do_not_load_bootstrap(self):
        body = self.client.get("/").content.decode()
        self.assertNotIn("bootstrap.min.css", body)
        self.assertNotIn("bootstrap-icons", body)

    def test_webp_thumbnail(self):
        r = self.client.get(f"/habari/picha/{self.a.pk}-sanaa.webp")
        self.assertEqual(r["Content-Type"], "image/webp")
        self.assertIn("immutable", r["Cache-Control"])
        self.assertTrue(r.content.startswith(b"RIFF"))

    def test_topic_and_latest_pages(self):
        self.assertContains(self.client.get(self.a.get_absolute_url()), "/mada/dodoma/")
        self.assertContains(self.client.get("/mada/dodoma/"), self.a.title)
        self.assertEqual(self.client.get("/mada/haipo/").status_code, 404)
        self.assertContains(self.client.get("/habari/"), self.a.title)
        self.assertContains(self.client.get("/sitemap.xml"), "/mada/bunge/")

    def test_news_sitemap_has_image(self):
        self.assertContains(self.client.get("/news-sitemap.xml"), f"/habari/picha/{self.a.pk}.png")

    @override_settings(INDEXNOW_KEY="abc123def456")
    def test_indexnow_key_file_and_robots_still_work(self):
        self.assertEqual(self.client.get("/abc123def456.txt").content, b"abc123def456")
        self.assertEqual(self.client.get("/wrong.txt").status_code, 404)
        self.assertContains(self.client.get("/robots.txt"), "Sitemap:")

    @override_settings(INDEXNOW_KEY="abc123def456", SITE_URL="https://example.tz")
    def test_publishing_batch_pings_indexnow(self):
        batch = NewsBatch.objects.create(date=date(2026, 10, 9))
        Article.objects.create(title="Rasimu", category="dunia", excerpt="x", body="y", batch=batch, slot=1)
        with mock.patch("news.indexnow.requests.post") as post, self.captureOnCommitCallbacks(execute=True):
            pipeline.publish_batch(batch)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["host"], "example.tz")
        self.assertTrue(any("/habari/dunia/rasimu/" in u for u in payload["urlList"]))

    def test_anonymous_pages_are_cached_and_gzipped(self):
        r = self.client.get("/", HTTP_ACCEPT_ENCODING="gzip")
        self.assertEqual(r["Content-Encoding"], "gzip")
        self.assertIn("max-age", r.get("Cache-Control", ""))


@override_settings(
    USE_SUPABASE_STORAGE=True,
    MEDIA_URL="https://proj.supabase.co/storage/v1/object/public/wilife-media/",
    STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
              "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class BucketStorageTests(TestCase):
    def _article(self, **kw):
        a = Article.objects.create(title="Picha kwenye bucket", category="dunia", excerpt="x", body="y",
                                   status="published", published_at=timezone.now(), **kw)
        return a

    def test_covers_go_to_bucket_not_database(self):
        a = self._article()
        a.render_cover()
        a.save()
        a.refresh_from_db()
        self.assertIsNone(a.cover_png)
        self.assertTrue(a.cover_image.name.endswith(".png"))
        self.assertTrue(a.cover_art.name.endswith(".webp"))
        self.assertTrue(a.art_url.startswith("https://proj.supabase.co/storage/v1/object/public/wilife-media/habari/"))

    def test_pages_link_to_bucket_and_old_urls_redirect(self):
        a = self._article()
        a.render_cover()
        a.save()
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn(a.art_url, page)
        self.assertIn(f'content="{a.og_url}"', page)
        self.assertIn(a.og_url, self.client.get("/news-sitemap.xml").content.decode())
        r = self.client.get(f"/habari/picha/{a.pk}.png")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], a.og_url)

    def test_move_command_uploads_database_covers(self):
        from django.core.management import call_command
        from io import StringIO
        a = self._article()
        with self.settings(USE_SUPABASE_STORAGE=False):
            a.render_cover()
            a.save()
        self.assertIsNotNone(Article.objects.get(pk=a.pk).cover_png)
        out = StringIO()
        call_command("news_move_covers", stdout=out)
        a.refresh_from_db()
        self.assertIn("Moved 1", out.getvalue())
        self.assertIsNone(a.cover_png)
        self.assertTrue(a.cover_art.name.endswith(".webp"))


class DatabaseFallbackTests(TestCase):
    def test_without_bucket_covers_stay_in_database(self):
        a = Article.objects.create(title="Bila bucket", category="tanzania", excerpt="x", body="y",
                                   status="published", published_at=timezone.now())
        a.render_cover()
        a.save()
        a.refresh_from_db()
        self.assertFalse(a.cover_image)
        self.assertIsNotNone(a.cover_png)
        self.assertEqual(a.art_url, f"/habari/picha/{a.pk}-sanaa.webp")
        self.assertEqual(self.client.get(a.art_url)["Content-Type"], "image/webp")

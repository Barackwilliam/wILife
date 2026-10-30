import json
import requests
import time
from datetime import date, datetime, timedelta, timezone as dt_tz
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

    def test_missing_slots_are_retried_after_a_partial_batch(self):
        day = date(2026, 10, 6)  # Tuesday: six slots

        def dry_afrika(category, exclude_urls=(), **kw):
            return ([], ["feed down"]) if category == "afrika" else fake_collect(category, exclude_urls)

        def at(hour, collect):
            when = timezone.make_aware(datetime(day.year, day.month, day.day, hour, 0))
            with mock.patch("news.feeds.collect", side_effect=collect), \
                 mock.patch("news.writer._call", return_value=json.dumps(ARTICLE_JSON)), \
                 mock.patch("core.agent.channels.send_to_self") as send:
                pipeline.run(now=when)
            return send

        at(10, dry_afrika)
        batch = NewsBatch.objects.get(date=day)
        self.assertEqual((batch.status, batch.articles.count()), ("pending", 4))
        send = at(11, fake_collect)  # feeds are back
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.articles.count()), ("pending", 6))
        self.assertIn("Rasimu zaidi", send.call_args.args[1])
        self.assertIn(f"OK {batch.approval.code}", send.call_args.args[1])

    def test_no_top_up_after_stop_hour(self):
        day = date(2026, 10, 6)
        batch = NewsBatch.objects.create(date=day, status="pending")
        when = timezone.make_aware(datetime(day.year, day.month, day.day, 17, 0))
        with mock.patch("news.feeds.collect", side_effect=fake_collect) as collect:
            self.assertEqual(pipeline.run(now=when)["detail"], "batch pending")
        collect.assert_not_called()

    def test_batch_closed_while_empty_is_reopened(self):
        NewsBatch.objects.create(date=date(2026, 10, 6), status="published")
        result, _ = self._run(date(2026, 10, 6))
        self.assertEqual(result["sent"], 6)
        self.assertEqual(NewsBatch.objects.get(date=date(2026, 10, 6)).status, "pending")

    def test_review_page_refuses_to_publish_empty_batch(self):
        batch = NewsBatch.objects.create(date=date(2026, 10, 6))
        self.client.force_login(self.owner)
        self.client.post("/habari-admin/rasimu/action/", {"action": "publish_batch", "batch": batch.pk})
        batch.refresh_from_db()
        self.assertEqual(batch.status, "drafting")


def _groq_response(status, body):
    response = mock.Mock(status_code=status, text=json.dumps(body))
    response.json.return_value = body
    return response


@override_settings(GROQ_API_KEY="k", GROQ_MODEL="retired-model")
class GroqModelFallbackTests(TestCase):
    def setUp(self):
        from core import groq
        groq._replacements.clear()
        self.groq = groq

    def test_retired_model_is_replaced_and_remembered(self):
        gone = _groq_response(404, {"error": {"code": "model_not_found"}})
        ok = _groq_response(200, {"choices": [{"message": {"content": " habari "}}]})
        models = _groq_response(200, {"data": [{"id": "whisper-large-v3"}, {"id": "llama-3.1-8b-instant"},
                                               {"id": "openai/gpt-oss-120b"}]})
        with mock.patch("core.groq.requests.post", side_effect=[gone, ok, ok]) as post, \
             mock.patch("core.groq.requests.get", return_value=models):
            self.assertEqual(self.groq.chat("s", "u", json_mode=True), "habari")
            self.groq.chat("s", "u")
        used = [c.kwargs["json"]["model"] for c in post.call_args_list]
        self.assertEqual(used, ["retired-model", "openai/gpt-oss-120b", "openai/gpt-oss-120b"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["response_format"], {"type": "json_object"})

    def test_other_errors_are_not_retried(self):
        with mock.patch("core.groq.requests.post", return_value=_groq_response(401, {"error": "bad key"})) as post:
            with self.assertRaises(self.groq.GroqError):
                self.groq.chat("s", "u")
        self.assertEqual(post.call_count, 1)

    def test_writer_reports_groq_failure_as_writer_error(self):
        with mock.patch("core.groq.requests.post", return_value=_groq_response(500, {})):
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

    def test_head_tags_are_clean(self):
        import re
        body = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertEqual(body.count('property="og:image:width"'), 1)
        self.assertNotIn("hreflang", body)
        blocks = re.findall(r'application/ld\+json">(.*?)</script>', body, re.S)
        self.assertTrue(blocks)
        for block in blocks:
            json.loads(block)
            self.assertNotIn("null", block)
        self.assertEqual(len(re.findall(r"<h1[ >]", body)), 1)

    def test_pagination_links_page_one_cleanly(self):
        for i in range(25):
            Article.objects.create(title=f"Habari ya majaribio {i}", category="tanzania", excerpt="M", body="Aya.",
                                   status="published", published_at=timezone.now())
        from django.core.cache import cache
        cache.clear()
        body = self.client.get("/habari/?page=2").content.decode()
        cache.clear()   # ukurasa uliohifadhiwa (cache_page) usivuje kwenye majaribio mengine
        self.assertIn('rel="prev" href="/habari/"', body)
        self.assertNotIn("page=1", body)
        self.assertIn('og:url" content="http://testserver/habari/?page=2"', body)

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
        # Mada yenye habari moja tu haiingii kwenye sitemap (IndexingHygieneTest)
        self.assertNotContains(self.client.get("/sitemap.xml"), "/mada/bunge/")

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


class RiverListTests(TestCase):
    """The list pages: lead story, day groups, load-more fragment, scoped stats."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)  # anonymous pages are cached; keep other tests clean
        now = timezone.now()
        for i in range(20):
            Article.objects.create(
                title=f"Habari namba {i}", category="tanzania" if i % 2 else "dunia", excerpt="x", body="y " * 50,
                keywords="Dodoma", source_url=f"https://example.tz/{i}", status="published",
                published_at=now - timezone.timedelta(hours=i * 6))

    def test_latest_page_has_lead_day_groups_and_load_more(self):
        r = self.client.get("/habari/")
        self.assertContains(r, 'class="rv-lead')
        self.assertContains(r, "Habari namba 0")
        self.assertContains(r, 'class="rv-day"')
        self.assertContains(r, "data-more")
        self.assertContains(r, "?page=2")
        self.assertNotContains(r, "core/css/wilife.css")

    def test_partial_returns_only_rows_and_is_not_indexed(self):
        r = self.client.get("/habari/?page=2&partial=1")
        self.assertEqual(r["X-Robots-Tag"], "noindex")
        self.assertContains(r, 'class="rv-row')
        self.assertNotContains(r, "<html")
        self.assertNotContains(r, "rv-lead")
        self.assertNotContains(r, "data-next")  # 20 articles fit on two pages

    def test_category_stats_count_only_that_category(self):
        r = self.client.get("/habari/tanzania/")
        self.assertEqual(r.context["total"], 10)
        self.assertEqual(r.context["all_count"], 20)
        self.assertNotContains(r, "Habari namba 0")  # dunia

    def test_topic_and_search_use_the_river(self):
        self.assertContains(self.client.get("/mada/dodoma/"), 'class="rv-row')
        r = self.client.get("/tafuta/?q=namba")
        self.assertContains(r, 'class="rv-row')
        self.assertNotContains(r, "rv-lead")  # search results are not ranked by "lead"


class BrandAssetTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)  # anonymous pages are cached; keep other tests clean

    def test_favicon_manifest_and_share_image(self):
        r = self.client.get("/favicon.ico")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/x-icon"))
        manifest = self.client.get("/site.webmanifest").json()
        self.assertEqual(manifest["short_name"], "wILife")
        self.assertTrue(any(i["sizes"] == "512x512" for i in manifest["icons"]))
        home = self.client.get("/").content.decode()
        self.assertIn("news/brand/og-default", home)
        self.assertIn('rel="apple-touch-icon"', home)
        self.assertIn("news/brand/icon-512", home)  # publisher logo for Google
        self.assertIn("news/brand/mark-96", home)    # header logo

    def test_generated_covers_carry_the_logo(self):
        from news import covers
        self.assertIsNotNone(covers._mark(52))


@override_settings(JAMIITEK_SITE="https://www.jamiitek.com")
class ViewsAndJamiiTekLinksTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        self.bot = JamiiTekService.objects.get(slug="whatsapp-bots-na-ai")
        self.art = Article.objects.create(
            title="JamiiBot inavyosaidia maduka", category="jamiitek", excerpt="x", service=self.bot,
            body="JamiiBot ni msaidizi wa WhatsApp wa JamiiTek.\n\nJamiiBot tena.\n\n"
                 "## JamiiBot kichwa\n- Chagua templates za tovuti\n- Kagua domain yako",
            status="published", published_at=timezone.now(), views=1499, cover_png=b"\x89PNG")
        self.news = Article.objects.create(
            title="Habari ya dunia", category="dunia", excerpt="x", body="Domain mpya za .africa.",
            status="published", published_at=timezone.now(), views=7, cover_png=b"\x89PNG")

    def test_views_are_shown(self):
        r = self.client.get(self.art.get_absolute_url())
        self.assertContains(r, "Imesomwa mara 1500")           # pamoja na usomaji huu
        self.assertContains(r, "1.5K")
        r = self.client.get("/habari/")
        self.assertContains(r, "Imesomwa mara 7")

    def test_jamiitek_article_links_each_product_once(self):
        html = self.client.get(self.art.get_absolute_url()).content.decode()
        self.assertEqual(html.count('class="jt-link" href="https://www.jamiitek.com/bot/?utm_source=wilife'), 1)
        self.assertIn('href="https://www.jamiitek.com/bot/?utm_source=wilife&amp;utm_medium=referral'
                      '&amp;utm_campaign=service-whatsapp-bots-na-ai"', html)          # kitufe cha huduma
        self.assertIn('class="jt-link" href="https://www.jamiitek.com/templates/?utm_source=wilife', html)
        self.assertIn('href="https://www.jamiitek.com/domain-check/?utm_source=wilife', html)
        self.assertIn('href="https://www.jamiitek.com/?utm_source=wilife', html)
        self.assertIn("<h2>JamiiBot kichwa</h2>", html)                 # vichwa havigeuzwi

    def test_other_news_is_not_linked(self):
        html = self.client.get(self.news.get_absolute_url()).content.decode()
        self.assertNotIn('class="jt-link"', html)

    def test_service_page_opens_the_real_jamiitek_page(self):
        r = self.client.get(self.bot.get_absolute_url())
        self.assertContains(r, 'href="https://www.jamiitek.com/bot/?utm_source=wilife&amp;utm_medium=referral'
                               '&amp;utm_campaign=service-whatsapp-bots-na-ai"')
        self.assertContains(r, "Fungua kwenye JamiiTek")

    def test_views_short(self):
        from news.templatetags.news_tags import views_short
        self.assertEqual([views_short(n) for n in (0, 999, 1000, 1499, 12000, 2_300_000)],
                         ["0", "999", "1K", "1.5K", "12K", "2.3M"])

    def test_linker_escapes(self):
        html = render_body("<b>JamiiBot</b> & JamiiTek", "jamiitek")
        self.assertIn("&lt;b&gt;<a", html)
        self.assertIn("&amp;", html)


@override_settings(SITE_URL="https://www.wlife.online")
class SeoAuditTests(TestCase):
    def setUp(self):
        self.a = Article.objects.create(
            title="Serikali yatangaza mpango mpya wa kilimo cha umwagiliaji kwa wakulima wadogo nchini",
            category="tanzania", excerpt="Muhtasari wa mpango", body="Aya.",
            status="published", published_at=timezone.now())

    def test_canonical_and_jsonld_use_official_domain(self):
        body = self.client.get(self.a.get_absolute_url(), HTTP_HOST="wilife.onrender.com").content.decode()
        self.assertIn(f'<link rel="canonical" href="https://www.wlife.online{self.a.get_absolute_url()}"', body)
        self.assertNotIn("onrender.com", body)

    def test_robots_and_sitemaps_use_official_domain(self):
        for url in ("/robots.txt", "/sitemap.xml", "/news-sitemap.xml"):
            body = self.client.get(url, HTTP_HOST="wilife.onrender.com").content.decode()
            self.assertIn("https://www.wlife.online/", body, url)
            self.assertNotIn("onrender.com", body, url)

    def test_paginated_pages_are_self_canonical(self):
        for i in range(30):
            Article.objects.create(title=f"Habari {i}", category="dunia", excerpt=f"x{i}", body="y",
                                   status="published", published_at=timezone.now())
        r = self.client.get("/habari/?page=2")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<link rel="canonical" href="https://www.wlife.online/habari/?page=2"')

    def test_long_article_title_has_no_brand_suffix(self):
        self.assertContains(self.client.get(self.a.get_absolute_url()), f"<title>{self.a.title}</title>")

    def test_private_pages_are_noindex(self):
        r = self.client.get("/login/")
        self.assertEqual(r["X-Robots-Tag"], "noindex, nofollow")
        self.assertNotIn("X-Robots-Tag", self.client.get("/"))
        self.assertNotIn("X-Robots-Tag", self.client.get(self.a.get_absolute_url()))


GNEWS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>"Simba Yanga" - Google News</title>
<item><title>Simba yaichapa Yanga 2-1 Kwa Mkapa - Mwanaspoti</title><link>https://news.google.com/rss/articles/abc</link>
<description>&lt;a href="x"&gt;Simba yaichapa Yanga 2-1 Kwa Mkapa&lt;/a&gt;&amp;nbsp;&amp;nbsp;Mwanaspoti</description>
<pubDate>Fri, 09 Oct 2026 18:00:00 GMT</pubDate><source url="https://www.mwanaspoti.co.tz">Mwanaspoti</source></item>
</channel></rss>"""


def item(title, publisher, hours_ago=1, summary=""):
    return feeds.FeedItem(title=title, url=f"https://x.tz/{abs(hash((title, publisher)))}", summary=summary,
                          publisher=publisher, published=timezone.now() - timedelta(hours=hours_ago))


class TrendingAndSportsTests(TestCase):
    def test_google_news_source_and_title(self):
        it = feeds.parse(GNEWS, "https://news.google.com/rss/search?q=x")[0]
        self.assertEqual(it.publisher, "Mwanaspoti")
        self.assertEqual(it.title, "Simba yaichapa Yanga 2-1 Kwa Mkapa")

    def test_story_covered_by_many_outlets_beats_newest_obscure_one(self):
        items = [
            item("Mtu mmoja aokota sarafu ya zamani Kigoma", "BlogA", hours_ago=0),
            item("Simba yaichapa Yanga 2-1 Kwa Mkapa", "Mwanaspoti", hours_ago=2),
            item("Yanga yapoteza dhidi ya Simba Kwa Mkapa", "Mwananchi", hours_ago=3),
            item("Matokeo: Simba 2 Yanga 1 ligi kuu", "Citizen", hours_ago=3),
        ]
        ranked = feeds.rank(items, focus="")
        self.assertNotEqual(ranked[0].publisher, "BlogA")
        self.assertGreaterEqual(ranked[0].extra["coverage"], 2)

    def test_focus_and_avoid_topics(self):
        items = [item("Bei ya mchele yapanda sokoni", "A"), item("Taifa Stars yaitwa kambini", "B"),
                 item("Tukio la uchawi kijijini", "C")]
        ranked = feeds.rank(items, focus="Taifa Stars", avoid="uchawi")
        self.assertEqual(ranked[0].title, "Taifa Stars yaitwa kambini")
        self.assertNotIn("Tukio la uchawi kijijini", [i.title for i in ranked])

    def test_sports_slot_reads_sports_feeds_and_editor_notes_reach_the_writer(self):
        from news.models import EditorSettings
        cfg = EditorSettings.load()
        cfg.editor_notes = "Taja wafungaji"
        cfg.save()
        calls = []

        def collect(category, exclude_urls=(), **kw):
            calls.append(kw.get("urls"))
            return [item("Simba yaichapa Yanga 2-1", "Mwanaspoti")], []
        batch = NewsBatch.objects.create(date=date(2026, 10, 9))
        with mock.patch("news.feeds.collect", side_effect=collect), \
             mock.patch("news.writer._call", return_value=json.dumps(ARTICLE_JSON)) as call:
            pipeline.fill_slot(batch, pipeline.SPORTS_SLOT, "tanzania")
        self.assertEqual(calls[0], feeds.feeds_for("michezo"))
        self.assertIn("Taja wafungaji", call.call_args[0][1])


class CommissionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "o@x.com", "x")

    def test_parse_command(self):
        from news.commission import parse_command
        self.assertEqual(parse_command("Andika habari kuhusu Simba na Yanga leo"), ("Simba na Yanga leo", False))
        self.assertEqual(parse_command("habari: Taifa Stars #chapisha"), ("Taifa Stars", True))
        self.assertEqual(parse_command("OK 1234"), (None, False))

    def _run(self, publish=False, sources=True, reply=None):
        from news import commission
        from news.models import NewsRequest
        req = NewsRequest.objects.create(topic="Simba na Yanga leo", category="tanzania",
                                         publish_now=publish, via="whatsapp", requested_by=self.owner)
        found = [item("Simba yaichapa Yanga 2-1", "Mwanaspoti"), item("Yanga yapoteza kwa Simba", "Mwananchi")]
        with mock.patch("news.feeds.collect", return_value=(found if sources else [], [])), \
             mock.patch("news.writer._call", return_value=reply or json.dumps(ARTICLE_JSON)) as call, \
             mock.patch("core.agent.channels.send_to_self") as send:
            commission.run(req)
        req.refresh_from_db()
        return req, call, send

    def test_draft_with_approval_code_then_ok_publishes(self):
        req, call, send = self._run()
        self.assertEqual(req.status, "drafted")
        self.assertEqual(req.article.status, "draft")
        self.assertIn("Mwanaspoti", call.call_args[0][1])
        self.assertEqual(len(req.article.sources), 2)
        self.assertIn(f"OK {req.approval.code}", send.call_args[0][1])
        ok, reply = approvals.approve(req.approval.code, user=self.owner)
        self.assertTrue(ok, reply)
        req.refresh_from_db()
        self.assertEqual((req.status, req.article.status), ("published", "published"))

    def test_publish_now(self):
        req, _, _ = self._run(publish=True)
        self.assertEqual(req.article.status, "published")

    def test_no_sources_means_no_article(self):
        req, call, send = self._run(sources=False)
        self.assertEqual(req.status, "failed")
        self.assertFalse(call.called)
        self.assertIn("Sitabuni", req.error)
        self.assertFalse(Article.objects.exists())

    def test_ai_can_refuse_when_sources_are_thin(self):
        req, _, _ = self._run(reply='{"skip": "vyanzo havitaji matokeo"}')
        self.assertEqual(req.status, "failed")
        self.assertIn("vyanzo havitaji matokeo", req.error)

    def test_whatsapp_command_from_owner_starts_a_request(self):
        from core import views_whatsapp
        from news.models import NewsRequest
        with mock.patch("core.views_whatsapp._authorised_user", return_value=self.owner), \
             mock.patch("news.commission.start") as start:
            reply = views_whatsapp._handle_message("255700000000", "Andika habari kuhusu Taifa Stars")
        self.assertIn("Taifa Stars", reply)
        self.assertTrue(start.called)
        self.assertEqual(NewsRequest.objects.get().via, "whatsapp")

    def test_page_and_settings(self):
        from news.models import EditorSettings
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/habari-admin/agiza/").status_code, 200)
        self.client.post("/habari-admin/agiza/", {"action": "settings", "focus_topics": "Simba, Yanga",
                                                  "avoid_topics": "uchawi", "editor_notes": "Fupi"})
        cfg = EditorSettings.load()
        self.assertEqual((cfg.focus_topics, cfg.sports_slot), ("Simba, Yanga", False))
        with mock.patch("news.commission.start") as start, self.settings(GROQ_API_KEY="k"):
            self.client.post("/habari-admin/agiza/", {"action": "commission", "topic": "Bunge leo"})
        self.assertTrue(start.called)


class ScheduledCommissionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "o@x.com", "x")
        # Ijumaa 9 Okt 2026, saa 4 asubuhi (10:00) Dar es Salaam
        self.now = timezone.make_aware(datetime(2026, 10, 9, 10, 0))

    def test_swahili_time(self):
        from news.commission import parse_time
        cases = {
            "Simba na Yanga saa 2 usiku": (9, 20, 0, "Simba na Yanga"),
            "Bunge saa 4:30 asubuhi": (9, 10, 30, "Bunge"),
            "Taifa Stars kesho saa 10 jioni": (10, 16, 0, "Taifa Stars"),
            "Mechi 18:45": (9, 18, 45, "Mechi"),
            "Bajeti saa 6 mchana": (9, 12, 0, "Bajeti"),
            "Habari saa 1 asubuhi": (10, 7, 0, "Habari"),   # imeshapita leo -> kesho
        }
        for text, (day, hour, minute, topic) in cases.items():
            when, rest = parse_time(text, now=self.now)
            local = timezone.localtime(when)
            self.assertEqual((local.day, local.hour, local.minute, rest), (day, hour, minute, topic), text)
        self.assertEqual(parse_time("Simba na Yanga leo", now=self.now), (None, "Simba na Yanga leo"))

    def test_chat_command_with_time_is_scheduled(self):
        from news import commission
        from news.models import NewsRequest
        with mock.patch("news.commission.start") as start, \
             mock.patch("django.utils.timezone.now", return_value=self.now):
            reply = commission.from_chat("Andika habari kuhusu Simba na Yanga saa 2 usiku", self.owner, "whatsapp")
        req = NewsRequest.objects.get()
        self.assertFalse(start.called)
        self.assertEqual((req.status, req.topic), ("scheduled", "Simba na Yanga"))
        self.assertEqual(timezone.localtime(req.start_at).hour, 20)
        self.assertIn("20:00", reply)

    def _scheduled(self, start_at, **kw):
        from news import commission
        from news.models import NewsRequest
        req = NewsRequest(topic="Simba na Yanga", category="tanzania", via="whatsapp", requested_by=self.owner, **kw)
        commission.schedule(req, start_at).save()
        return req

    def test_tick_starts_only_due_requests(self):
        from news import commission
        due = self._scheduled(self.now - timedelta(minutes=1))
        later = self._scheduled(self.now + timedelta(hours=3))
        with mock.patch("news.commission.start") as start:
            result = commission.run_due(now=self.now)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(start.call_args[0][0].pk, due.pk)
        later.refresh_from_db()
        self.assertEqual(later.status, "scheduled")

    def _run(self, req, found, now):
        from news import commission
        with mock.patch("news.feeds.collect", return_value=(found, [])), \
             mock.patch("news.writer._call", return_value=json.dumps(ARTICLE_JSON)), \
             mock.patch("core.agent.channels.send_to_self") as send, \
             mock.patch("django.utils.timezone.now", return_value=now):
            commission.run(req)
        req.refresh_from_db()
        return send

    def test_no_reports_yet_keeps_watching_quietly_then_gives_up(self):
        start = self.now
        req = self._scheduled(start, watch_hours=2)
        old = feeds.FeedItem(title="Simba na Yanga leo: vikosi", url="https://x.tz/preview", summary="",
                             publisher="Mwananchi", published=start - timedelta(hours=5))
        send = self._run(req, [old], start)          # makala ya kabla ya mechi haihesabiki
        self.assertEqual(req.status, "watching")
        self.assertEqual(req.next_try_at, start + timedelta(minutes=15))
        self.assertFalse(send.called)
        send = self._run(req, [], start + timedelta(hours=3))
        self.assertEqual(req.status, "failed")
        self.assertTrue(send.called)

    def test_reports_after_the_event_become_a_draft(self):
        start = self.now
        req = self._scheduled(start)
        fresh = feeds.FeedItem(title="Simba yaichapa Yanga 2-1", url="https://x.tz/res", summary="",
                               publisher="Mwanaspoti", published=start + timedelta(minutes=20))
        send = self._run(req, [fresh], start + timedelta(minutes=30))
        self.assertEqual(req.status, "drafted")
        self.assertIn("OK", send.call_args[0][1])

    def test_web_form_schedules(self):
        from news.models import NewsRequest
        self.client.force_login(self.owner)
        when = timezone.localtime() + timedelta(hours=2)
        with mock.patch("news.commission.start") as start, self.settings(GROQ_API_KEY="k"):
            self.client.post("/habari-admin/agiza/", {"action": "commission", "topic": "Simba na Yanga",
                                                      "start_at": when.strftime("%Y-%m-%dT%H:%M"), "watch_hours": "4"})
        req = NewsRequest.objects.get()
        self.assertFalse(start.called)
        self.assertEqual((req.status, req.watch_hours), ("scheduled", 4))
        self.client.post("/habari-admin/agiza/", {"action": "cancel", "request": req.pk})
        with mock.patch("news.commission.start"), self.settings(GROQ_API_KEY="k"):
            self.client.post("/habari-admin/agiza/", {"action": "commission", "topic": "Uchaguzi",
                                                      "start_at": when.strftime("%Y-%m-%dT%H:%M"), "watch_hours": "168"})
        self.assertEqual(NewsRequest.objects.get(topic="Uchaguzi").watch_hours, 168)
        req.refresh_from_db()
        self.assertEqual(req.status, "failed")



class GroqJsonFallbackTests(TestCase):
    def test_json_mode_rejection_retries_without_json_mode(self):
        from core import groq
        bad = mock.Mock(status_code=400, text='{"error":{"code":"json_validate_failed"}}')
        bad.json.return_value = {"error": {"code": "json_validate_failed", "failed_generation": ""}}
        good = mock.Mock(status_code=200, text="ok")
        good.json.return_value = {"choices": [{"message": {"content": "```json\n{\"a\": 1}\n```"}}]}
        with mock.patch("core.groq.requests.post", side_effect=[bad, good]) as post, \
             self.settings(GROQ_API_KEY="k"):
            out = groq.chat("s", "u", json_mode=True, max_tokens=1000)
        self.assertIn('{"a": 1}', out)
        second = post.call_args_list[1].kwargs["json"]
        self.assertNotIn("response_format", second)
        self.assertEqual(second["max_tokens"], 1500)

    def test_writer_reads_json_wrapped_in_text(self):
        raw = "Hii ndiyo makala:\n```json\n" + json.dumps(ARTICLE_JSON) + "\n```\nAsante."
        with mock.patch("news.writer._call", return_value=raw):
            data = writer.write_news("Tanzania", fake_item(1, "tanzania"))
        self.assertEqual(data["title"], ARTICLE_JSON["title"])

    def test_failed_request_can_be_retried(self):
        from news.models import NewsRequest
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        req = NewsRequest.objects.create(topic="Simba", status="failed", error="groq HTTP 400", requested_by=owner)
        self.client.force_login(owner)
        with mock.patch("news.commission.start") as start:
            self.client.post("/habari-admin/agiza/", {"action": "retry", "request": req.pk})
        req.refresh_from_db()
        self.assertTrue(start.called)
        self.assertEqual((req.status, req.error), ("queued", ""))



class LongWatchTests(TestCase):
    def test_retry_slows_down_after_six_hours_and_labels(self):
        from news import commission
        from news.models import NewsRequest
        start = timezone.now() - timedelta(hours=1)
        req = NewsRequest(topic="x", start_at=start, watch_hours=168)
        self.assertEqual(commission._retry_minutes(req), 15)
        req.start_at = timezone.now() - timedelta(hours=8)
        self.assertEqual(commission._retry_minutes(req), 60)
        self.assertEqual((commission.watch_label(6), commission.watch_label(168)), ("saa 6", "siku 7"))


import shutil
import tempfile

_MEDIA = tempfile.mkdtemp(prefix="wilife-media-")


def _photo(w, h, fmt="JPEG", exif_rotate=False):
    from io import BytesIO
    from PIL import Image
    img = Image.new("RGB", (w, h), (30, 120, 200))
    for x in range(0, w, 40):
        img.paste((240, 200, 40), (x, 0, x + 8, h))
    buf = BytesIO()
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = 6   # simu: zungusha 90°
        img.save(buf, fmt, exif=exif)
    else:
        img.save(buf, fmt)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=_MEDIA, USE_SUPABASE_STORAGE=False)
class UploadedPhotoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def _article(self, data, name="picha-yangu.jpg"):
        from django.core.files.base import ContentFile
        a = Article.objects.create(title="Simba yaichapa Yanga", category="tanzania", excerpt="x", body="y",
                                   status="published", published_at=timezone.now())
        a.cover_art.save(name, ContentFile(data), save=True)
        return a

    def _size(self, f):
        from PIL import Image
        f.open("rb")
        try:
            return Image.open(f).size
        finally:
            f.close()

    def test_big_upload_gets_hd_mobile_and_share_versions(self):
        from news import photos
        a = self._article(_photo(3000, 2000))
        self.assertEqual(photos.process(a, field="cover_art"), "hd")
        a.save()
        self.assertEqual(self._size(a.cover_art), (1200, 800))
        self.assertEqual(self._size(a.cover_hd), (2400, 1600))
        self.assertEqual(self._size(a.cover_image), (1200, 630))
        self.assertEqual(self._size(a.cover_original), (3000, 2000))
        self.assertIn("2400w", a.srcset)
        r = self.client.get(a.get_absolute_url())
        self.assertContains(r, "2400w")
        self.assertContains(r, a.cover_image.url)   # og:image ni picha yako

    def test_small_upload_is_flagged_and_has_no_hd(self):
        from news import photos
        a = self._article(_photo(640, 480))
        self.assertEqual(photos.process(a, field="cover_art"), "low")
        self.assertFalse(a.cover_hd)
        self.assertEqual(a.srcset, "")
        self.assertEqual(self._size(a.cover_art)[0], 1200)

    def test_phone_photo_is_rotated(self):
        from news import photos
        a = self._article(_photo(2000, 1500, exif_rotate=True))
        photos.process(a, field="cover_art")
        self.assertEqual(a.photo_size, "1500×2000")

    def test_tick_checks_pending_and_skips_generated(self):
        from news import photos
        up = self._article(_photo(2000, 1200))
        gen = Article.objects.create(title="Habari ya mfumo", category="dunia", excerpt="x", body="y")
        from django.core.files.base import ContentFile
        gen.cover_art.save(f"{gen.slug}-{gen.pk}.webp", ContentFile(_photo(1200, 630, "WEBP")), save=True)
        result = photos.check_pending(limit=10)
        up.refresh_from_db()
        gen.refresh_from_db()
        self.assertEqual((up.photo_quality, gen.photo_quality), ("hd", "generated"))
        self.assertEqual(result["sent"], 2)
        self.assertEqual(photos.check_pending()["detail"], "none")

    def test_admin_save_processes_new_upload(self):
        from django.contrib.admin.sites import site
        from news.admin import ArticleAdmin
        a = self._article(_photo(1300, 900))
        admin = ArticleAdmin(Article, site)
        request = mock.Mock()
        admin.message_user = mock.Mock()
        admin.save_model(request, a, mock.Mock(changed_data=["cover_art"]), True)
        a.refresh_from_db()
        self.assertEqual(a.photo_quality, "sd")
        self.assertTrue(a.cover_original)
        # Rasimu ya mfumo haifuniki picha yako
        a.render_cover()
        self.assertTrue(a.cover_art.name.endswith("-p.webp"))


@override_settings(MEDIA_ROOT=_MEDIA, USE_SUPABASE_STORAGE=False)
class PhotoAdminPageTests(TestCase):
    def test_admin_pages_show_photo_quality(self):
        from django.core.files.base import ContentFile
        from news import photos
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        a = Article.objects.create(title="Taifa Stars", category="tanzania", excerpt="x", body="y")
        a.cover_art.save("stars.jpg", ContentFile(_photo(700, 500)), save=True)
        photos.process(a, field="cover_art")
        a.save()
        self.client.force_login(owner)
        r = self.client.get(f"/admin/news/article/{a.pk}/change/")
        self.assertContains(r, "Ndogo")
        self.assertContains(r, "1800px")
        self.assertContains(self.client.get("/admin/news/article/"), "Ndogo")


PROMO_JSON = {"title": "Duka la Mama Neema lazindua ofa ya vitenge", "excerpt": "Ofa ya vitenge Kariakoo.",
              "body": "Duka la Mama Neema lililopo Kariakoo limezindua ofa ya vitenge kwa bei ya TZS 25,000. "
                      "Wateja wanaweza kupiga 0712 345 678 kwa maelezo zaidi kuhusu ofa hii ya wiki hii.",
              "keywords": "vitenge, Kariakoo, ofa"}
CAPTION = ("OFA KUBWA! Vitenge vipya TZS 25,000 tu 🔥🔥 Kariakoo. Piga 0712 345 678 #vitenge #kariakoo "
           "@mamaneema_store https://www.instagram.com/p/ABC123/")


@override_settings(MEDIA_ROOT=_MEDIA, USE_SUPABASE_STORAGE=False)
class InstagramPromoTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "o@x.com", "x")

    def test_parse_caption(self):
        from news.promo import parse_caption
        text, link, account = parse_caption(CAPTION)
        self.assertEqual(link, "https://www.instagram.com/p/ABC123/")
        self.assertEqual(account, "@mamaneema_store")
        self.assertNotIn("instagram.com", text)

    def test_draft_with_photo_and_ai(self):
        from news import promo
        with mock.patch("news.writer._call", return_value=json.dumps(PROMO_JSON)) as call, \
             self.settings(GROQ_API_KEY="k"):
            a, note = promo.create_draft(_photo(2000, 2000), "post.jpg", CAPTION,
                                         link="https://www.instagram.com/p/ABC123/", account="mamaneema_store")
        self.assertIn("0712 345 678", call.call_args[0][1])
        self.assertEqual((a.status, a.origin, a.photo_quality), ("draft", "instagram", "hd"))
        self.assertEqual(a.sources[0]["publisher"], "Instagram @mamaneema_store")
        self.assertEqual(a.source_url, "https://www.instagram.com/p/ABC123/")
        self.assertEqual(note, "")

    def test_without_ai_the_caption_is_kept(self):
        from news import promo
        a, note = promo.create_draft(_photo(600, 600), "p.jpg", "Ofa ya vitenge\nBei TZS 25,000", rewrite=False)
        self.assertIn("TZS 25,000", a.body)
        self.assertEqual(a.title, "Ofa ya vitenge")
        self.assertIn("ndogo", note)

    def test_ai_failure_falls_back_to_caption(self):
        from news import promo
        with mock.patch("news.writer._call", return_value="si JSON"), self.settings(GROQ_API_KEY="k"):
            a, note = promo.create_draft(None, "", "Ofa ya vitenge\nBei TZS 25,000")
        self.assertIn("TZS 25,000", a.body)
        self.assertIn("AI haikupanga", note)

    def test_page_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/habari-admin/weka-habari/").status_code, 200)
        r = self.client.post("/habari-admin/weka-habari/", {
            "caption": CAPTION, "category": "tanzania",
            "image": SimpleUploadedFile("post.jpg", _photo(1500, 1500), content_type="image/jpeg")})
        self.assertEqual(r.status_code, 302)
        a = Article.objects.get(origin="instagram")
        self.assertEqual(a.sources[0]["url"], "https://www.instagram.com/p/ABC123/")
        self.assertContains(self.client.get("/habari-admin/weka-habari/"), a.title)

    def test_telegram_photo_from_owner_becomes_draft(self):
        from core import views_telegram

        class Now:
            def __init__(self, target, **kw):
                self.target = target

            def start(self):
                self.target()
        msg = {"chat": {"id": 42}, "caption": CAPTION,
               "photo": [{"file_id": "small"}, {"file_id": "big"}]}
        with self.settings(AGENT_TELEGRAM_CHAT_ID="42"), \
             mock.patch("threading.Thread", Now), \
             mock.patch("core.agent.telegram.download_file", return_value=(_photo(1600, 1200), "f.jpg")) as dl, \
             mock.patch("core.views_telegram.send_telegram") as send, \
             mock.patch("django.db.connection.close"):
            views_telegram._handle({"message": msg})
        dl.assert_called_with("big")
        a = Article.objects.get(origin="instagram")
        self.assertIn("OK", send.call_args[0][1])
        self.assertEqual(a.sources[0]["publisher"], "Instagram @mamaneema_store")

    def test_telegram_photo_from_stranger_is_ignored(self):
        from core import views_telegram
        with self.settings(AGENT_TELEGRAM_CHAT_ID="42"), mock.patch("core.views_telegram._start_promo") as start:
            views_telegram._handle({"message": {"chat": {"id": 7}, "photo": [{"file_id": "x"}]}})
        self.assertFalse(start.called)


class PromoFallbackTitleTests(TestCase):
    def test_title_is_first_sentences_without_tags(self):
        from news.promo import _fallback
        d = _fallback(CAPTION, "@mamaneema_store")
        self.assertEqual(d["title"], "OFA KUBWA! Vitenge vipya TZS 25,000 tu 🔥🔥 Kariakoo.")
        self.assertNotIn("@", d["title"])


FB_CAPTION = ("Tamasha la Muziki Dodoma litafanyika Jumamosi tarehe 25 Oktoba, Uwanja wa Jamhuri. "
              "Tiketi TZS 10,000. https://www.facebook.com/DodomaEvents/posts/12345")


@override_settings(MEDIA_ROOT=_MEDIA, USE_SUPABASE_STORAGE=False)
class FacebookPostTests(TestCase):
    def test_facebook_link_is_detected(self):
        from news.promo import parse_caption, platform_of
        text, link, _ = parse_caption(FB_CAPTION)
        self.assertEqual(link, "https://www.facebook.com/DodomaEvents/posts/12345")
        self.assertEqual(platform_of(link), "facebook")
        self.assertEqual(platform_of("https://fb.watch/abc/"), "facebook")
        self.assertNotIn("facebook.com", text)

    def test_facebook_draft_keeps_page_name(self):
        from news import promo
        with mock.patch("news.writer._call", return_value=json.dumps(PROMO_JSON)) as call, \
             self.settings(GROQ_API_KEY="k"):
            a, _ = promo.create_draft(_photo(1900, 1000), "fb.jpg", FB_CAPTION,
                                      link="https://www.facebook.com/DodomaEvents/posts/12345",
                                      account="Dodoma Events")
        self.assertIn("Mtandao: Facebook", call.call_args[0][1])
        self.assertEqual((a.origin, a.sources[0]["publisher"]), ("facebook", "Facebook Dodoma Events"))

    def test_page_lists_both_platforms(self):
        from news import promo
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        promo.create_draft(None, "", "Habari ya Instagram\nmaelezo", link="https://www.instagram.com/p/A1/", rewrite=False)
        promo.create_draft(None, "", "Habari ya Facebook\nmaelezo", link="https://www.facebook.com/x/posts/1", rewrite=False)
        self.client.force_login(owner)
        r = self.client.get("/habari-admin/weka-habari/")
        self.assertContains(r, "Habari ya Instagram")
        self.assertContains(r, "Habari ya Facebook")
        self.assertContains(r, "Weka habari")


class PlatformChoiceTests(TestCase):
    def test_link_wins_over_radio_choice(self):
        from news import promo
        a, _ = promo.create_draft(None, "", "Habari\nmaelezo", link="https://www.facebook.com/x/posts/9",
                                  platform="instagram", rewrite=False)
        self.assertEqual(a.origin, "facebook")
        b, _ = promo.create_draft(None, "", "Habari nyingine\nmaelezo", platform="facebook", rewrite=False)
        self.assertEqual(b.origin, "facebook")


def _http(status, body):
    r = mock.Mock(status_code=status, text=json.dumps(body))
    r.json.return_value = body
    return r


GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "Habari njema kwa Kiswahili."}]},
                             "finishReason": "STOP"}]}


@override_settings(GEMINI_API_KEY="g-key", GROQ_API_KEY="q-key")
class GeminiAndModelChoiceTests(TestCase):
    def setUp(self):
        from core import gemini
        gemini._replacements.clear()

    def test_gemini_request_and_reply(self):
        from core import gemini
        with mock.patch("core.gemini.requests.post", return_value=_http(200, GEMINI_OK)) as post:
            out = gemini.chat("mfumo", "swali", model="gemini-2.5-flash", json_mode=True, max_tokens=500)
        self.assertEqual(out, "Habari njema kwa Kiswahili.")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "mfumo")
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})
        self.assertIn("gemini-2.5-flash:generateContent", post.call_args.args[0])

    def test_retired_gemini_model_is_replaced(self):
        from core import gemini
        models = _http(200, {"models": [{"name": "models/gemini-2.0-flash",
                                         "supportedGenerationMethods": ["generateContent"]}]})
        with mock.patch("core.gemini.requests.post", side_effect=[_http(404, {}), _http(200, GEMINI_OK)]) as post, \
             mock.patch("core.gemini.requests.get", return_value=models):
            gemini.chat("s", "u", model="gemini-9-flash")
        self.assertIn("gemini-2.0-flash:generateContent", post.call_args.args[0])

    def test_model_suggested_by_google_is_used(self):
        from core import gemini
        gone = _http(404, {"error": {"code": 404, "message": "This model models/gemini-2.5-flash is no longer "
                                     "available to new users. Please update your code to use "
                                     "models/gemini-3.8-flash for the latest features."}})
        models = _http(200, {"models": [{"name": "models/gemini-2.5-flash",
                                         "supportedGenerationMethods": ["generateContent"]}]})
        with mock.patch("core.gemini.requests.post", side_effect=[gone, _http(200, GEMINI_OK)]) as post, \
             mock.patch("core.gemini.requests.get", return_value=models):
            self.assertEqual(gemini.chat("s", "u", model="gemini-2.5-flash"), "Habari njema kwa Kiswahili.")
        self.assertIn("gemini-3.8-flash:generateContent", post.call_args.args[0])
        self.assertEqual(gemini._replacements["gemini-2.5-flash"], "gemini-3.8-flash")

    def test_newest_listed_model_of_same_family_is_tried_next(self):
        from core import gemini
        models = _http(200, {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                                        for n in ("gemini-2.5-pro", "gemini-3.1-pro", "gemini-3.1-flash",
                                                  "gemini-3.1-flash-lite", "gemini-2.5-flash-preview-tts")]})
        with mock.patch("core.gemini.requests.get", return_value=models):
            self.assertEqual(gemini.replacement_models("gemini-2.5-flash", "")[0], "gemini-3.1-flash")
            self.assertEqual(gemini.replacement_models("gemini-2.5-pro", "")[0], "gemini-3.1-pro")

    def test_writer_uses_the_selected_model(self):
        from core import llm
        from news.models import EditorSettings
        cfg = EditorSettings.load()
        cfg.ai_model = "gemini:gemini-2.5-pro"
        cfg.save()
        with mock.patch("core.gemini.chat", return_value=json.dumps(ARTICLE_JSON)) as g, \
             mock.patch("core.groq.chat") as q:
            writer.write_news("Tanzania", fake_item(1, "tanzania"))
        self.assertEqual(g.call_args.kwargs["model"], "gemini-2.5-pro")
        self.assertFalse(q.called)
        self.assertEqual(llm.selected(), "gemini:gemini-2.5-pro")

    def test_gemini_limit_falls_back_to_groq(self):
        from core import gemini, llm
        with mock.patch("core.gemini.chat", side_effect=gemini.GeminiError("gemini HTTP 429")), \
             mock.patch("core.groq.chat", return_value="jibu la groq") as q:
            self.assertEqual(llm.chat("s", "u", choice="gemini:gemini-2.5-flash"), "jibu la groq")
        self.assertIsNone(q.call_args.kwargs["model"])   # model ya kawaida ya Groq

    def test_groq_choice_passes_its_model(self):
        from core import llm
        with mock.patch("core.groq.chat", return_value="ok") as q:
            llm.chat("s", "u", choice="groq:openai/gpt-oss-120b")
        self.assertEqual(q.call_args.kwargs["model"], "openai/gpt-oss-120b")

    def test_page_buttons_switch_and_test(self):
        from news.models import EditorSettings
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        self.client.force_login(owner)
        r = self.client.get("/habari-admin/agiza/")
        self.assertContains(r, "Gemini 2.5 Flash")
        self.assertContains(r, "Tumia hii")
        self.client.post("/habari-admin/agiza/", {"action": "ai_model", "ai_model": "groq:openai/gpt-oss-120b"})
        self.assertEqual(EditorSettings.load().ai_model, "groq:openai/gpt-oss-120b")
        self.client.post("/habari-admin/agiza/", {"action": "ai_model", "ai_model": "evil:model"})
        self.assertEqual(EditorSettings.load().ai_model, "groq:openai/gpt-oss-120b")
        with mock.patch("core.gemini.chat", return_value="Habari sahihi ni msingi wa jamii."):
            r = self.client.post("/habari-admin/agiza/", {"action": "ai_test", "ai_model": "gemini:gemini-2.5-flash"},
                                 follow=True)
        self.assertContains(r, "Habari sahihi ni msingi wa jamii.")

    def test_admin_list_can_change_model(self):
        from news.models import EditorSettings
        EditorSettings.load()
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        self.client.force_login(owner)
        self.assertContains(self.client.get("/admin/news/editorsettings/"), "Gemini 2.5 Flash")


@override_settings(GEMINI_API_KEY="g-key", GROQ_API_KEY="q-key")
class AiHealthWarningTests(TestCase):
    def _fail_once(self):
        from core import gemini, llm
        with mock.patch("core.gemini.chat", side_effect=gemini.GeminiError('gemini HTTP 429: {"status": "RESOURCE_EXHAUSTED"}')), \
             mock.patch("core.groq.chat", return_value="jibu la groq"):
            llm.chat("s", "u")

    def test_fallback_is_recorded_and_cleared(self):
        from core import llm
        self.assertIsNone(llm.health())
        self._fail_once()
        self._fail_once()
        h = llm.health()
        self.assertEqual(h["level"], "fallback")
        self.assertIn("Gemini imeshindwa", h["title"])
        self.assertIn("Groq", h["title"])
        self.assertIn("Kikomo cha bure", h["reason"])
        self.assertEqual(h["count"], 2)
        with mock.patch("core.gemini.chat", return_value="sawa"):
            llm.chat("s", "u")
        self.assertIsNone(llm.health())

    def test_all_failing_is_down(self):
        from core import gemini, groq, llm
        with mock.patch("core.gemini.chat", side_effect=gemini.GeminiError("gemini HTTP 403: API key not valid")), \
             mock.patch("core.groq.chat", side_effect=groq.GroqError("groq HTTP 500: x")):
            with self.assertRaises(llm.LLMError):
                llm.chat("s", "u")
        h = llm.health()
        self.assertEqual(h["level"], "down")
        self.assertIn("Key haikubaliki", h["reason"])

    @override_settings(GEMINI_API_KEY="")
    def test_missing_gemini_key_warns(self):
        from core import llm
        self.assertIn("Gemini haijawekwa", llm.health()["title"])

    def test_warning_shows_for_staff_on_dashboard_and_ai_pages(self):
        self._fail_once()
        owner = User.objects.create_superuser("owner", "o@x.com", "x")
        self.client.force_login(owner)
        for url in ("/dashboard/", "/habari-admin/agiza/", "/habari-admin/rasimu/", "/habari-admin/weka-habari/"):
            self.assertContains(self.client.get(url, follow=True), "Gemini imeshindwa", msg_prefix=url)

    def test_warning_hidden_from_non_staff(self):
        self._fail_once()
        user = User.objects.create_user("u", "u@x.com", "x")
        self.client.force_login(user)
        self.assertNotContains(self.client.get("/dashboard/", follow=True), "Gemini imeshindwa")


class NoAiKeyTests(TestCase):
    @override_settings(GEMINI_API_KEY="", GROQ_API_KEY="")
    def test_no_keys(self):
        from core import llm
        self.assertFalse(llm.enabled())
        with self.assertRaises(llm.LLMError):
            llm.chat("s", "u")


@override_settings(JAMIITEK_SITE="https://www.jamiitek.com")
class TemplatePromoPhoneTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        self.art = Article.objects.create(
            title="Habari ya promo", category="dunia", excerpt="x", body="Maandishi.",
            status="published", published_at=timezone.now(), cover_png=b"\x89PNG")

    def test_promo_phone_on_public_pages_from_jamiitek(self):
        for url in ("/", self.art.get_absolute_url()):
            html = self.client.get(url).content.decode()
            self.assertIn('id="jtp" hidden', html, url)                 # haionekani hadi JS iamue
            self.assertIn('data-api="https://www.jamiitek.com/templates/promo.json"', html, url)
            self.assertIn("addEventListener('scroll', onScroll", html, url)   # inasubiri ku-scroll
            self.assertIn("TTL = 2 * 60 * 60 * 1000", html, url)          # inarudi baada ya saa 2
            self.assertIn("drift=1", html, url)
            self.assertIn("utm_source=wilife", html, url)
            self.assertIn("POS = 'jt_promo_pos'", html, url)               # inaburutwa, upande unakumbukwa
            self.assertIn(".jtp.tuck .jtp-txt", html, url)                  # ikivutwa ukingoni inajificha nusu

    def test_promo_phone_not_on_staff_pages(self):
        staff = User.objects.create_superuser("boss", "b@x.com", "x")
        self.client.force_login(staff)
        self.assertNotIn('id="jtp"', self.client.get("/dashboard/", follow=True).content.decode())


class NewsSitemapNeverEmptyTests(TestCase):
    """Search Console treats an empty <urlset> as an error ("Missing XML tag: url")."""

    def _article(self, title, hours_ago):
        a = Article.objects.create(title=title, category="dunia", excerpt="x", body="x",
                                   status="published", published_at=timezone.now(), cover_png=b"\x89PNG")
        Article.objects.filter(pk=a.pk).update(published_at=timezone.now() - timedelta(hours=hours_ago))
        return a

    def test_quiet_48_hours_keep_the_newest_article(self):
        old = self._article("Habari ya juzi", 72)
        self._article("Habari ya zamani zaidi", 200)
        xml = self.client.get("/news-sitemap.xml").content.decode()
        self.assertEqual(xml.count("<url>"), 1)
        self.assertIn(old.get_absolute_url(), xml)

    def test_only_recent_articles_when_there_are_some(self):
        new = self._article("Habari ya leo", 1)
        old = self._article("Habari ya juzi", 72)
        xml = self.client.get("/news-sitemap.xml").content.decode()
        self.assertIn(new.get_absolute_url(), xml)
        self.assertNotIn(old.get_absolute_url(), xml)


class IndexingHygieneTest(TestCase):
    """Search Console: kurasa nyembamba za mada na /index.html zilizokuwa 404."""

    def _publish(self, n, keywords):
        from django.utils import timezone
        for i in range(n):
            Article.objects.create(title=f"Habari {keywords} {i}",
                                   category="tanzania", excerpt="x", body="maandishi", keywords=keywords,
                                   status="published", published_at=timezone.now())

    def test_topics_with_few_articles_are_noindex_and_left_out_of_the_sitemap(self):
        self._publish(3, "Bunge, Dodoma")
        self._publish(1, "Simba")
        self.assertContains(self.client.get("/mada/simba/"), 'content="noindex, follow"')
        self.assertNotContains(self.client.get("/mada/bunge/"), "noindex")
        xml = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("/mada/bunge/", xml)
        self.assertNotIn("/mada/simba/", xml)

    def test_old_index_urls_redirect_home(self):
        for url in ("/index.html", "/index"):
            r = self.client.get(url)
            self.assertEqual((r.status_code, r["Location"]), (301, "/"), url)


class LiveCoverageTest(TestCase):
    """Habari ya LIVE (news/live.py): masasisho, JSON kwa wasomaji, LiveBlogPosting kwa Google."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()                       # ukurasa wa mwanzo uko kwenye cache — usiuachie jaribio jingine
        self.addCleanup(cache.clear)
        self.a = Article.objects.create(title="Simba vs Yanga: Kariakoo Derby LIVE", category="tanzania",
                                        excerpt="Mechi ya watani wa jadi", body="Aya.", keywords="Simba, Yanga",
                                        status="published", published_at=timezone.now())
        self.a.render_cover()
        self.a.save()
        self.staff = User.objects.create_user("mhariri", "m@x.com", "p", is_staff=True)
        self.act = f"/habari-admin/live/{self.a.pk}/"

    def _jsonld(self, body):
        import re
        return [json.loads(b) for b in re.findall(r'application/ld\+json">(.*?)</script>', body, re.S)]

    def test_staff_runs_live_coverage_and_google_gets_liveblogposting(self):
        url = self.a.get_absolute_url()
        self.assertNotIn('id="live"', self.client.get(url).content.decode())        # makala ya kawaida
        self.assertEqual(self.client.post(self.act, {"action": "start"}).status_code, 302)   # si mhariri
        self.a.refresh_from_db()
        self.assertIsNone(self.a.live_started_at)

        self.client.force_login(self.staff)
        self.assertIn("Anza LIVE", self.client.get(url).content.decode())
        self.client.post(self.act, {"action": "start"})
        self.client.post(self.act, {"action": "update", "label": "6'", "text": "GOOOL! Simba wanaongoza.",
                                    "score": "Simba 1 - 0 Yanga"})
        self.client.post(self.act, {"action": "update", "label": "52'", "text": "Yanga <b>wanasawazisha</b>.",
                                    "score": "Simba 1 - 1 Yanga"})
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_live)
        self.assertEqual(self.a.live_score, "Simba 1 - 1 Yanga")

        self.client.logout()
        page = self.client.get(url).content.decode()
        self.assertIn('class="lv-badge"><i></i>LIVE', page)
        self.assertIn("Simba 1 - 1 Yanga", page)
        self.assertIn("Yanga &lt;b&gt;wanasawazisha&lt;/b&gt;.", page)               # imesafishwa
        self.assertLess(page.index("52&#x27;"), page.index("6&#x27;"))               # mpya juu
        live = next(d for block in self._jsonld(page) for d in block if d.get("@type") == "LiveBlogPosting")
        self.assertIn("coverageStartTime", live)
        self.assertNotIn("coverageEndTime", live)
        self.assertEqual(len(live["liveBlogUpdate"]), 2)
        self.assertTrue(live["liveBlogUpdate"][0]["headline"].startswith("52' Yanga"))
        self.assertIn('class="live-pill">LIVE', self.client.get("/").content.decode())

        feed = self.client.get(f"/habari/live/{self.a.pk}.json").json()
        self.assertEqual((feed["live"], feed["score"], len(feed["updates"])), (True, "Simba 1 - 1 Yanga", 2))
        newest = feed["updates"][0]["id"]
        self.assertEqual(self.client.get(f"/habari/live/{self.a.pk}.json?after={newest}").json()["updates"], [])

        self.client.force_login(self.staff)
        self.client.post(self.act, {"action": "end"})
        self.client.logout()
        page = self.client.get(url).content.decode()
        self.assertIn("IMEISHA", page)
        live = next(d for block in self._jsonld(page) for d in block if d.get("@type") == "LiveBlogPosting")
        self.assertIn("coverageEndTime", live)
        self.assertFalse(self.client.get(f"/habari/live/{self.a.pk}.json").json()["live"])

    def test_feed_is_only_for_published_live_articles(self):
        self.assertEqual(self.client.get(f"/habari/live/{self.a.pk}.json").status_code, 404)
        self.a.live_started_at = timezone.now()
        self.a.status = "draft"
        self.a.save()
        self.assertEqual(self.client.get(f"/habari/live/{self.a.pk}.json").status_code, 404)


@override_settings(WHATSAPP_BRIDGE_KEY="bridge-key", AGENT_DEFAULT_RECIPIENT="255712345678",
                   TELEGRAM_WEBHOOK_SECRET="tg-secret", AGENT_TELEGRAM_CHAT_ID="42", SITE_URL="https://www.wlife.online")
class LiveFromChatTest(TestCase):
    """Mhariri anaripoti mechi kutoka WhatsApp / Telegram — kupitia milango ile ile bridge na Telegram zinayotumia."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        User.objects.create_user("william", "w@x.com", "p", is_superuser=True, is_staff=True)
        cover = mock.patch("news.models.Article.render_cover")
        cover.start()
        self.addCleanup(cover.stop)

    def wa(self, message, phone="255712345678"):
        r = self.client.post("/agent/whatsapp/baileys/", json.dumps({"phone": phone, "message": message}),
                             content_type="application/json", HTTP_X_BRIDGE_KEY="bridge-key")
        self.assertEqual(r.status_code, 200)
        return r.json()["reply"]

    def test_whatsapp_runs_a_whole_match(self):
        reply = self.wa("live anza Simba vs Yanga")
        a = Article.objects.get()
        self.assertIn("LIVE imeanza", reply)
        self.assertIn(f"https://www.wlife.online{a.get_absolute_url()}", reply)
        self.assertTrue(a.is_live)
        self.assertEqual(a.status, "published")

        self.assertIn("Sasisho limewekwa", self.wa("live 1' Mechi imeanza!"))
        self.assertIn("Simba 1-0 Yanga", self.wa("LIVE 6' GOOOL! Chama anafunga | Simba 1-0 Yanga"))
        self.assertIn("Simba 1-1 Yanga", self.wa("live matokeo Simba 1-1 Yanga"))
        self.wa("live 45+2' Kipindi cha kwanza kimeisha")
        updates = list(a.live_updates.order_by("created_at", "id").values_list("label", "text"))
        self.assertEqual(updates, [("1'", "Mechi imeanza!"), ("6'", "GOOOL! Chama anafunga"),
                                   ("45+2'", "Kipindi cha kwanza kimeisha")])

        status = self.wa("live")
        self.assertIn("Inaendelea: Simba vs Yanga", status)
        self.assertIn("live mwisho", status)

        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn("Simba 1-1 Yanga", page)
        self.assertIn("GOOOL! Chama anafunga", page)

        self.assertIn("LIVE imeisha", self.wa("live mwisho"))
        a.refresh_from_db()
        self.assertFalse(a.is_live)
        self.assertIn("Hakuna LIVE inayoendelea", self.wa("live 90' Bado?"))

    @override_settings(AGENT_DEFAULT_RECIPIENT="255750910158", AGENT_OWNER_NUMBERS=["255750910158", "255629712678"])
    def test_both_of_williams_numbers_can_report(self):
        self.assertIn("LIVE imeanza", self.wa("live anza Simba vs Yanga", phone="0750910158"))
        self.assertIn("Sasisho limewekwa", self.wa("live 3' Kona ya kwanza", phone="+255 629 712 678"))
        self.assertEqual(self.wa("live 4' Bandia", phone="255712345678"), "")    # namba ya mfano: haikubaliwi
        self.assertEqual(Article.objects.get().live_updates.count(), 1)

    def test_strangers_cannot_post_and_other_messages_are_untouched(self):
        self.assertEqual(self.wa("live anza Mechi bandia", phone="255700000999"), "")
        self.assertFalse(Article.objects.exists())
        self.assertEqual(self.wa("Habari za leo?"), "")              # si amri — kimya kama zamani
        self.assertFalse(Article.objects.exists())

    def test_telegram_works_the_same(self):
        sent = []
        with mock.patch("core.views_telegram.send_telegram", side_effect=lambda chat, text: sent.append(text)):
            for text in ("live anza Azam vs Namungo", "live 12' Penalti kwa Azam | Azam 1-0 Namungo"):
                r = self.client.post("/agent/telegram/", json.dumps({"message": {"chat": {"id": 42}, "text": text}}),
                                     content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="tg-secret")
                self.assertEqual(r.status_code, 200)
        self.assertIn("LIVE imeanza: Azam vs Namungo", sent[0])
        self.assertIn("Azam 1-0 Namungo", sent[1])
        self.assertEqual(Article.objects.get().live_updates.get().label, "12'")


def _fixture(fid=101, status="NS", elapsed=None, hg=None, ag=None, events=None, league=39, kickoff=None):
    """Mechi moja kama API-Football inavyoirudisha (/fixtures)."""
    kickoff = kickoff or (timezone.now() + timedelta(minutes=1))
    return {
        "fixture": {"id": fid, "date": kickoff.isoformat(), "status": {"short": status, "elapsed": elapsed},
                    "venue": {"name": "Emirates Stadium", "city": "London"}},
        "league": {"id": league, "name": "Premier League", "country": "England", "logo": "https://media.example/39.png",
                   "round": "Regular Season - 9"},
        "teams": {"home": {"id": 42, "name": "Arsenal", "logo": "https://media.example/42.png"},
                  "away": {"id": 49, "name": "Chelsea", "logo": "https://media.example/49.png"}},
        "goals": {"home": hg, "away": ag},
        "events": events or [],
    }


class _Resp:
    def __init__(self, items, errors=None, remaining="90"):
        self.items, self.errors, self.headers = items, errors, {"x-ratelimit-requests-remaining": remaining}

    def raise_for_status(self):
        pass

    def json(self):
        return {"errors": self.errors or [], "response": self.items}


@override_settings(API_FOOTBALL_KEY="test-key", API_FOOTBALL_LEAGUES=["39"], API_FOOTBALL_DAILY_LIMIT=100,
                   API_FOOTBALL_LIVE_SECONDS=0, SITE_URL="https://www.wlife.online")
class FootballAutoLiveTest(TestCase):
    """Njia A: mechi za kimataifa zinajiendesha kutoka API-Football (news/football.py)."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None) if target.endswith("_image") else mock.patch(target)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def run_with(self, *responses):
        from news import football
        queue = list(responses)

        from news.models import FootballState
        FootballState.objects.update(live_at=None)   # kila run_with = tick baada ya muda wa kuangalia mechi kufika

        def fake_get(url, params=None, headers=None, timeout=None):
            self.calls.append(params)
            self.assertEqual(headers["x-apisports-key"], "test-key")
            return queue.pop(0)
        with mock.patch("news.football.requests.get", side_effect=fake_get):
            return football.run()

    def test_a_match_runs_itself_from_preview_to_final_score(self):
        from news.models import FootballState, LiveMatch
        # Ratiba (ligi 140 haikuchaguliwa), kisha mechi inakaribia kuanza — inaangaliwa tick ile ile
        out = self.run_with(_Resp([_fixture(), _fixture(fid=999, league=140)]), _Resp([_fixture()]))
        self.assertEqual(out["failed"], 0, out)
        a = Article.objects.get()
        self.assertEqual(a.title, "Arsenal dhidi ya Chelsea LIVE: Matokeo, Kikosi na Takwimu — Premier League")
        self.assertEqual((a.status, a.category, a.origin), ("published", "dunia", "football"))
        self.assertIn("Emirates Stadium, London", a.body)
        self.assertIsNone(a.live_started_at)

        hub = self.client.get("/live/").content.decode()
        self.assertIn("Mechi zinazofuata", hub)
        self.assertIn('data-match="%d"' % a.pk, hub)
        self.assertIn('"@type": "CollectionPage"', hub)
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn("sb sb-big", page)
        self.assertIn("Mechi bado haijaanza", page)

        goal = {"time": {"elapsed": 23, "extra": None}, "team": {"id": 42, "name": "Arsenal"},
                "player": {"id": 7, "name": "B. Saka"}, "assist": {"id": 8, "name": "M. Ødegaard"},
                "type": "Goal", "detail": "Normal Goal"}
        card = {"time": {"elapsed": 30, "extra": None}, "team": {"id": 49, "name": "Chelsea"},
                "player": {"id": 9, "name": "M. Caicedo"}, "assist": {}, "type": "Card", "detail": "Yellow Card"}
        self.run_with(_Resp([_fixture(status="1H", elapsed=31, hg=1, ag=0, events=[goal, card])]))
        a.refresh_from_db()
        self.assertTrue(a.is_live)
        self.assertEqual(a.live_score, "Arsenal 1 - 0 Chelsea")
        texts = list(a.live_updates.order_by("created_at", "id").values_list("label", "kind", "text"))
        self.assertEqual(texts[0][:2], ("1'", "whistle"))
        self.assertEqual(texts[1], ("23'", "goal", "⚽ GOLI! B. Saka ameifungia Arsenal. Pasi ya mwisho: M. Ødegaard. "
                                                   "(Arsenal 1-0 Chelsea)"))
        self.assertEqual(texts[2], ("30'", "card", "🟨 Kadi ya njano ameonyeshwa M. Caicedo (Chelsea)."))

        # Ombi lile lile tena: hakuna sasisho linalorudiwa
        self.run_with(_Resp([_fixture(status="1H", elapsed=33, hg=1, ag=0, events=[goal, card])]))
        self.assertEqual(a.live_updates.count(), 3)
        scores = self.client.get("/live/matokeo.json").json()
        self.assertEqual((scores["live"], scores["matches"][0]["hg"], scores["matches"][0]["clock"]), (1, 1, "33'"))
        from django.core.cache import cache
        cache.clear()                             # idadi ya LIVE kwenye menyu inakaa sekunde 60
        self.assertIn('class="nav-live on is-on"><i></i>LIVE<span class="n">1</span>',
                      self.client.get("/live/").content.decode())

        self.run_with(_Resp([_fixture(status="FT", elapsed=90, hg=2, ag=0, events=[goal, card, dict(
            goal, time={"elapsed": 88, "extra": None}, player={"id": 11, "name": "G. Martinelli"}, assist={},
            detail="Penalty")])]))
        a.refresh_from_db()
        self.assertFalse(a.is_live)
        self.assertEqual(a.title, "Arsenal 2-0 Chelsea: Matokeo, Magoli na Takwimu — Premier League")
        self.assertIn("## Magoli", a.body)
        self.assertIn("G. Martinelli ameifungia Arsenal kwa mkwaju wa penalti", a.body)
        self.assertEqual(LiveMatch.objects.get().status, "FT")
        page = self.client.get(a.get_absolute_url()).content.decode()
        event = next(d.get("about") for block in LiveCoverageTest._jsonld(None, page) for d in block
                     if d.get("@type") == "LiveBlogPosting")
        self.assertEqual((event["@type"], event["homeTeam"]["name"], event["location"]["name"]),
                         ("SportsEvent", "Arsenal", "Emirates Stadium"))
        cache.clear()
        self.assertIn("Matokeo ya hivi karibuni", self.client.get("/live/").content.decode())
        self.assertEqual(FootballState.objects.get().requests, 5)
        self.assertEqual(self.calls[1]["ids"], "101")

    def test_a_match_first_seen_finished_still_gets_its_goals(self):
        from news import football
        from news.models import LiveMatch
        m = LiveMatch(article=None)
        football._fill(m, _fixture())
        m.article = football._create_article(m, _fixture()["league"])
        m.save()
        goal = {"time": {"elapsed": 50, "extra": None}, "team": {"id": 49, "name": "Chelsea"},
                "player": {"id": 10, "name": "C. Palmer"}, "assist": {}, "type": "Goal", "detail": "Normal Goal"}
        football.apply(_fixture(status="FT", elapsed=90, hg=0, ag=1, events=[goal]), m)
        a = Article.objects.get()
        self.assertFalse(a.is_live)
        self.assertTrue(a.title.startswith("Arsenal 0-1 Chelsea"))
        self.assertIn("C. Palmer ameifungia Chelsea", a.body)

    def test_free_plan_without_ids_falls_back_to_live_and_finishes_matches(self):
        from news import football
        from news.models import FootballState, LiveMatch
        plan = _Resp([], errors={"plan": "Free plans do not have access to the Ids parameter."})
        goal = {"time": {"elapsed": 5, "extra": None}, "team": {"id": 42, "name": "Arsenal"},
                "player": {"id": 7, "name": "B. Saka"}, "assist": {}, "type": "Goal", "detail": "Normal Goal"}
        # ratiba; ids imekataliwa -> live (mechi inachezwa)
        out = self.run_with(_Resp([_fixture()]), plan, _Resp([_fixture(status="1H", elapsed=5, hg=1, ag=0, events=[goal])]))
        self.assertEqual(out["failed"], 0, out)
        self.assertEqual(self.calls[2]["live"], "39")
        state = FootballState.objects.get()
        self.assertEqual(state.live_mode, "live")
        a = Article.objects.get()
        self.assertTrue(a.is_live)
        # Tick inayofuata: haijaribu ids tena; mechi haipo kwenye 'live' -> imeisha -> inaulizwa kwa id
        self.calls.clear()
        self.run_with(_Resp([]), _Resp([_fixture(status="FT", elapsed=90, hg=1, ag=0, events=[goal])]))
        self.assertEqual([("live" in c, c.get("id")) for c in self.calls], [(True, None), (False, 101)])
        a.refresh_from_db()
        self.assertFalse(a.is_live)
        self.assertEqual(LiveMatch.objects.get().status, "FT")

    def test_live_mode_fetches_events_when_the_score_changes(self):
        from news.models import FootballState
        goal = {"time": {"elapsed": 30, "extra": None}, "team": {"id": 49, "name": "Chelsea"},
                "player": {"id": 10, "name": "C. Palmer"}, "assist": {}, "type": "Goal", "detail": "Normal Goal"}
        self.run_with(_Resp([_fixture()]), _Resp([_fixture(status="1H", elapsed=1, hg=0, ag=0)]))
        FootballState.objects.update(live_mode="live")
        a = Article.objects.get()
        # 'live' haina matukio; magoli yamebadilika -> ombi la id linaleta mfungaji
        self.calls.clear()
        self.run_with(_Resp([dict(_fixture(status="1H", elapsed=31, hg=0, ag=1), events=[])]),
                      _Resp([_fixture(status="1H", elapsed=31, hg=0, ag=1, events=[goal])]))
        self.assertEqual([c.get("id") for c in self.calls], [None, 101])
        self.assertTrue(a.live_updates.filter(kind="goal", text__contains="C. Palmer").exists())
        # hakuna kilichobadilika -> ombi 1 tu
        self.calls.clear()
        self.run_with(_Resp([dict(_fixture(status="1H", elapsed=35, hg=0, ag=1), events=[])]))
        self.assertEqual(len(self.calls), 1)

    def test_a_failed_request_is_not_retried_every_minute(self):
        from news import football
        self.run_with(_Resp([], errors={"token": "Error/Missing application key."}))
        self.assertEqual(len(self.calls), 1)
        self.run_with()                           # tick ya dakika inayofuata: hakuna ombi jipya
        self.assertEqual(len(self.calls), 1)

    @override_settings(API_FOOTBALL_LIVE_SECONDS=120)
    def test_free_plan_requests_are_spread_until_the_last_match_ends(self):
        from news import football
        self.run_with(_Resp([_fixture(kickoff=timezone.now() - timedelta(minutes=10)),
                             _fixture(fid=102, kickoff=timezone.now() + timedelta(hours=8))]),
                      _Resp([]))
        state = football._state()
        gap = football.live_interval(state, timezone.now()).total_seconds()
        self.assertGreater(gap, 300)              # ~10.5h / ~94 maombi
        self.assertLess(gap, 600)

    def test_no_key_does_nothing_and_api_errors_are_recorded(self):
        from news import football
        from news.models import FootballState
        with override_settings(API_FOOTBALL_KEY=""):
            self.assertIn("haijawekwa", football.run()["detail"])
        out = self.run_with(_Resp([], errors={"plan": "Free plans do not have access to this season"}))
        self.assertEqual(out["failed"], 1)
        self.assertIn("Free plans", FootballState.objects.get().error)
        self.assertFalse(Article.objects.exists())

    @override_settings(API_FOOTBALL_DAILY_LIMIT=3)
    def test_daily_quota_is_never_exceeded(self):
        from news.models import FootballState
        FootballState.objects.create(day=timezone.now().astimezone(dt_tz.utc).date(), requests=1)
        out = self.run_with()                     # hakuna jibu lililoandaliwa: ombi lolote lingevunja jaribio
        self.assertIn("yameisha", out["detail"])
        self.assertEqual(self.calls, [])


class LiveHubTest(TestCase):
    """Ukurasa wa /live/ na ubao wa matokeo kwa mechi za mhariri."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_editor_match_gets_scoreboard_hub_menu_and_sitemap(self):
        from news import live, live_chat
        with mock.patch("news.models.Article.render_cover"), mock.patch("news.indexnow.submit"):
            live_chat.from_chat("live anza Simba vs Yanga")
            live_chat.from_chat("live 6' GOOOL! Chama anafunga | Simba 1-0 Yanga")
        a = Article.objects.get()
        self.assertEqual((a.match.home, a.match.away, a.match.home_goals, a.match.away_goals), ("Simba", "Yanga", 1, 0))
        self.assertEqual(a.live_updates.get().kind, "goal")
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn("sb sb-big is-on", page)
        self.assertIn('"@type": "SportsEvent"', page)
        hub = self.client.get("/live/").content.decode()
        self.assertIn("Matokeo ya Mpira Leo", hub)
        self.assertIn('data-match="%d"' % a.pk, hub)
        home = self.client.get("/").content.decode()
        self.assertIn('class="ls ls-band"', home)
        self.assertIn('href="/live/"', home)
        self.assertIn("/live/</loc>", self.client.get("/sitemap.xml").content.decode())
        with mock.patch("news.indexnow.submit"):
            live.end(a)
        a.match.refresh_from_db()
        self.assertEqual(a.match.status, "FT")

    def test_empty_hub_still_renders(self):
        page = self.client.get("/live/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Mechi zikianza", page.content.decode())


class LiveDeleteTest(TestCase):
    """Mhariri anaondoa LIVE nzima au sasisho moja — za mkono na za automatic."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None) if target.endswith("_image") else mock.patch(target)
            p.start()
            self.addCleanup(p.stop)
        self.staff = User.objects.create_user("mhariri", "m@x.com", "p", is_staff=True)

    def test_staff_removes_an_update_and_a_whole_live_from_the_site(self):
        from news import live_chat
        live_chat.from_chat("live anza Simba vs Yanga")
        live_chat.from_chat("live 6' GOOOL! Chama | Simba 1-0 Yanga")
        live_chat.from_chat("live 7' Kosa la kuandika")
        a = Article.objects.get()
        act = f"/habari-admin/live/{a.pk}/"
        typo = a.live_updates.first()

        self.client.post(act, {"action": "delete_update", "update": typo.pk})      # si mhariri
        self.assertEqual(a.live_updates.count(), 2)
        self.client.force_login(self.staff)
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn("Ondoa LIVE hii", page)
        self.assertIn('name="action" value="delete_update"', page)
        self.client.post(act, {"action": "delete_update", "update": typo.pk})
        self.assertEqual(list(a.live_updates.values_list("label", flat=True)), ["6'"])

        r = self.client.post(act, {"action": "delete", "next": "https://evil.example/"})
        self.assertRedirects(r, "/live/", fetch_redirect_response=False)
        a.refresh_from_db()
        self.assertEqual(a.status, "rejected")
        self.assertFalse(a.is_live)
        self.client.logout()
        self.assertEqual(self.client.get(a.get_absolute_url()).status_code, 404)
        self.assertNotIn('data-match="%d"' % a.pk, self.client.get("/live/").content.decode())

    def test_chat_deletes_last_update_or_whole_live(self):
        from news import live_chat
        live_chat.from_chat("live anza Azam vs Namungo")
        live_chat.from_chat("live 3' Sasisho la kwanza")
        live_chat.from_chat("live 4' Limekosewa")
        self.assertIn("Sasisho limefutwa: 4' Limekosewa", live_chat.from_chat("live futa"))
        a = Article.objects.get()
        self.assertEqual(a.live_updates.count(), 1)
        self.assertIn("imeondolewa", live_chat.from_chat("live futa yote"))
        a.refresh_from_db()
        self.assertEqual(a.status, "rejected")

    @override_settings(API_FOOTBALL_KEY="k", API_FOOTBALL_LEAGUES=["39"], API_FOOTBALL_LIVE_SECONDS=0)
    def test_removed_automatic_match_is_not_recreated_or_updated(self):
        from news import football, live
        from news.models import LiveMatch
        fx = _fixture(kickoff=timezone.now() + timedelta(hours=2))
        with mock.patch("news.football.requests.get", return_value=_Resp([fx])):
            football.run()
        a = Article.objects.get()
        self.client.force_login(self.staff)
        hub = self.client.get("/live/").content.decode()
        self.assertIn('class="sb-del"', hub)
        live.remove(a)
        from news.models import FootballState
        FootballState.objects.all().delete()                      # ratiba isomwe tena
        with mock.patch("news.football.requests.get", return_value=_Resp([dict(fx, goals={"home": 1, "away": 0})])):
            football.run()
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(Article.objects.get().status, "rejected")
        self.assertIsNone(LiveMatch.objects.get().home_goals)



class MatchCoverTest(TestCase):
    """Mechi za API-Football zinapata picha yenye nembo halisi za timu, si sanaa ya mfumo."""

    def test_automatic_match_cover_uses_team_crests(self):
        import io
        from PIL import Image
        from news import covers, football
        from news.models import LiveMatch
        crest = io.BytesIO()
        Image.new("RGBA", (120, 120), (200, 16, 46, 255)).save(crest, "PNG")
        a = Article.objects.create(title="Arsenal vs Chelsea LIVE", category="dunia", excerpt="x", body="x",
                                   status="published", published_at=timezone.now(), origin="football")
        m = LiveMatch.objects.create(article=a, fixture_id=1, home="Arsenal", away="Chelsea",
                                     home_logo="https://media.example/42.png", away_logo="https://media.example/49.png",
                                     competition="Premier League", kickoff=timezone.now())
        fetched = []

        def fake_image(url):
            fetched.append(url)
            return crest.getvalue()
        with mock.patch("news.football._image", side_effect=fake_image), \
                mock.patch.object(covers, "render", side_effect=AssertionError("sanaa ya mfumo")), \
                mock.patch("news.indexnow.submit"):
            football.refresh_cover(m)
        a.refresh_from_db()
        self.assertEqual({u for u in fetched if u}, {"https://media.example/42.png", "https://media.example/49.png"})
        self.assertTrue(LiveMatch.objects.get().cover_ok)
        png = bytes(a.cover_png)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (1200, 630))
        self.assertEqual(Image.open(io.BytesIO(bytes(a.cover_thumb))).format, "WEBP")
        # nembo nyekundu iko kwenye picha (upande wa wenyeji)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(img.getpixel((300, 255)), (200, 16, 46))
        self.assertIn("?v=", a.art_url)

    def test_missing_logo_falls_back_to_initials_not_ai_art(self):
        from news import covers
        png = covers.render_match("Manchester United", "Chelsea", None, None, "Premier League", None, "", score="3 - 1")
        self.assertTrue(png.startswith(b"\x89PNG"))

    @override_settings()
    def test_css_link_carries_the_deploy_version(self):
        import os
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abc1234def"}):
            page = self.client.get("/kuhusu/").content.decode()
        self.assertRegex(page, r"news(\.[0-9a-f]+)?\.css\?v=abc1234d")


def _hl(mid=7001, desc="Not started", clock=None, score=None, events=None, league="Premier League",
        country="England", kickoff=None):
    """Mechi moja kama Highlightly inavyoirudisha (muundo wa github.com/Highlightly/world-cup-tracker)."""
    kickoff = kickoff or (timezone.now() + timedelta(minutes=1))
    raw = {"id": mid, "date": kickoff.isoformat(), "round": "Regular Season - 9",
           "country": {"name": country}, "league": {"id": 33973, "name": league, "logo": "https://hl.example/l.png"},
           "homeTeam": {"id": 1, "name": "Arsenal", "logo": "https://hl.example/1.png"},
           "awayTeam": {"id": 2, "name": "Chelsea", "logo": "https://hl.example/2.png"},
           "state": {"description": desc, "clock": clock, "score": {"current": score}},
           "venue": {"name": "Emirates Stadium", "city": "London"}}
    if events is not None:
        raw["events"] = events
    return raw


class _HLResp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.headers = body, status, {}

    def json(self):
        return self.body


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", API_FOOTBALL_LIVE_SECONDS=0,
                   HIGHLIGHTLY_LEAGUES=["England:Premier League", "*:CAF Champions League"],
                   SITE_URL="https://www.wlife.online")
class HighlightlyProviderTest(TestCase):
    """LIVE ya kujiendesha kupitia Highlightly — mtiririko ule ule, data ya mtoaji mwingine."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit"):
            p = mock.patch(target)
            p.start()
            self.addCleanup(p.stop)
        for target in ("news.football._image", "news.enrich.run"):      # taarifa za ziada: MatchEnrichmentTest
            p = mock.patch(target, return_value=None if target.endswith("_image") else 0)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def run_with(self, *responses):
        from news import football
        from news.models import FootballState
        FootballState.objects.update(live_at=None)
        queue = list(responses)

        def fake_get(url, params=None, headers=None, timeout=None):
            self.calls.append((url, params))
            self.assertEqual(headers["x-rapidapi-key"], "hl-key")
            return queue.pop(0)
        with mock.patch("news.highlightly.requests.get", side_effect=fake_get):
            return football.run()

    def test_match_runs_from_preview_to_final_with_scorers(self):
        from news.models import FootballState, LiveMatch
        schedule = _HLResp({"data": [_hl(), _hl(mid=7002, league="Premier League", country="Kenya")],
                            "pagination": {"totalCount": 2}})
        out = self.run_with(schedule, _HLResp([_hl(events=[])]))
        self.assertEqual(out["failed"], 0, out)
        self.assertTrue(self.calls[0][0].endswith("/matches"))
        self.assertEqual(self.calls[0][1]["date"], timezone.localdate().isoformat())
        a = Article.objects.get()                          # Premier League ya Kenya haikuchaguliwa
        self.assertEqual(a.sources[0]["publisher"], "Highlightly")
        self.assertEqual(a.title, "Arsenal dhidi ya Chelsea LIVE: Matokeo, Kikosi na Takwimu — Premier League")
        m = LiveMatch.objects.get()
        self.assertEqual((m.home_logo, m.venue, m.status), ("https://hl.example/1.png", "Emirates Stadium", "NS"))
        self.assertIn('"Arsenal"', FootballState.objects.get().sample)

        goal = {"type": "Goal", "time": "23", "team": {"id": 1, "name": "Arsenal"},
                "player": "B. Saka", "assist": "M. Ødegaard"}
        card = {"type": "Yellow Card", "time": "45+2", "team": {"id": 2, "name": "Chelsea"}, "player": "M. Caicedo"}
        sub = {"type": "Substitution", "time": "60", "team": {"id": 2, "name": "Chelsea"},
               "player": "J. Pedro", "substituted": "N. Jackson"}
        self.run_with(_HLResp([_hl(desc="Second half", clock=61, score="1 - 0", events=[goal, card, sub])]))
        self.assertTrue(self.calls[-1][0].endswith("/matches/7001"))
        a.refresh_from_db()
        self.assertTrue(a.is_live)
        self.assertEqual(a.live_score, "Arsenal 1 - 0 Chelsea")
        rows = list(a.live_updates.order_by("created_at", "id").values_list("label", "kind", "text"))
        self.assertEqual(rows[1], ("23'", "goal", "⚽ GOLI! B. Saka ameifungia Arsenal. Pasi ya mwisho: M. Ødegaard. "
                                                 "(Arsenal 1-0 Chelsea)"))
        self.assertEqual(rows[2], ("45+2'", "card", "🟨 Kadi ya njano ameonyeshwa M. Caicedo (Chelsea)."))
        self.assertEqual(rows[3][:2], ("60'", "sub"))
        self.assertIn("J. Pedro ↔ N. Jackson", rows[3][2])
        self.assertEqual(LiveMatch.objects.get().minute, 61)

        self.run_with(_HLResp([_hl(desc="Finished", clock=90, score="2 - 0", events=[goal, card, sub, dict(
            goal, time="88", player="G. Martinelli", assist=None, type="Penalty")])]))
        a.refresh_from_db()
        self.assertFalse(a.is_live)
        self.assertTrue(a.title.startswith("Arsenal 2-0 Chelsea: Matokeo"))
        self.assertIn("G. Martinelli ameifungia Arsenal kwa mkwaju wa penalti", a.body)
        self.assertEqual(LiveMatch.objects.get().status, "FT")

    def test_status_and_score_mapping(self):
        from news import highlightly
        cases = {"Not started": "NS", "To be announced": "NS", "First half": "1H", "Half time": "HT",
                 "Second half": "2H", "Extra time": "ET", "Penalties": "P", "Finished": "FT",
                 "Finished after extra time": "AET", "Finished after penalties": "PEN", "Postponed": "PST",
                 "Cancelled": "CANC", "Abandoned": "ABD"}
        for desc, code in cases.items():
            self.assertEqual(highlightly._status({"state": {"description": desc}}), code, desc)
        item = highlightly.normalize(_hl(desc="First half", clock="34", score="2 - 1"))
        self.assertEqual((item["goals"], item["fixture"]["status"]), ({"home": 2, "away": 1},
                                                                     {"short": "1H", "elapsed": 34}))
        self.assertEqual(highlightly.normalize(_hl())["goals"], {"home": None, "away": None})

    def test_rules_match_country_and_international_cups(self):
        from news import highlightly
        self.assertEqual(highlightly.rank(_hl()), 1)               # 0 ni ya timu zetu (Simba, Yanga...)
        self.assertIsNone(highlightly.rank(_hl(country="Kenya")))
        self.assertEqual(highlightly.rank(_hl(league="CAF Champions League", country="World")), 2)

    def test_api_errors_are_shown_and_not_retried_every_minute(self):
        from news.models import FootballState
        out = self.run_with(_HLResp({"message": "Invalid API key"}, status=401))
        self.assertEqual(out["failed"], 1)
        self.assertIn("Highlightly 401: Invalid API key", FootballState.objects.get().error)
        with mock.patch("news.leagues.refresh_standings", return_value=0), \
                mock.patch("news.fixtures.read_ahead", return_value=0), mock.patch("news.odds.fetch", return_value=0):
            self.run_with()                               # dakika inayofuata: hakuna ombi jipya la ratiba
        self.assertEqual(len(self.calls), 1)

    @override_settings(HIGHLIGHTLY_VIA_RAPIDAPI=True)
    def test_rapidapi_key_uses_the_rapidapi_host(self):
        from news import highlightly
        base, headers = highlightly._base_and_headers()
        self.assertEqual(base, "https://football-highlights-api.p.rapidapi.com")
        self.assertEqual(headers["x-rapidapi-host"], "football-highlights-api.p.rapidapi.com")


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="old-key", API_FOOTBALL_LEAGUES=["39"],
                   API_FOOTBALL_LIVE_SECONDS=0, HIGHLIGHTLY_LEAGUES=["England:Premier League"])
class ProviderSwitchTest(TestCase):
    """Mechi za API-Football hazitumwi kwa Highlightly (namba zao ni za mtoaji mwingine)."""

    def test_only_current_provider_matches_are_polled(self):
        from news import football
        from news.models import LiveMatch
        with mock.patch("news.indexnow.submit"):
            old = Article.objects.create(title="Lens vs Paris FC LIVE", category="dunia", excerpt="x", body="x",
                                         status="published", published_at=timezone.now(), origin="football")
            LiveMatch.objects.create(article=old, fixture_id=1388012, provider="api-football", home="Lens",
                                     away="Paris FC", kickoff=timezone.now() - timedelta(minutes=30))
            new = Article.objects.create(title="Arsenal vs Chelsea LIVE", category="dunia", excerpt="x", body="x",
                                         status="published", published_at=timezone.now(), origin="football")
            LiveMatch.objects.create(article=new, fixture_id=1340772559, provider="highlightly", home="Arsenal",
                                     away="Chelsea", kickoff=timezone.now() - timedelta(minutes=10))
        self.assertEqual(football.provider(), "highlightly")
        self.assertEqual([m.fixture_id for m in football.active(timezone.now())], [1340772559])

    def test_scan_note_reports_a_cut_schedule(self):
        from news import highlightly
        highlightly._scan.update(pages=15, total=1800, rows=1500)
        self.assertIn("IMEKATIKA", highlightly.last_scan_note())
        highlightly._scan.update(pages=9, total=870, rows=870)
        self.assertEqual(highlightly.last_scan_note(), "kurasa 9, mechi 870/870")


def _hl_game(h, a, hg, ag, date="2026-09-01T18:00:00Z", hid=1, aid=2):
    return {"id": 9, "date": date, "league": {"name": "Premier League"}, "homeTeam": {"id": hid, "name": h},
            "awayTeam": {"id": aid, "name": a}, "state": {"description": "Finished", "score": {"current": f"{hg} - {ag}"}}}


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", HIGHLIGHTLY_LEAGUES=["England:Premier League"])
class MatchEnrichmentTest(TestCase):
    """Takwimu, vikosi, uso kwa uso, fomu na video kwa ukurasa wa mechi (news/enrich.py)."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import FootballState, LiveMatch
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit"):
            p = mock.patch(target)
            p.start()
            self.addCleanup(p.stop)
        self.a = Article.objects.create(title="Arsenal dhidi ya Chelsea LIVE: Matokeo, Kikosi na Takwimu — Premier League",
                                        category="dunia", excerpt="x", body="Awali.", status="published",
                                        published_at=timezone.now(), origin="football")
        self.m = LiveMatch.objects.create(article=self.a, fixture_id=1340772559, provider="highlightly",
                                          home="Arsenal", away="Chelsea", competition="Premier League",
                                          kickoff=timezone.now() + timedelta(hours=3),
                                          extra={"home_id": 1, "away_id": 2, "league_id": 33973})
        self.state = FootballState.objects.create(day=timezone.now().astimezone(dt_tz.utc).date())
        self.calls = []

    def serve(self, routes):
        def fake_get(url, params=None, headers=None, timeout=None):
            path = url.split(".net", 1)[1]
            self.calls.append((path, params))
            for prefix, resp in routes.items():
                if path.startswith(prefix):
                    return resp
            return _HLResp({"message": "Not found"}, status=404)
        return mock.patch("news.highlightly.requests.get", side_effect=fake_get)

    def test_match_details_bring_stats_referee_and_weather(self):
        from news import football, highlightly
        raw = _hl(mid=1340772559, desc="Second half", clock=70, score="1 - 0", events=[])
        raw.update(referee={"name": "Michael Oliver"}, forecast={"temperature": "14°C", "status": "Mawingu"},
                   statistics=[{"team": {"id": 2}, "statistics": [{"displayName": "Possession", "value": 0.38},
                                                                  {"displayName": "Expected Goals", "value": 0.7},
                                                                  {"displayName": "Corners", "value": 3}]},
                               {"team": {"id": 1}, "statistics": [{"displayName": "Possession", "value": 0.62},
                                                                  {"displayName": "Expected Goals", "value": 1.91},
                                                                  {"displayName": "Corners", "value": 7}]}])
        with mock.patch("news.football._image", return_value=None):
            football.apply(highlightly.normalize(raw), self.m)
        self.m.refresh_from_db()
        x = self.m.extra
        self.assertEqual(x["referee"], "Michael Oliver")
        self.assertEqual(x["stats"][0], {"name": "Umiliki wa mpira", "home": "62%", "away": "38%", "pct": 62})
        self.assertEqual(x["stats"][1]["name"], "Nafasi za kufunga zilizotengenezwa (xG)")
        self.assertEqual((x["stats"][1]["home"], x["stats"][1]["away"]), ("1.91", "0.7"))
        self.assertIn({"label": "Umiliki wa mpira",
                       "text": "Arsenal wameutawala mpira kwa muda mwingi zaidi: Arsenal 62% – 38% Chelsea."}, self.m.insights)
        page = self.client.get(self.a.get_absolute_url()).content.decode()
        for text in ("Uchambuzi wa wILife", "Takwimu", "Umiliki wa mpira", "Michael Oliver", "14°C"):
            self.assertIn(text, page)

    def test_pre_match_head_to_head_and_form_become_analysis(self):
        from news import enrich
        h2h = _HLResp([_hl_game("Arsenal", "Chelsea", 2, 0), _hl_game("Chelsea", "Arsenal", 1, 1, hid=2, aid=1),
                       _hl_game("Chelsea", "Arsenal", 2, 1, hid=2, aid=1)])
        home5 = _HLResp([_hl_game("Arsenal", "X", 3, 0), _hl_game("Y", "Arsenal", 0, 0, hid=9, aid=1)])
        away5 = _HLResp([_hl_game("Chelsea", "Z", 0, 1, hid=2, aid=8)])
        responses = iter([home5, away5])
        with self.serve({"/head-2-head": h2h, "/last-five-games": None}) as get:
            def route(url, params=None, headers=None, timeout=None):
                path = url.split(".net", 1)[1]
                self.calls.append((path, params))
                return h2h if path.startswith("/head-2-head") else next(responses)
            get.side_effect = route
            spent = enrich.run(self.state, timezone.now())
        self.assertEqual(spent, 3)
        self.assertEqual(self.calls[0][1], {"teamIdOne": 1, "teamIdTwo": 2})
        self.m.refresh_from_db()
        x = self.m.extra
        self.assertEqual(x["h2h_tally"], {"W": 1, "D": 1, "L": 1})
        self.assertEqual((x["form_home"], x["form_away"]), (["W", "D"], ["L"]))
        tips = self.m.insights
        self.assertIn({"label": "Walipokutana wao kwa wao",
                       "text": "Katika mechi 3 za mwisho kati ya Arsenal na Chelsea: Arsenal wameshinda 1, "
                               "Chelsea wameshinda 1, sare 1."}, tips)
        self.assertIn({"label": "Mechi 2 za mwisho za Arsenal",
                       "text": "Dhidi ya timu mbalimbali: ushindi 1, sare 1, kipigo 0."}, tips)
        self.a.refresh_from_db()
        self.assertIn("## Uchambuzi kabla ya mechi", self.a.body)
        self.assertIn("2026-09-01: Arsenal 2 - 0 Chelsea", self.a.body)
        page = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertIn("Historia ya kukutana", page)
        self.assertIn("Mechi 2 za mwisho za Arsenal", page)                    # jedwali la wapinzani
        self.assertIn('Arsenal ilishinda mara <b>1</b>', page)
        self.assertIn("<td>Y<small>Ugenini</small></td>", page)
        self.assertIn("- 01/09/26: Arsenal 3-0 X (Nyumbani) — Ushindi", self.a.body)
        self.assertIn('class="f-W"', page)

    def test_lineups_near_kickoff_and_highlights_after_full_time(self):
        from news import enrich
        self.m.kickoff = timezone.now() + timedelta(minutes=40)
        self.m.extra = {**self.m.extra, "h2h_done": True}
        self.m.save()
        lineup = _HLResp({"homeTeam": {"formation": "4-3-3", "initialLineup": [[{"name": "D. Raya", "number": 22,
                          "position": "Goalkeeper"}], [{"name": "W. Saliba", "number": 2}]],
                          "substitutes": [{"name": "K. Havertz", "number": 29}]},
                          "awayTeam": {"formation": "4-2-3-1", "initialLineup": [[{"name": "R. Sánchez", "number": 1}]]}})
        with self.serve({"/lineups/1340772559": lineup}):
            enrich.run(self.state, timezone.now())
        self.m.refresh_from_db()
        lu = self.m.extra["lineups"]
        self.assertEqual((lu["home"]["formation"], [p["name"] for p in lu["home"]["start"]]), ("4-3-3", ["D. Raya", "W. Saliba"]))
        self.assertEqual(lu["home"]["subs"][0]["name"], "K. Havertz")

        self.m.status = "FT"
        self.m.save()
        clips = _HLResp({"data": [{"title": "Arsenal 2-0 Chelsea | Highlights", "url": "https://youtu.be/abc",
                                   "embedUrl": "https://www.youtube.com/embed/abc", "imgUrl": "https://img.example/a.jpg",
                                   "type": "VERIFIED", "source": "youtube"},
                                  {"title": "bad", "url": "javascript:alert(1)"}]})
        with self.serve({"/highlights": clips}):
            enrich.run(self.state, timezone.now())
        self.m.refresh_from_db()
        self.assertEqual([v["url"] for v in self.m.extra["highlights"]], ["https://youtu.be/abc"])
        self.assertEqual(self.calls[-1][1]["matchId"], 1340772559)
        page = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertIn("▶ Video", page)
        self.assertIn('"@type": "VideoObject"', page)
        self.assertIn("https://www.youtube.com/embed/abc", page)
        self.assertIn("4-3-3", page)

    def test_a_missing_endpoint_is_switched_off_not_retried(self):
        from news import enrich
        self.m.kickoff = timezone.now() + timedelta(minutes=40)
        self.m.extra = {**self.m.extra, "h2h_done": True}
        self.m.save()
        with self.serve({}):
            enrich.run(self.state, timezone.now())
            self.m.refresh_from_db()
            self.assertIn("404", self.m.extra["lineups_error"])
            self.m.extra.pop("lineups_try", None)         # hata muda wa kujaribu ukifika: njia imezimwa
            self.m.save()
            enrich.run(self.state, timezone.now())
        self.assertEqual(len(self.calls), 1)


class SelfUpdatingPagesTest(TestCase):
    """Ukurasa wa mechi, /live/ na ukurasa wa mwanzo vinajibadilisha bila kurefresh."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import LiveMatch
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch("news.indexnow.submit")
        p.start()
        self.addCleanup(p.stop)
        self.a = Article.objects.create(title="Arsenal vs Chelsea LIVE", category="dunia", excerpt="x", body="x",
                                        status="published", published_at=timezone.now(), origin="football")
        self.m = LiveMatch.objects.create(article=self.a, fixture_id=1340772559, provider="highlightly", home="Arsenal",
                                          away="Chelsea", kickoff=timezone.now() + timedelta(hours=2))
        self.b = Article.objects.create(title="Real Madrid vs Barcelona LIVE", category="dunia", excerpt="x", body="x",
                                        status="published", published_at=timezone.now(), origin="football")
        LiveMatch.objects.create(article=self.b, fixture_id=1340772560, provider="highlightly", home="Real Madrid",
                                 away="Barcelona", kickoff=timezone.now() + timedelta(hours=3))

    def feed(self):
        return self.client.get(f"/habari/live/{self.a.pk}.json").json()

    def test_match_page_knows_when_to_reload_its_zones(self):
        page = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertIn('id="mz-body" data-zone data-v="', page)
        self.assertIn('data-active="1"', page)
        self.assertIn("news/match.", page)
        self.assertIn("Mechi nyingine za leo", page)                   # viungo vya ndani
        self.assertIn("Real Madrid", page)
        v1 = self.feed()["v"]                                          # mechi haijaanza bado ina feed
        self.assertEqual(v1, self.feed()["v"])                         # hakuna kilichobadilika
        self.m.extra = {"lineups": {"home": {"start": [{"name": "D. Raya"}]}}}
        self.m.save()
        v2 = self.feed()["v"]
        self.assertNotEqual(v1, v2)                                    # vikosi vimefika -> sehemu zibadilike
        from news import live
        live.start(self.a)
        self.assertNotEqual(v2, self.feed()["v"])                      # mechi imeanza

    def test_zone_reload_does_not_count_as_a_new_reader(self):
        self.client.get(self.a.get_absolute_url())
        self.client.get(self.a.get_absolute_url() + "?_=123")
        self.a.refresh_from_db()
        self.assertEqual(self.a.views, 1)

    def test_live_lists_carry_a_version(self):
        from news.models import LiveMatch
        hub = self.client.get("/live/").content.decode()
        self.assertIn('id="lv-zone" data-v="', hub)
        self.assertIn('id="lv-zone" data-v="', self.client.get("/").content.decode())
        v1 = self.client.get("/live/matokeo.json").json()["v"]
        LiveMatch.objects.filter(pk=self.m.pk).update(status="1H")
        from news import live
        live.start(self.a)
        self.assertNotEqual(v1, self.client.get("/live/matokeo.json").json()["v"])

    def test_old_finished_article_stops_asking(self):
        from news import live
        self.m.kickoff = timezone.now() - timedelta(days=2)
        self.m.status = "FT"
        self.m.save()
        live.start(self.a)
        Article.objects.filter(pk=self.a.pk).update(live_ended_at=timezone.now() - timedelta(days=2))
        page = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertIn('data-active="0"', page)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", API_FOOTBALL_LIVE_SECONDS=0,
                   HIGHLIGHTLY_LEAGUES=["England:Premier League", "Spain:La Liga"], API_FOOTBALL_MAX_PER_DAY=1,
                   SITE_URL="https://www.wlife.online")
class DayPagesTest(TestCase):
    """Mechi za leo / Matokeo ya jana / /mechi/<tarehe>/ — ratiba nzima, si mechi zenye LIVE tu."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("news.enrich.run", return_value=0)
        p.start()
        self.addCleanup(p.stop)

    def _scan(self):
        from news import football
        later = timezone.now() + timedelta(hours=2)
        rows = [_hl(), _hl(mid=7003, league="La Liga", country="Spain", kickoff=later),
                _hl(mid=7004, league="Serie A", country="Italy")]
        rows[1]["homeTeam"] = {"id": 5, "name": "Real Madrid", "logo": "https://hl.example/5.png"}
        rows[1]["awayTeam"] = {"id": 6, "name": "Barcelona", "logo": "https://hl.example/6.png"}
        with mock.patch("news.highlightly.requests.get",
                        return_value=_HLResp({"data": rows, "pagination": {"totalCount": 3}})):
            football.sync_schedule(football._state(), timezone.now())

    def test_whole_schedule_is_stored_and_listed_beyond_live_cap(self):
        from news.models import Fixture, LiveMatch
        self._scan()
        self.assertEqual(LiveMatch.objects.count(), 1)              # LIVE: mechi 1 tu (kikomo)
        self.assertEqual(sorted(Fixture.objects.values_list("fixture_id", flat=True)), [7001, 7003])
        page = self.client.get("/mechi-za-leo/").content.decode()
        self.assertIn("<title>Mechi za Leo ", page)
        for text in ("Arsenal", "Real Madrid", "Barcelona", "La Liga", '"@type": "SportsEvent"',
                     '<link rel="canonical" href="https://www.wlife.online/mechi-za-leo/">'):
            self.assertIn(text, page)
        self.assertIn(LiveMatch.objects.get().article.get_absolute_url(), page)   # mechi yenye LIVE ni link
        self.assertEqual(page.count('id="m-highlightly7001"'), 1)                   # haijirudii

    def test_date_urls_canonical_and_bounds(self):
        self._scan()
        today = timezone.localdate()
        page = self.client.get(f"/mechi/{today.isoformat()}/").content.decode()
        self.assertIn('<link rel="canonical" href="https://www.wlife.online/mechi-za-leo/">', page)
        self.assertEqual(self.client.get(f"/mechi/{(today + timedelta(days=1)).isoformat()}/").status_code, 404)
        self.assertEqual(self.client.get(f"/mechi/{(today - timedelta(days=9)).isoformat()}/").status_code, 404)
        self.assertEqual(self.client.get("/mechi/si-tarehe/").status_code, 404)
        self.assertEqual(self.client.get("/matokeo-ya-jana/").status_code, 200)

    def test_old_day_page_is_self_canonical_and_in_sitemap(self):
        from news.models import Fixture
        old = timezone.localdate() - timedelta(days=5)
        Fixture.objects.create(fixture_id=1, day=old, home="Simba", away="Yanga", competition="Ligi Kuu",
                               home_goals=1, away_goals=0, status="FT",
                               kickoff=timezone.now() - timedelta(days=5))
        url = f"/mechi/{old.isoformat()}/"
        page = self.client.get(url).content.decode()
        self.assertIn(f'<link rel="canonical" href="https://www.wlife.online{url}">', page)
        self.assertIn("Simba", page)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        for loc in (url, "/mechi-za-leo/", "/matokeo-ya-jana/", "/utabiri/"):
            self.assertIn(f"https://www.wlife.online{loc}</loc>", sitemap)

    def test_evening_refresh_fills_results_with_one_request_per_league(self):
        from news import fixtures, football
        from news.models import Fixture
        Fixture.objects.create(fixture_id=8001, day=timezone.localdate(), home="Real Madrid", away="Barcelona",
                               competition="La Liga", competition_id=140, kickoff=timezone.now() - timedelta(hours=3))
        done = _hl(mid=8001, desc="Finished", score="3 - 1", league="La Liga", country="Spain")
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(params)
            return _HLResp({"data": [done, _hl(mid=9999, desc="Finished", score="0 - 0")]})
        with mock.patch("news.highlightly.requests.get", side_effect=fake_get):
            spent = fixtures.refresh_results(football._state(), timezone.now(), 80)
            again = fixtures.refresh_results(football._state(), timezone.now(), 80)    # saa 3 hazijapita
        self.assertEqual((spent, again, len(calls)), (1, 0, 1))
        self.assertEqual(calls[0]["leagueId"], 140)
        f = Fixture.objects.get()
        self.assertEqual((f.home_goals, f.away_goals, f.status), (3, 1, "FT"))
        self.assertEqual(Fixture.objects.count(), 1)                                # 9999 haikuombwa


class PredictionTest(TestCase):
    """Makadirio (Poisson) kutoka fomu na uso kwa uso, na rekodi ya wazi baada ya mechi."""

    def setUp(self):
        from news.models import LiveMatch
        p = mock.patch("news.models.Article.render_cover")
        p.start()
        self.addCleanup(p.stop)
        self.a = Article.objects.create(title="Arsenal vs Chelsea LIVE", category="dunia", excerpt="x", body="b",
                                        status="published", published_at=timezone.now(), origin="football")
        strong = [{"home": "Arsenal", "away": t, "score": "3 - 0"} for t in "ABC"] + \
                 [{"home": "D", "away": "Arsenal", "score": "0 - 2"}]
        weak = [{"home": "Chelsea", "away": t, "score": "0 - 2"} for t in "ABC"] + \
               [{"home": "D", "away": "Chelsea", "score": "1 - 1"}]
        self.m = LiveMatch.objects.create(article=self.a, fixture_id=55, provider="highlightly", home="Arsenal",
                                          away="Chelsea", competition="Premier League",
                                          kickoff=timezone.now() + timedelta(hours=5),
                                          extra={"form_home_games": strong, "form_away_games": weak,
                                                 "form_home": ["W", "W", "W", "W"], "form_away": ["L", "L", "L", "D"]})

    def test_compute_favours_the_team_in_form_and_sums_to_100(self):
        from news import predict
        p = predict.ensure(self.m)
        self.assertEqual(p["home"] + p["draw"] + p["away"], 100)
        self.assertGreater(p["home"], 60)
        self.assertEqual(p["pick"], "1")
        self.assertRegex(p["score"], r"^\d-\d$")
        from news.enrich import insights
        self.assertIn("Utabiri wa wILife", " ".join(insights(self.m)))          # kwenye makala
        self.assertNotIn("Utabiri", [i["label"] for i in self.m.insights])      # ukurasani una kisanduku chake

    def test_home_side_with_better_results_and_record_is_not_the_underdog(self):
        """Wenyeji wenye matokeo bora na historia bora hawawi "wanyonge" kwa sababu wageni walishinda 4-0 mara moja."""
        from news import predict
        liv = [("1 - 0", 1), ("2 - 1", 1), ("1 - 0", 1), ("1 - 3", 1), ("2 - 1", 1)]
        bri = [("2 - 2", 1), ("3 - 1", 1), ("4 - 0", 1), ("2 - 1", 1), ("3 - 0", 1)]
        h2h = [("Brighton", "2 - 1", "Liverpool"), ("Liverpool", "3 - 0", "Brighton"), ("Liverpool", "2 - 0", "Brighton"),
               ("Brighton", "3 - 2", "Liverpool"), ("Liverpool", "2 - 1", "Brighton"), ("Brighton", "2 - 3", "Liverpool")]
        self.m.home, self.m.away = "Liverpool", "Brighton"
        self.m.extra = {
            "form_home_games": [{"home": "Liverpool", "away": f"T{i}", "score": sc} for i, (sc, _) in enumerate(liv)],
            "form_away_games": [{"home": "Brighton", "away": f"U{i}", "score": sc} for i, (sc, _) in enumerate(bri)],
            "form_home": ["W", "W", "W", "L", "W"], "form_away": ["D", "W", "W", "W", "W"],
            "h2h": [{"home": h, "away": a, "score": sc} for h, sc, a in h2h], "h2h_tally": {"W": 4, "D": 0, "L": 2}}
        p = predict.compute(self.m)
        self.assertGreater(p["home"], p["away"])
        self.assertEqual(p["home"] + p["draw"] + p["away"], 100)

    def test_not_enough_games_means_no_prediction(self):
        from news import predict
        self.m.extra = {"form_home_games": self.m.extra["form_home_games"][:2], "form_away_games": []}
        self.assertIsNone(predict.ensure(self.m))

    def test_prediction_is_frozen_at_kickoff_and_settled_after_final_whistle(self):
        from news import predict
        predict.ensure(self.m)
        made = self.m.extra["prediction"]
        self.m.status, self.m.home_goals, self.m.away_goals = "1H", 0, 0
        self.m.extra["form_home_games"] = []
        self.assertEqual(predict.ensure(self.m), made)            # mechi imeanza: hayabadiliki
        self.m.status, self.m.home_goals, self.m.away_goals = "FT", 2, 0
        self.m.save()
        predict.settle(self.m)
        self.m.refresh_from_db()
        self.assertTrue(self.m.extra["prediction_hit"])
        rec = predict.record()
        self.assertEqual((rec["total"], rec["hits"], rec["pct"]), (1, 1, 100))

    def test_predictions_page_and_match_panel(self):
        from news import predict
        predict.ensure(self.m)
        page = self.client.get("/utabiri/").content.decode()
        self.assertEqual(self.client.get("/makadirio/").status_code, 301)
        for text in ("Utabiri wa Mechi za Leo", "Arsenal", "18+", '"@type": "SportsEvent"', "Dixon–Coles"):
            self.assertIn(text, page)
        match_page = self.client.get(self.a.get_absolute_url()).content.decode()
        self.assertIn("Utabiri wa wILife", match_page)
        self.assertIn("Beti kwa uwajibikaji", match_page)


@override_settings(SITE_URL="https://www.wlife.online", AGENT_ENABLED=True, WHATSAPP_ENABLED=True,
                   WHATSAPP_PROVIDER="baileys", WHATSAPP_BRIDGE_URL="http://bridge", WHATSAPP_BRIDGE_KEY="k",
                   FOOTBALL_DIGEST_HOURS="8", NEWS_WHATSAPP_NUMBER="0629000000")
class FootballDigestTest(TestCase):
    """JIUNGE / ACHA na ujumbe wa kila siku wa michezo kwa wasomaji waliojiunga tu."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        today = timezone.localdate()
        Fixture.objects.create(fixture_id=1, day=today - timedelta(days=1), home="Simba", away="Yanga",
                               competition="Ligi Kuu", home_goals=2, away_goals=1, status="FT",
                               kickoff=timezone.now() - timedelta(days=1))
        Fixture.objects.create(fixture_id=2, day=today, home="Arsenal", away="Chelsea", competition="Premier League",
                               kickoff=timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=22))

    def _at(self, hour):
        today = timezone.localdate()
        return timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=hour, minutes=5)

    def test_join_and_leave_by_whatsapp(self):
        from news.models import Subscriber
        r = self.client.post("/agent/whatsapp/baileys/", data=json.dumps({"phone": "255711000111", "message": "Jiunge"}),
                             content_type="application/json", HTTP_X_BRIDGE_KEY="k")
        self.assertIn("Karibu wILife Michezo", r.json()["reply"])
        self.assertTrue(Subscriber.objects.get(phone="255711000111").active)
        from news import digest
        self.assertIsNone(digest.handle("255799999999", "acha"))         # si mwanachama: kimya
        self.assertIn("Umesitisha", digest.handle("0711000111", "ACHA"))
        self.assertFalse(Subscriber.objects.get().active)

    def test_daily_message_goes_once_per_slot_and_only_in_window(self):
        from news import digest
        from news.models import Subscriber
        Subscriber.objects.create(phone="255711000111")
        Subscriber.objects.create(phone="255711000222", active=False)
        with mock.patch("core.agent.whatsapp._send_via_bridge", return_value="id") as send, \
                mock.patch("time.sleep"):
            self.assertEqual(digest.run(now=self._at(6))["sent"], 0)          # kabla ya saa 2
            self.assertEqual(digest.run(now=self._at(8))["sent"], 1)
            self.assertEqual(digest.run(now=self._at(9))["sent"], 0)          # tayari ametumiwa
        number, text = send.call_args[0][:2]
        self.assertEqual(number, "255711000111")
        for part in ("MATOKEO YA JANA", "🏆 *Ligi Kuu*", "Simba  *2–1*  Yanga", "MECHI ZA LEO",
                     "`22:00`  Arsenal dhidi ya Chelsea",
                     "https://www.wlife.online/mechi-za-leo/", "ACHA"):
            self.assertIn(part, text)

    def test_bridge_down_does_not_skip_the_reader(self):
        from core.agent.whatsapp import WhatsAppError
        from news import digest
        from news.models import Subscriber
        Subscriber.objects.create(phone="255711000111")
        with mock.patch("core.agent.whatsapp._send_via_bridge", side_effect=WhatsAppError("ConnectionError")), \
                mock.patch("time.sleep"):
            out = digest.run(now=self._at(8))
        self.assertEqual(out["failed"], 1)
        sub = Subscriber.objects.get()
        self.assertEqual((sub.last_slot, sub.active, sub.failures), ("", True, 0))

    def test_join_box_shows_only_with_number(self):
        page = self.client.get("/mechi-za-leo/").content.decode()
        self.assertIn("https://wa.me/255629000000?text=JIUNGE", page)
        with self.settings(NEWS_WHATSAPP_NUMBER=""):
            from django.core.cache import cache
            cache.clear()
            self.assertNotIn("wa.me/?text=JIUNGE", self.client.get("/mechi-za-leo/").content.decode())
            self.assertNotIn("Jiunge WhatsApp", self.client.get("/mechi-za-leo/").content.decode())


@override_settings(SITE_URL="https://www.wlife.online", AGENT_ENABLED=True, WHATSAPP_ENABLED=True,
                   WHATSAPP_PROVIDER="baileys", WHATSAPP_BRIDGE_URL="http://bridge", WHATSAPP_BRIDGE_KEY="k",
                   FOOTBALL_DIGEST_HOURS="8", RESEND_API_KEY="re_x", AGENT_EMAIL_FROM="wILife <a@wlife.online>",
                   FOOTBALL_WHATSAPP_MAX=1, FOOTBALL_EMAIL_DAILY_MAX=3,
                   WHATSAPP_CHANNEL="https://whatsapp.com/channel/AbC123")
class DigestChannelsTest(TestCase):
    """Kikomo cha WhatsApp, email (thibitisha / jiondoe) na posti ya WhatsApp Channel."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        today = timezone.localdate()
        Fixture.objects.create(fixture_id=2, day=today, home="Arsenal", away="Chelsea", competition="Premier League",
                               kickoff=timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=22))
        self.at8 = timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=8, minutes=5)

    def test_full_whatsapp_points_to_channel_and_email(self):
        from news import digest
        from news.models import Subscriber
        Subscriber.objects.create(phone="255711000111")
        reply = digest.handle("255722000222", "JIUNGE")
        self.assertIn("imefika kikomo", reply)
        self.assertIn("https://whatsapp.com/channel/AbC123", reply)
        self.assertIn("Soka360", reply)
        page = self.client.get("/utabiri/").content.decode()
        self.assertIn("Fuatilia Soka360", page)
        self.assertIn("soka360-96", page)
        self.assertIn("https://www.wlife.online/michezo/jiunge/", reply)
        self.assertFalse(Subscriber.objects.filter(phone="255722000222").exists())
        self.assertIn("Tayari", digest.handle("0711000111", "jiunge"))          # aliyekuwemo hazuiwi

    def test_email_double_opt_in_and_unsubscribe(self):
        from news.models import EmailSubscriber
        with mock.patch("core.agent.email_channel.requests.post") as post:
            post.return_value = mock.Mock(status_code=200, json=lambda: {"id": "e1"})
            page = self.client.post("/michezo/jiunge/", {"email": "Msomaji@Mfano.com"}).content.decode()
            again = self.client.post("/michezo/jiunge/", {"email": "msomaji@mfano.com"}).content.decode()
            bot = self.client.post("/michezo/jiunge/", {"email": "x@y.com", "website": "spam"})
        self.assertIn("Angalia barua pepe yako", page)
        self.assertIn("Kiungo kimeshatumwa", again)                                  # haitumwi mara mbili
        self.assertEqual(bot.status_code, 200)
        self.assertEqual(post.call_count, 1)
        sub = EmailSubscriber.objects.get()
        self.assertEqual((sub.email, sub.confirmed), ("msomaji@mfano.com", False))
        self.assertIn(f"/michezo/thibitisha/{sub.token}/", post.call_args.kwargs["json"]["text"])
        self.assertIn("Thibitisha barua pepe yangu", post.call_args.kwargs["json"]["html"])
        self.assertIn("Umefanikiwa kujiunga", self.client.get(f"/michezo/thibitisha/{sub.token}/").content.decode())
        sub.refresh_from_db()
        self.assertTrue(sub.confirmed)
        self.client.get(f"/michezo/acha/{sub.token}/")                           # programu za email: GET haiondoi
        sub.refresh_from_db()
        self.assertTrue(sub.active)
        self.assertIn("Umefanikiwa kujiondoa", self.client.post(f"/michezo/acha/{sub.token}/").content.decode())
        sub.refresh_from_db()
        self.assertFalse(sub.active)
        self.assertEqual(self.client.get("/michezo/thibitisha/si-token/").status_code, 404)

    def test_daily_run_posts_channel_once_and_emails_within_cap(self):
        from news import digest
        from news.models import DailyCount, EmailSubscriber
        for i in range(4):
            EmailSubscriber.objects.create(email=f"m{i}@mfano.com", confirmed=True)
        EmailSubscriber.objects.create(email="bado@mfano.com")                   # hajathibitisha
        bridge = mock.Mock(status_code=200, json=lambda: {"id": "120363000000@newsletter"})
        with mock.patch("core.agent.whatsapp.requests.get", return_value=bridge) as lookup, \
                mock.patch("core.agent.whatsapp._send_via_bridge", return_value="id") as send, \
                mock.patch("core.agent.email_channel.requests.post") as post, mock.patch("time.sleep"):
            post.return_value = mock.Mock(status_code=200, json=lambda: {"id": "e"})
            first = digest.run(now=self.at8)
            second = digest.run(now=self.at8 + timedelta(minutes=1))
        self.assertEqual(lookup.call_args.kwargs["params"], {"invite": "https://whatsapp.com/channel/AbC123"})
        self.assertEqual(send.call_count, 1)                                      # channel: posti moja tu
        self.assertEqual(send.call_args[0][0], "120363000000@newsletter")
        self.assertNotIn("ACHA", send.call_args[0][1])
        self.assertTrue(send.call_args[0][1].startswith("⚽ *SOKA360*"))
        self.assertEqual(post.call_count, 3)                                      # kikomo cha email cha siku
        self.assertEqual(DailyCount.get("email"), 3)
        mail = post.call_args.kwargs["json"]
        self.assertIn("/michezo/acha/", mail["headers"]["List-Unsubscribe"])
        self.assertTrue(mail["subject"].startswith("⚽ Ratiba ya mechi za leo: Arsenal dhidi ya Chelsea"))
        for part in ("Ratiba ya mechi za leo na matokeo ya jana", "Arsenal", "22:00", "Jiondoe", "Fuatilia mechi mubashara", "Soka360",
                     "soka360-96",
                     "https://whatsapp.com/channel/AbC123"):
            self.assertIn(part, mail["html"])
        self.assertNotIn("Dashibodi", mail["html"])                               # si email ya wakala
        self.assertIn("Jiondoe kwenye orodha: https://www.wlife.online/michezo/acha/", mail["text"])
        self.assertIn("channel: imechapishwa", first["detail"])
        self.assertEqual(second["sent"], 0)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", HIGHLIGHTLY_LEAGUES=["England:Premier League"])
class OverdueMatchTest(TestCase):
    """Mechi iliyokwama "73' LIVE" (maombi ya siku yaliisha) inapata matokeo ya mwisho baadaye, na haionyeshwi LIVE."""

    def setUp(self):
        from django.core.cache import cache
        from news import live
        from news.models import LiveMatch
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        self.a = Article.objects.create(title="Arsenal dhidi ya Chelsea LIVE", category="dunia", excerpt="x", body="b",
                                        status="published", published_at=timezone.now(), origin="football")
        self.m = LiveMatch.objects.create(article=self.a, fixture_id=7001, provider="highlightly", home="Arsenal",
                                          away="Chelsea", competition="Premier League", status="2H", minute=73,
                                          home_goals=1, away_goals=1, kickoff=timezone.now() - timedelta(hours=9))
        live.start(self.a)

    def test_stale_match_is_not_shown_as_live(self):
        self.assertTrue(self.m.is_stale)
        self.assertFalse(self.m.is_playing)
        self.assertEqual(self.m.clock, "Inasubiri matokeo")
        hub = self.client.get("/live/").content.decode()
        self.assertNotIn('class="n">1<', hub)                        # kitufe cha LIVE hakihesabu mechi hii
        self.assertNotIn("73'", hub)

    def test_overdue_match_gets_its_final_score_on_a_later_tick(self):
        from news import football
        football._state().__class__.objects.update(schedule_at=timezone.now())   # ratiba ya leo imeshasomwa
        done = _hl(mid=7001, desc="Finished", score="2 - 1", events=[], kickoff=self.m.kickoff)
        with mock.patch("news.highlightly.requests.get", return_value=_HLResp([done])) as get, \
                mock.patch("news.enrich.run", return_value=0), \
                mock.patch("news.leagues.refresh_standings", return_value=0), \
                mock.patch("news.fixtures.read_ahead", return_value=0), mock.patch("news.odds.fetch", return_value=0):
            out = football.run()
            again = football.run()                                      # imeisha: haiombwi tena
        self.assertIn("matokeo ya mwisho yaliyochelewa: maombi 1", out["detail"])
        self.assertEqual(get.call_count, 1)
        self.m.refresh_from_db()
        self.a.refresh_from_db()
        self.assertEqual((self.m.status, self.m.home_goals, self.m.away_goals), ("FT", 2, 1))
        self.assertIsNotNone(self.a.live_ended_at)
        self.assertNotIn("matokeo ya mwisho", again["detail"])


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", HIGHLIGHTLY_LEAGUES=["*:CAF Confederation Cup"],
                   SITE_URL="https://www.wlife.online", FOOTBALL_ONDEMAND_MAX=1)
class FixturePageTest(TestCase):
    """Kila mechi ya ratiba ina ukurasa: matokeo, matukio na takwimu zinaombwa msomaji akiufungua (ndani ya bajeti)."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        self.f = Fixture.objects.create(fixture_id=9100, day=timezone.localdate(), home="USM Alger", away="Panthers",
                                        competition="CAF Confederation Cup", status="NS",
                                        kickoff=timezone.now() - timedelta(minutes=50))

    def test_unknown_state_without_clock_is_not_shown_as_live(self):
        from news import highlightly
        raw = _hl(desc="Time to be defined")
        self.assertEqual(highlightly._status(raw), "NS")
        self.assertEqual(highlightly._status(_hl(desc="Delayed")), "NS")
        self.assertEqual(highlightly._status(_hl(desc="In progress", clock=12)), "LIVE")

    def test_row_links_to_fixture_page_which_fetches_details_once_within_budget(self):
        from news.models import Fixture
        page = self.client.get("/mechi-za-leo/").content.decode()
        url = self.f.get_absolute_url()
        self.assertIn(f'href="{url}"', page)
        goal = {"type": "Goal", "time": "23", "team": {"id": 1, "name": "USM Alger"}, "player": "A. Mehdi"}
        raw = _hl(mid=9100, desc="Second half", clock=61, score="1 - 0", events=[goal],
                  league="CAF Confederation Cup", country="Africa", kickoff=self.f.kickoff)
        raw["homeTeam"]["name"], raw["awayTeam"]["name"] = "USM Alger", "Panthers"
        raw.update(referee={"name": "J. Gomes"})
        with mock.patch("news.highlightly.requests.get", return_value=_HLResp([raw])) as get:
            body = self.client.get(url).content.decode()
            from django.core.cache import cache
            cache.clear()
            self.client.get(url)                                    # chini ya dakika 10: haiombwi tena
        self.assertEqual(get.call_count, 1)
        for text in ("USM Alger dhidi ya Panthers", "Matukio ya mechi", "GOLI! A. Mehdi", "J. Gomes",
                     '"@type": "SportsEvent"'):
            self.assertIn(text, body)
        f = Fixture.objects.get()
        self.assertEqual((f.status, f.home_goals, f.away_goals), ("2H", 1, 0))

    def test_budget_reached_means_no_request(self):
        from news.models import DailyCount
        DailyCount.bump("fixture-detail")
        with mock.patch("news.highlightly.requests.get") as get:
            body = self.client.get(self.f.get_absolute_url()).content.decode()
        get.assert_not_called()
        self.assertIn("bado hatujapata taarifa zake mpya", body)      # ilipangwa dakika 50 zilizopita

    def test_fixture_with_live_coverage_redirects_to_its_article(self):
        from news.models import LiveMatch
        a = Article.objects.create(title="USM Alger LIVE", category="afrika", excerpt="x", body="b",
                                   status="published", published_at=timezone.now(), origin="football")
        with mock.patch("news.models.Article.render_cover"):
            LiveMatch.objects.create(article=a, fixture_id=9100, provider="highlightly", home="USM Alger", away="Panthers")
        r = self.client.get(self.f.get_absolute_url())
        self.assertRedirects(r, a.get_absolute_url(), fetch_redirect_response=False)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", HIGHLIGHTLY_LEAGUES=["England:Premier League"],
                   SITE_URL="https://www.wlife.online")
class PastDayResultsTest(TestCase):
    """Siku zilizopita: matokeo ya mwisho tu, yanayojazwa hata ligi isiposaidia, na siku iliyokosekana inasomwa."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        self.yesterday = timezone.localdate() - timedelta(days=1)
        ko = timezone.now() - timedelta(hours=20)
        self.done = Fixture.objects.create(fixture_id=1, day=self.yesterday, home="Zamalek", away="APR",
                                           competition="CAF Champions League", status="FT",
                                           home_goals=1, away_goals=0, kickoff=ko)
        self.stuck = Fixture.objects.create(fixture_id=2, day=self.yesterday, home="Renaissance", away="Star Sport",
                                            competition="CAF Champions League", status="NS", kickoff=ko)

    def test_unfinished_matches_are_hidden_and_empty_days_are_not_linked(self):
        page = self.client.get("/matokeo-ya-jana/").content.decode()
        self.assertIn("Zamalek", page)
        self.assertNotIn("Renaissance", page)
        two_back = (self.yesterday - timedelta(days=1)).isoformat()
        self.assertNotIn(f"/mechi/{two_back}/", page)               # siku isiyo na data haina kitufe

    def test_results_fill_match_by_match_when_league_call_cannot(self):
        from news import fixtures, football
        final = _hl(mid=2, desc="Finished", score="2 - 2", events=[], kickoff=self.stuck.kickoff)
        with mock.patch("news.highlightly.requests.get", return_value=_HLResp([final])) as get:
            spent = fixtures.refresh_results(football._state(), timezone.now(), 80)
        self.assertEqual((spent, get.call_count), (1, 1))            # haina leagueId: ombi la mechi moja
        self.stuck.refresh_from_db()
        self.assertEqual((self.stuck.status, self.stuck.home_goals), ("FT", 2))
        self.assertIn("Arsenal", self.client.get("/matokeo-ya-jana/").content.decode())   # jina la API sasa

    def test_a_missing_past_day_is_read_once(self):
        from news import fixtures, football
        from news.models import Fixture
        two_back = self.yesterday - timedelta(days=1)
        Fixture.objects.create(fixture_id=3, day=timezone.localdate(), home="A", away="B", kickoff=timezone.now())
        raw = _hl(mid=50, desc="Finished", score="3 - 1", kickoff=timezone.now() - timedelta(days=2))
        with mock.patch("news.highlightly.requests.get",
                        return_value=_HLResp({"data": [raw], "pagination": {"totalCount": 1}})) as get:
            fixtures.backfill(football._state(), timezone.now(), 90)
            fixtures.backfill(football._state(), timezone.now(), 90)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["date"], two_back.isoformat())
        self.assertTrue(Fixture.objects.filter(fixture_id=50, status="FT").exists())
        self.assertEqual(self.client.get(f"/mechi/{Fixture.objects.get(fixture_id=50).day.isoformat()}/").status_code, 200)


class MatchWordingTenseTest(TestCase):
    """Maandishi yanaendana na wakati: kabla (itaanza), wakati (inaendelea), baada (ulichezwa)."""

    def test_body_follows_the_match(self):
        from news import football
        from news.models import LiveMatch
        with mock.patch("news.models.Article.render_cover"):
            a = Article.objects.create(title="t", category="dunia", excerpt="x", body="", status="published",
                                       published_at=timezone.now(), origin="football")
        m = LiveMatch(article=a, home="Simba", away="Yanga", competition="Ligi Kuu Bara", venue="Benjamin Mkapa",
                      kickoff=timezone.now(), home_goals=2, away_goals=1, status="FT")
        before = football._preview_body(m) + "\n\n## Uchambuzi kabla ya mechi\n\n- Uchambuzi"
        self.assertIn("Mechi itaanza", before)
        during = football._live_body(m, before)
        self.assertIn("unaoendelea sasa", during)
        self.assertIn("Mechi inaendelea sasa", during)
        self.assertNotIn("itaanza", during)
        self.assertIn("- Uchambuzi", during)                     # uchambuzi unabaki
        after = football._final_body(m, a)
        self.assertIn("imekamilika kwa matokeo ya Simba 2-1 Yanga", after)
        self.assertIn("ulichezwa", after)
        self.assertNotIn("itaanza", after)
        self.assertNotIn("utaonekana", after)

    def test_day_page_names_the_leagues_actually_played(self):
        from news.models import Fixture
        day = timezone.localdate() - timedelta(days=1)
        for i, comp in enumerate(["CAF Champions League", "La Liga"]):
            Fixture.objects.create(fixture_id=i + 1, day=day, home=f"H{i}", away=f"A{i}", competition=comp,
                                   status="FT", home_goals=1, away_goals=0, rank=i,
                                   kickoff=timezone.now() - timedelta(hours=20))
        page = self.client.get("/matokeo-ya-jana/").content.decode()
        self.assertIn("mechi 2 zilizochezwa", page)
        self.assertIn("CAF Champions League na La Liga", page)
        self.assertNotIn("zilizochezwa tarehe", page)
        self.assertNotIn("Premier League, La Liga, Serie A", page)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", API_FOOTBALL_LIVE_SECONDS=0,
                   HIGHLIGHTLY_LEAGUES=["England:Premier League", "*:CAF Champions League"],
                   HIGHLIGHTLY_FAVOURITE_TEAMS=["Simba", "Young Africans", "Azam", "Singida Fountain Gate"],
                   API_FOOTBALL_MAX_PER_DAY=1, SITE_URL="https://www.wlife.online")
class FavouriteTeamsAndBudgetTest(TestCase):
    """
    Simba ilicheza CAF Champions League bila LIVE (nafasi 5 zilijaa EPL) na mechi za jana zilibaki
    "Inasubiri matokeo" (LIVE ilitumia maombi yote). Timu zetu zinatangulia; matokeo yana akiba yake.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("news.enrich.run", return_value=0)
        p.start()
        self.addCleanup(p.stop)

    def _simba(self, mid=7100, desc="Not started", clock=None, score=None, kickoff=None, events=None):
        raw = _hl(mid=mid, desc=desc, clock=clock, score=score, league="CAF Champions League", country="World",
                  kickoff=kickoff, events=events)
        raw["homeTeam"] = {"id": 50, "name": "Simba SC", "logo": "https://hl.example/simba.png"}
        raw["awayTeam"] = {"id": 51, "name": "15 de Agosto", "logo": None}
        return raw

    def test_team_names_match_with_or_without_suffix(self):
        from news import highlightly
        for home in ("Simba", "Simba SC", "Simba S.C.", "simba sc", "Azam FC", "Young Africans SC",
                     "Singida Fountain Gate"):
            self.assertTrue(highlightly.is_favourite(home, "X"), home)
        for home in ("Simba Bhora", "Real Madrid", ""):
            self.assertFalse(highlightly.is_favourite(home, "X"), home)
        self.assertEqual(highlightly.rank(self._simba()), 0)

    def test_simba_gets_live_even_when_daily_cap_is_full(self):
        from news import football
        from news.models import Fixture, LiveMatch
        Article.objects.create(title="LIVE ya awali", origin="football", status="published",
                               published_at=timezone.now())              # nafasi ya siku (kikomo 1) imeshajaa
        rows = [_hl(), self._simba()]
        with mock.patch("news.highlightly.requests.get",
                        return_value=_HLResp({"data": rows, "pagination": {"totalCount": 2}})):
            football.sync_schedule(football._state(), timezone.now())
        self.assertEqual(list(LiveMatch.objects.values_list("home", flat=True)), ["Simba SC"])
        self.assertEqual(Fixture.objects.get(fixture_id=7100).rank, 0)
        page = self.client.get("/mechi-za-leo/").content.decode()
        self.assertLess(page.index("Simba SC"), page.index("Arsenal"))     # timu zetu juu kabisa

    def test_started_simba_fixture_without_live_is_promoted_and_followed(self):
        from news import football
        from news.models import Fixture, FootballState, LiveMatch
        start = timezone.now() - timedelta(minutes=10)
        f = Fixture.objects.create(provider="highlightly", fixture_id=7100, day=timezone.localdate(), rank=9,
                                   home="Simba SC", away="15 de Agosto", competition="CAF Champions League",
                                   competition_id=77, status="NS", kickoff=start)
        FootballState.objects.create(day=timezone.now().date(), schedule_at=timezone.now())   # ratiba imeshasomwa
        playing = self._simba(desc="First half", clock=8, score="0 - 0", kickoff=start, events=[])
        with mock.patch("news.highlightly.requests.get", return_value=_HLResp([playing])) as get:
            out = football.run()
        self.assertEqual(out["failed"], 0, out)
        self.assertIn("timu zetu: LIVE mpya 1", out["detail"])
        self.assertTrue(get.call_args.args[0].endswith("/matches/7100"))
        m = LiveMatch.objects.get(fixture_id=7100)
        self.assertEqual((m.status, m.minute, m.home_goals), ("1H", 8, 0))
        self.assertTrue(m.article.is_live)
        self.assertEqual(Fixture.objects.get(pk=f.pk).rank, 0)
        r = self.client.get(f.get_absolute_url())                         # ukurasa wa ratiba -> LIVE kamili
        self.assertRedirects(r, m.article.get_absolute_url(), fetch_redirect_response=False)

    def test_live_polling_leaves_requests_for_results(self):
        from news import football, fixtures
        from news.models import LiveMatch
        with mock.patch("news.highlightly.requests.get",
                        return_value=_HLResp({"data": [self._simba(kickoff=timezone.now())],
                                              "pagination": {"totalCount": 1}})):
            football.sync_schedule(football._state(), timezone.now())
        LiveMatch.objects.update(status="1H")
        state = football._state()
        state.requests = 40
        now = timezone.now()
        left = football.daily_limit() - football.RESERVE - 40 - 2 - fixtures.OFFLIVE_RESERVE
        horizon = (LiveMatch.objects.get().kickoff + timedelta(minutes=150) - now).total_seconds()
        got = football.live_interval(state, now).total_seconds()
        self.assertAlmostEqual(got, horizon / left, delta=2)

    def test_yesterday_without_results_is_rescanned_once(self):
        from news import fixtures, football
        from news.models import Fixture
        yday = timezone.localdate() - timedelta(days=1)
        kick = timezone.now() - timedelta(days=1)
        for i in range(3):
            Fixture.objects.create(provider="highlightly", fixture_id=8100 + i, day=yday, home=f"H{i}", away=f"A{i}",
                                   competition="Premier League", status="NS", kickoff=kick, rank=1)
        done = []
        for i in range(3):
            raw = _hl(mid=8100 + i, desc="Finished", score=f"{i} - 1", kickoff=kick)
            raw["homeTeam"]["name"], raw["awayTeam"]["name"] = f"H{i}", f"A{i}"
            done.append(raw)
        with mock.patch("news.highlightly.requests.get",
                        return_value=_HLResp({"data": done, "pagination": {"totalCount": 3}})) as get:
            spent = fixtures.rescan_day(football._state(), timezone.now(), 80)
            again = fixtures.rescan_day(football._state(), timezone.now(), 80)
        self.assertEqual((spent, again, get.call_count), (1, 0, 1))
        self.assertEqual(get.call_args.kwargs["params"]["date"], yday.isoformat())
        self.assertEqual(sorted(Fixture.objects.values_list("status", "home_goals")), [("FT", 0), ("FT", 1), ("FT", 2)])
        page = self.client.get(f"/mechi/{yday.isoformat()}/").content.decode()
        self.assertNotIn("Inasubiri matokeo", page)

    def test_rescan_needs_pending_matches_and_budget(self):
        from news import fixtures, football
        from news.models import Fixture
        yday = timezone.localdate() - timedelta(days=1)
        for i in range(3):
            Fixture.objects.create(provider="highlightly", fixture_id=8200 + i, day=yday, home=f"H{i}", away="A",
                                   status="NS", kickoff=timezone.now() - timedelta(days=1))
        with mock.patch("news.highlightly.requests.get") as get:
            self.assertEqual(fixtures.rescan_day(football._state(), timezone.now(), 30), 0)   # maombi machache
            Fixture.objects.filter(fixture_id=8200).update(status="FT")
            Fixture.objects.filter(fixture_id=8201).update(status="FT")
            self.assertEqual(fixtures.rescan_day(football._state(), timezone.now(), 80), 0)   # moja tu bila matokeo
        get.assert_not_called()

    def test_fixture_page_fetches_details_even_late_in_a_busy_day(self):
        from news import football
        from news.models import Fixture
        f = Fixture.objects.create(provider="highlightly", fixture_id=9300, day=timezone.localdate(), home="Coastal",
                                   away="KMC", competition="Premier League", status="NS",
                                   kickoff=timezone.now() - timedelta(minutes=30))
        state = football._state()
        state.requests = 75                         # zamani: <= 35 zilizobaki -> hakuna ombi kabisa
        state.save()
        raw = _hl(mid=9300, desc="First half", clock=30, score="1 - 0", kickoff=f.kickoff, events=[])
        raw["homeTeam"]["name"], raw["awayTeam"]["name"] = "Coastal", "KMC"
        with mock.patch("news.highlightly.requests.get", return_value=_HLResp([raw])) as get:
            self.client.get(f.get_absolute_url())
        get.assert_called_once()
        f.refresh_from_db()
        self.assertEqual((f.status, f.home_goals), ("1H", 1))

    def test_missing_logo_shows_a_crest_not_a_digit(self):
        from news.models import Fixture
        f = Fixture.objects.create(provider="highlightly", fixture_id=9400, day=timezone.localdate(), home="Simba SC",
                                   away="15 de Agosto", home_logo="https://hl.example/simba.png", status="FT",
                                   home_goals=1, away_goals=0, kickoff=timezone.now() - timedelta(days=1),
                                   extra={"final": True, "fetched_at": timezone.now().isoformat()})
        page = self.client.get(f.get_absolute_url()).content.decode()
        self.assertIn('<i aria-hidden="true">A</i>', page)             # "15 de Agosto" -> herufi, si tarakimu
        self.assertNotIn('aria-hidden="true">1', page)


class OverdueFixtureTest(TestCase):
    """Mechi isiyo na LIVE iliyopita muda wa kuanza bila data mpya: "Ilipangwa 14:30", si "14:30" tu."""

    def _fx(self, minutes_ago, status="NS", **kw):
        from news.models import Fixture
        return Fixture.objects.create(provider="highlightly", fixture_id=9500 + minutes_ago, day=timezone.localdate(),
                                      home="Inter", away="Fiorentina", competition="Serie A", status=status,
                                      kickoff=timezone.now() - timedelta(minutes=minutes_ago), **kw)

    def test_clock_and_day_row(self):
        hhmm = lambda f: timezone.localtime(f.kickoff).strftime("%H:%M")
        soon, started = self._fx(-60), self._fx(30)
        self.assertEqual(soon.clock, hhmm(soon))                         # bado haijaanza
        self.assertEqual(started.clock, f"Ilipangwa {hhmm(started)}")
        self.assertFalse(self._fx(2).is_overdue)                          # dakika 5 za kwanza: bado saa tu
        self.assertEqual(self._fx(300).clock, "Inasubiri matokeo")       # baada ya saa 4
        self.assertFalse(self._fx(40, status="1H", minute=40).is_overdue)
        self.assertFalse(self._fx(200, status="FT", home_goals=1, away_goals=0).is_overdue)
        page = self.client.get("/mechi-za-leo/").content.decode()
        self.assertIn(f"<small>Ilipangwa</small>{hhmm(started)}", page)

    def test_fixture_page_explains_missing_data(self):
        from news.models import Fixture
        f = self._fx(40)
        Fixture.objects.filter(pk=f.pk).update(extra={"fetched_at": timezone.now().isoformat()})  # hakuna ombi jipya
        page = self.client.get(f.get_absolute_url()).content.decode()
        self.assertIn("ilipangwa kuanza saa", page)
        self.assertIn("bado hatujapata taarifa zake mpya", page)
        self.assertNotIn("Jaribu tena baada ya muda mfupi", page)


class _SBResp(_HLResp):
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _sb_item(title="Arsenal - Chelsea", comp="ENGLAND: Premier League", hours_ago=3, embed=None, url=None, videos=None):
    """Kipengele kimoja cha feed ya ScoreBat (v3)."""
    when = (timezone.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    src = embed or "<div style='width:100%'><iframe src='https://www.scorebat.com/embed/v/abc123/?utm_source=api' frameborder='0' allowfullscreen></iframe></div>"
    return {"title": title, "competition": comp, "matchviewUrl": url or f"https://www.scorebat.com/embed/matchview/{abs(hash(title)) % 99999}/",
            "thumbnail": "https://www.scorebat.com/og/m/og1.jpeg", "date": when,
            "videos": videos if videos is not None else [{"id": "v1", "title": "Highlights", "embed": src}]}


@override_settings(SCOREBAT_TOKEN="sb-token", SCOREBAT_FEED="https://www.scorebat.com/video-api/v3/free-feed/",
                   SITE_URL="https://www.wlife.online")
class ScoreBatVideoTest(TestCase):
    """Video za magoli (ScoreBat): feed -> Highlight -> /video/, ukurasa wa video na kurasa za mechi."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def _sync(self, items):
        from news import scorebat
        with mock.patch("news.scorebat.requests.get",
                        return_value=_SBResp({"response": items})) as get:
            out = scorebat.run()
        return out, get

    def test_parse_keeps_only_scorebat_iframes(self):
        from news import scorebat
        evil = "<iframe src='https://evil.example/x'></iframe>"
        items = scorebat.parse({"response": [
            _sb_item(),
            _sb_item(title="Roma - Lazio", embed=evil),                        # host isiyo ScoreBat: inarukwa
            _sb_item(title="Napoli - Inter", embed="<iframe src=\"javascript:alert(1)\"></iframe>"),
            {"title": "bila tarehe", "videos": []},
        ]})
        self.assertEqual([i["title"] for i in items], ["Arsenal - Chelsea"])
        it = items[0]
        self.assertEqual((it["home"], it["away"], it["home_key"]), ("Arsenal", "Chelsea", "arsenal"))
        self.assertEqual(it["videos"], [{"title": "Highlights", "src": "https://www.scorebat.com/embed/v/abc123/?utm_source=api"}])
        v1 = scorebat.parse([{"title": "A - B", "embed": "<iframe src='https://www.scorebat.com/embed/g/1/'></iframe>",
                              "side1": {"name": "Atlético Madrid"}, "side2": {"name": "Real Betis FC"},
                              "date": "2026-10-24T18:00:00+0000", "url": "https://www.scorebat.com/x/"}])
        self.assertEqual((v1[0]["home_key"], v1[0]["away_key"]), ("atletico madrid", "real betis"))

    def test_sync_stores_once_per_period_and_needs_token(self):
        from news.models import Highlight
        out, get = self._sync([_sb_item(), _sb_item(title="Liverpool - Brighton")])
        self.assertEqual(out["sent"], 2, out)
        self.assertEqual(get.call_args.kwargs["params"], {"token": "sb-token"})
        out, get = self._sync([_sb_item()])
        get.assert_not_called()                                     # kipindi hiki (saa 2) kimeshasomwa
        self.assertEqual(Highlight.objects.count(), 2)
        self.assertTrue(Highlight.objects.get(home="Arsenal").slug.startswith("arsenal-vs-chelsea-"))
        with self.settings(SCOREBAT_TOKEN=""):
            from news import scorebat
            self.assertIn("SCOREBAT_TOKEN", scorebat.run()["detail"])

    def test_bad_feed_is_reported_not_crashing(self):
        from news import scorebat
        with mock.patch("news.scorebat.requests.get", return_value=_SBResp({"error": "invalid token"})):
            out = scorebat.run()
        self.assertIn("feed haikuwa na video", out["detail"])
        with mock.patch("news.scorebat.requests.get", side_effect=requests.ConnectionError("down")):
            from news.models import DailyCount
            DailyCount.objects.all().delete()
            out = scorebat.run()
        self.assertEqual(out["failed"], 1)

    def test_hub_and_detail_pages(self):
        self._sync([_sb_item(), _sb_item(title="Liverpool - Brighton", videos=[
            {"title": "Highlights", "embed": "<iframe src='https://www.scorebat.com/embed/v/l1/'></iframe>"},
            {"title": "Goal 1 - Salah", "embed": "<iframe src='https://www.scorebat.com/embed/v/l2/'></iframe>"}])])
        hub = self.client.get("/video/").content.decode()
        for text in ("<title>Video za Magoli na Highlights za Leo | wILife</title>", '"@type": "ItemList"',
                     "Liverpool vs Brighton", "Arsenal vs Chelsea", 'data-src="https://www.scorebat.com/embed/v/'):
            self.assertIn(text, hub)
        from news.models import Highlight
        h = Highlight.objects.get(home="Liverpool")
        page = self.client.get(h.get_absolute_url()).content.decode()
        for text in ("Liverpool vs Brighton Highlights: Video za Magoli Yote", '"@type": "VideoObject"',
                     '"embedUrl": "https://www.scorebat.com/embed/v/l1/"', "Goal 1 - Salah", "Shiriki WhatsApp",
                     "Nakili kiungo", f'<link rel="canonical" href="https://www.wlife.online{h.get_absolute_url()}">'):
            self.assertIn(text, page)
        self.assertNotRegex(hub, r'<iframe[^>]*scorebat')        # /video/: iframe inapakiwa msomaji akibonyeza tu
        self.assertNotIn("download", page.lower().split("<footer")[0].split("</header>")[-1])
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn(f"https://www.wlife.online{h.get_absolute_url()}</loc>", sitemap)
        self.assertIn("https://www.wlife.online/video/</loc>", sitemap)
        self.assertEqual(self.client.get("/video/hakuna/").status_code, 404)

    def test_finished_match_pages_show_their_video(self):
        from news.models import Fixture
        self._sync([_sb_item(title="Manchester United - Bournemouth", hours_ago=1)])
        f = Fixture.objects.create(provider="highlightly", fixture_id=9700, day=timezone.localdate(),
                                   home="Manchester United", away="Bournemouth", competition="Premier League",
                                   status="FT", home_goals=0, away_goals=2, kickoff=timezone.now() - timedelta(hours=3),
                                   extra={"final": True, "fetched_at": timezone.now().isoformat()})
        page = self.client.get(f.get_absolute_url()).content.decode()
        self.assertIn('class="vx"', page)
        self.assertIn('"@type": "VideoObject"', page)
        Fixture.objects.filter(pk=f.pk).update(status="NS", home_goals=None, away_goals=None)
        from django.core.cache import cache
        cache.clear()                                               # ukurasa una nakala ya sekunde 30
        page = self.client.get(f.get_absolute_url()).content.decode()
        self.assertNotIn('class="vx"', page)                        # haijaisha: hakuna video
        from news import scorebat
        from news.models import Highlight
        self.assertEqual(scorebat.match_page(Highlight.objects.get())["url"], f.get_absolute_url())

    def test_fixture_keeps_team_ids_for_later_analysis(self):
        from news import fixtures, football
        from news.models import Fixture
        item = {"fixture": {"id": 9800, "date": timezone.now().isoformat(), "status": {"short": "NS"}},
                "league": {"id": 77, "name": "CAF Champions League"},
                "teams": {"home": {"name": "Simba"}, "away": {"name": "15 de Agosto"}}, "goals": {},
                "_extra": {"home_id": 50, "away_id": 51, "league_id": 77, "season": 2026, "stats": [1]}}
        with self.settings(HIGHLIGHTLY_API_KEY="hl-key", HIGHLIGHTLY_LEAGUES=["*:CAF Champions League"]):
            fixtures.store([item], timezone.localdate())
            f = Fixture.objects.get(fixture_id=9800)
            self.assertEqual({k: f.extra[k] for k in ("home_id", "away_id")}, {"home_id": 50, "away_id": 51})
            self.assertNotIn("stats", f.extra)
            self.assertEqual(football._item_from_fixture(f)["_extra"]["home_id"], 50)


@override_settings(SCOREBAT_TOKEN="sb-token", SITE_URL="https://www.wlife.online")
class VideoSeoTest(TestCase):
    """SEO ya video: kichwa chenye matokeo, muhtasari wa kipekee, video-sitemap, og:video, ukurasa wa mwanzo."""

    def setUp(self):
        from django.core.cache import cache
        from news import scorebat
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        scorebat.store(scorebat.parse({"response": [_sb_item(title="Arsenal - Chelsea", hours_ago=2)]}))
        Fixture.objects.create(provider="highlightly", fixture_id=9900, day=timezone.localdate(), home="Arsenal FC",
                               away="Chelsea", competition="Premier League", status="FT", home_goals=2, away_goals=1,
                               kickoff=timezone.now() - timedelta(hours=3),
                               extra={"final": True, "events": [
                                   {"label": "23'", "kind": "goal", "text": "⚽ GOLI! B. Saka ameifungia Arsenal."},
                                   {"label": "40'", "kind": "card", "text": "🟨 Kadi ya njano"}]})

    def test_video_page_is_rich_and_unique(self):
        from news.models import Highlight
        h = Highlight.objects.get()
        page = self.client.get(h.get_absolute_url()).content.decode()
        for text in ("<title>Arsenal vs Chelsea 2-1 Highlights: Video za Magoli Yote | wILife</title>",
                     "Muhtasari wa mechi", "Matokeo ya mwisho: Arsenal FC 2-1 Chelsea.", "23&#x27; GOLI! B. Saka",
                     '<meta property="og:video" content="https://www.scorebat.com/embed/v/abc123/',
                     '<meta property="og:image:width" content="1200">', '"inLanguage": "sw"',
                     '"@type": "SportsEvent"', "Arsenal vs Chelsea highlights",
                     '<iframe src="https://www.scorebat.com/embed/v/abc123/'):   # ukurasa wa video: iframe moja kwa moja
            self.assertIn(text, page)
        self.assertNotIn("Kadi ya njano", page.split("Muhtasari wa mechi")[1].split("</section>")[0])

    def test_video_sitemap_and_robots(self):
        from news.models import Highlight
        h = Highlight.objects.get()
        xml = self.client.get("/video-sitemap.xml")
        self.assertEqual(xml["Content-Type"], "application/xml")
        body = xml.content.decode()
        for text in ('xmlns:video="http://www.google.com/schemas/sitemap-video/1.1"',
                     f"<loc>https://www.wlife.online{h.get_absolute_url()}</loc>",
                     "<video:thumbnail_loc>https://www.scorebat.com/og/m/og1.jpeg</video:thumbnail_loc>",
                     "<video:title>Arsenal vs Chelsea 2-1 Highlights: Video za Magoli Yote</video:title>",
                     "<video:player_loc>https://www.scorebat.com/embed/v/abc123/?utm_source=api</video:player_loc>",
                     "<video:family_friendly>yes</video:family_friendly>"):
            self.assertIn(text, body)
        self.assertIn("Sitemap: https://www.wlife.online/video-sitemap.xml",
                      self.client.get("/robots.txt").content.decode())

    def test_home_page_links_latest_videos_and_new_videos_are_pinged(self):
        from news import scorebat
        Article.objects.create(title="Habari ya leo", category="tanzania", status="published",
                               published_at=timezone.now(), body="Maelezo.")
        page = self.client.get("/").content.decode()
        self.assertIn("Video za magoli", page)
        self.assertIn("/video/arsenal-vs-chelsea-", page)
        with mock.patch("news.indexnow.submit") as submit:
            scorebat.store(scorebat.parse({"response": [_sb_item(title="Roma - Lazio")]}))
        urls = submit.call_args.args[0]
        self.assertIn("/video/", urls)
        self.assertTrue(any(u.startswith("/video/roma-vs-lazio-") for u in urls))


@override_settings(SITE_URL="https://www.wlife.online")
class TeamPageTest(TestCase):
    """Kurasa za timu: habari, mechi ijayo, matokeo, fomu, takwimu na video — automatic, bila kubuni."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        now = timezone.now()
        mk = lambda fid, home, away, hg, ag, days, status="FT", logo="": Fixture.objects.create(
            provider="highlightly", fixture_id=fid, day=timezone.localdate(), home=home, away=away,
            home_goals=hg, away_goals=ag, status=status, competition="Ligi Kuu", home_logo=logo,
            kickoff=now + timedelta(days=days))
        mk(1, "Simba", "Yanga", 2, 0, -10, logo="https://hl.example/simba.png")      # W
        mk(2, "Azam FC", "Simba SC", 1, 1, -6)                                      # D
        mk(3, "Simba", "Coastal Union", 0, 1, -3)                                   # L
        mk(4, "JKT Tanzania", "Kagera Sugar", 1, 0, -2)                             # si ya timu yoyote
        mk(5, "Simba", "15 de Agosto", None, None, 1, status="NS")                  # ijayo

    def test_simba_page_is_filled_automatically(self):
        from news.models import Team
        Article.objects.create(title="Simba SC yashinda kwa kishindo", category="tanzania", status="published",
                               published_at=timezone.now(), body="x", excerpt="Habari ya Simba SC.")
        Article.objects.create(title="Simba ashambulia ng'ombe Serengeti", category="tanzania", status="published",
                               published_at=timezone.now(), body="x", excerpt="Mnyama wa porini.")
        team = Team.objects.get(slug="simba")
        page = self.client.get(team.get_absolute_url()).content.decode()
        for text in ("<title>Simba SC Leo: Habari, Matokeo, Ratiba na Video za Magoli | wILife</title>",
                     "Mechi ijayo", "15 de Agosto", "Matokeo ya karibuni", "Simba SC yashinda kwa kishindo",
                     '"@type": "SportsTeam"', '"logo": "https://hl.example/simba.png"',
                     "Takwimu za mechi 3 zilizorekodiwa kwenye wILife"):
            self.assertIn(text, page)
        self.assertNotIn("Simba ashambulia", page)                  # simba = mnyama: si habari ya timu
        self.assertNotIn("Kagera Sugar", page)
        from news import teams
        s = teams.summary(team)
        self.assertEqual(s["stats"], {"P": 3, "W": 1, "D": 1, "L": 1, "GF": 3, "GA": 2})
        self.assertEqual(s["form"], ["L", "D", "W"])                # karibuni kwanza
        self.assertEqual(s["results"][1]["opponent"], "Azam FC")
        self.assertEqual(s["next"].away, "15 de Agosto")

    def test_taifa_stars_matches_only_the_national_team(self):
        from news import teams
        from news.models import Fixture, Team
        Fixture.objects.create(provider="highlightly", fixture_id=9, day=timezone.localdate(), home="Tanzania",
                               away="Kenya", home_goals=2, away_goals=0, status="FT",
                               kickoff=timezone.now() - timedelta(days=1))
        s = teams.summary(Team.objects.get(slug="taifa-stars"))
        self.assertEqual([x["opponent"] for x in s["results"]], ["Kenya"])   # si "JKT Tanzania"

    def test_hub_sitemap_links_and_inactive(self):
        from news.models import Fixture, Team
        hub = self.client.get("/timu/").content.decode()
        for name in ("Simba SC", "Young Africans (Yanga)", "Azam FC", "Taifa Stars"):
            self.assertIn(name, hub)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("https://www.wlife.online/timu/simba/</loc>", sitemap)
        f = Fixture.objects.get(fixture_id=1)
        page = self.client.get(f.get_absolute_url()).content.decode()
        self.assertIn('href="/timu/simba/"', page)                  # ukurasa wa mechi -> kurasa za timu
        self.assertIn('href="/timu/yanga/"', page)
        Team.objects.filter(slug="azam").update(active=False)
        self.assertEqual(self.client.get("/timu/azam/").status_code, 404)

    def test_team_videos(self):
        from news import scorebat, teams
        from news.models import Team
        scorebat.store(scorebat.parse({"response": [_sb_item(title="Simba SC - Yanga", hours_ago=4),
                                                    _sb_item(title="Arsenal - Chelsea")]}))
        vids = teams.videos(Team.objects.get(slug="simba"))
        self.assertEqual([v.name for v in vids], ["Simba SC vs Yanga"])


def _yt_resp(body, status=200):
    r = _SBResp(body, status)
    r.text = json.dumps(body)
    return r


@override_settings(YOUTUBE_API_KEY="yt-key", SITE_URL="https://www.wlife.online")
class YouTubeChannelTest(TestCase):
    """Video za chaneli RASMI za YouTube: Admin anabandika kiungo, kazi inasoma video mpya zinazofaa."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import VideoChannel
        cache.clear()
        self.addCleanup(cache.clear)
        VideoChannel.objects.all().delete()          # chaneli za data migration zina jaribio lake (YouTubeSeeded…)

    def _channel(self, **kw):
        from news.models import Team, VideoChannel
        return VideoChannel.objects.create(link="https://www.youtube.com/@SimbaRasmi", channel_id="UC" + "a" * 22,
                                           name="Simba SC Rasmi", team=Team.objects.get(slug="simba"), **kw)

    def test_parse_link_and_titles(self):
        from news import youtube
        self.assertEqual(youtube.parse_link("https://www.youtube.com/@SimbaRasmi"), {"forHandle": "@SimbaRasmi"})
        self.assertEqual(youtube.parse_link("https://youtube.com/channel/UC" + "b" * 22), {"id": "UC" + "b" * 22})
        self.assertIsNone(youtube.parse_link("https://example.com/simba"))
        for title, teams in (("Simba SC vs Yanga | Highlights", ("Simba SC", "Yanga")),
                             ("HIGHLIGHTS | SIMBA SC 2-0 YANGA SC", ("SIMBA SC", "YANGA SC")),
                             ("Simba dhidi ya Azam: Magoli yote", ("Simba", "Azam")),
                             ("Mazoezi ya Simba leo", ("", ""))):
            self.assertEqual(youtube.teams_from_title(title), teams, title)
        self.assertEqual(youtube._seconds("PT9M41S"), 581)
        self.assertEqual(youtube._seconds("PT1H2M"), 3720)

    def test_sync_keeps_only_public_embeddable_highlights(self):
        from news import youtube
        from news.models import Highlight, VideoChannel
        ch = self._channel()
        good, live, private, noembed, training = "AAAAAAAAAA1", "AAAAAAAAAA2", "AAAAAAAAAA3", "AAAAAAAAAA4", "AAAAAAAAAA5"
        sn = lambda t: {"title": t, "publishedAt": "2026-10-25T18:00:00Z",
                        "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"},
                                       "maxres": {"url": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}}}
        playlist = {"items": [{"contentDetails": {"videoId": v}, "snippet": sn(t)} for v, t in (
            (good, "HIGHLIGHTS | Simba SC 6-1 15 de Agosto"), (live, "LIVE: Simba vs Yanga highlights"),
            (private, "Magoli yote ya wiki"), (noembed, "Simba vs KMC highlights"), (training, "Mazoezi ya leo"))]}
        details = {"items": [
            {"id": good, "status": {"embeddable": True, "privacyStatus": "public"},
             "contentDetails": {"duration": "PT9M41S"}, "snippet": {**sn("HIGHLIGHTS | Simba SC 6-1 15 de Agosto"),
                                                                    "liveBroadcastContent": "none"}},
            {"id": live, "status": {"embeddable": True, "privacyStatus": "public"},
             "contentDetails": {"duration": "PT0S"}, "snippet": {**sn("LIVE"), "liveBroadcastContent": "live"}},
            {"id": private, "status": {"embeddable": True, "privacyStatus": "unlisted"},
             "contentDetails": {}, "snippet": sn("x")},
            {"id": noembed, "status": {"embeddable": False, "privacyStatus": "public"},
             "contentDetails": {}, "snippet": sn("x")}]}
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append((url, params))
            return _yt_resp(playlist if url.endswith("/playlistItems") else details)
        with mock.patch("news.youtube.requests.get", side_effect=fake_get):
            out = youtube.run()
            again = youtube.run()                                   # bado ndani ya saa: haisomwi tena
        self.assertEqual((out["sent"], out["failed"]), (1, 0), out)
        self.assertEqual(again["sent"], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["playlistId"], "UU" + "a" * 22)
        self.assertEqual(calls[0][1]["key"], "yt-key")
        self.assertNotIn(training, calls[1][1]["id"])              # si highlights: haikuombwa hata maelezo
        h = Highlight.objects.get()
        self.assertEqual((h.provider, h.home, h.away, h.duration, h.channel), ("youtube", "Simba SC", "15 de Agosto",
                                                                               581, "Simba SC Rasmi"))
        self.assertEqual(h.videos, [{"title": "Video", "src": f"https://www.youtube-nocookie.com/embed/{good}"}])
        self.assertEqual(h.thumbnail, "https://i.ytimg.com/vi/x/maxresdefault.jpg")
        page = self.client.get(h.get_absolute_url()).content.decode()
        for text in ("Video rasmi kutoka YouTube (Simba SC Rasmi)", "Tazama YouTube", '"duration": "PT9M41S"',
                     f'<iframe src="https://www.youtube-nocookie.com/embed/{good}"'):
            self.assertIn(text, page)
        self.assertIn("<video:duration>581</video:duration>", self.client.get("/video-sitemap.xml").content.decode())
        team_page = self.client.get("/timu/simba/").content.decode()
        self.assertIn(h.get_absolute_url(), team_page)              # chaneli ya timu -> ukurasa wa timu
        self.assertEqual(VideoChannel.objects.get(pk=ch.pk).error, "")

    def test_api_error_is_recorded_not_raised(self):
        from news import youtube
        from news.models import VideoChannel
        ch = self._channel()
        with mock.patch("news.youtube.requests.get",
                        return_value=_yt_resp({"error": {"message": "API key not valid"}}, status=400)):
            out = youtube.run()
        self.assertEqual(out["failed"], 1)
        self.assertIn("API key not valid", VideoChannel.objects.get(pk=ch.pk).error)
        with self.settings(YOUTUBE_API_KEY=""):
            self.assertIn("YOUTUBE_API_KEY", youtube.run()["detail"])

    def test_admin_resolves_link_and_rejects_bad_ones(self):
        from django.contrib.auth.models import User
        from news.models import VideoChannel
        boss = User.objects.create_superuser("bossyt", "b@x.com", "x")
        self.client.force_login(boss)
        url = "/admin/news/videochannel/add/"
        ok = _yt_resp({"items": [{"id": "UC" + "c" * 22, "snippet": {"title": "Young Africans SC"}}]})
        with mock.patch("news.youtube.requests.get", return_value=ok) as get, \
                mock.patch.dict("os.environ", {"STAFF_2FA_REQUIRED": "false"}):
            r = self.client.post(url, {"link": "https://www.youtube.com/@YangaRasmi", "highlights_only": "on",
                                       "active": "on"}, secure=True)
        self.assertIn(r.status_code, (302, 200))
        self.assertEqual(get.call_args.kwargs["params"]["forHandle"], "@YangaRasmi")
        ch = VideoChannel.objects.get()
        self.assertEqual((ch.channel_id, ch.name), ("UC" + "c" * 22, "Young Africans SC"))
        with mock.patch("news.youtube.requests.get", return_value=_yt_resp({"items": []})), \
                mock.patch.dict("os.environ", {"STAFF_2FA_REQUIRED": "false"}):
            r = self.client.post(url, {"link": "https://www.youtube.com/@HakunaKitu", "active": "on"}, secure=True)
        self.assertContains(r, "Chaneli haikupatikana")
        self.assertEqual(VideoChannel.objects.count(), 1)


@override_settings(YOUTUBE_API_KEY="yt-key")
class YouTubeSeededChannelsTest(TestCase):
    """Chaneli zilizowekwa kwenye code zinathibitishwa zenyewe key ikiwepo; kichwa cha Azam TV kinasomeka."""

    def test_seeded_channels_resolve_then_sync(self):
        from news import youtube
        from news.models import VideoChannel
        self.assertEqual(sorted(VideoChannel.objects.values_list("link", flat=True)), [
            "https://www.youtube.com/@YoungAfricansSCTV", "https://www.youtube.com/@azamtvtz",
            "https://www.youtube.com/@simbasctanzania255"])
        self.assertEqual(VideoChannel.objects.get(link__endswith="@simbasctanzania255").team.slug, "simba")
        ids = iter("UC" + c * 22 for c in "xyz")

        def fake_get(url, params=None, timeout=None):
            if url.endswith("/channels"):
                return _yt_resp({"items": [{"id": next(ids), "snippet": {"title": params["forHandle"]}}]})
            return _yt_resp({"items": []})
        with mock.patch("news.youtube.requests.get", side_effect=fake_get):
            out = youtube.run()
        self.assertEqual(out["failed"], 0, out)
        self.assertEqual(VideoChannel.objects.exclude(channel_id=None).count(), 3)
        self.assertIn("imethibitishwa", out["detail"])

    def test_azam_tv_title(self):
        from news import youtube
        self.assertEqual(youtube.teams_from_title("Simba 6-1 Mashujaa | Highlights | NBC Premier League 13/10/2026"),
                         ("Simba", "Mashujaa"))
        self.assertEqual(youtube.teams_from_title("JKT Tanzania 2-1 Namungo | Magoli | NBC Premier League - 29/08/2026"),
                         ("JKT Tanzania", "Namungo"))
        self.assertEqual(youtube.teams_from_title("Magoli | Namungo FC 2-1 Coastal Union | NBC Premier League | 24/10/2026"),
                         ("Namungo FC", "Coastal Union"))


@override_settings(YOUTUBE_API_KEY="yt-key", SITE_URL="https://www.wlife.online", YOUTUBE_SEARCHES_PER_DAY=80,
                   YOUTUBE_MIN_SUBSCRIBERS=100000)
class YouTubeMatchSearchTest(TestCase):
    """Kila mechi iliyokwisha kwenye mfumo inatafutiwa video — kutoka chaneli rasmi tu, inayochezeka Tanzania."""

    OFFICIAL, FAN, BLOCKED, GAME, SHORT = "BBBBBBBBBB1", "BBBBBBBBBB2", "BBBBBBBBBB3", "BBBBBBBBBB4", "BBBBBBBBBB5"

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture, VideoChannel
        cache.clear()
        self.addCleanup(cache.clear)
        VideoChannel.objects.all().delete()
        self.now = timezone.now()
        self.match = Fixture.objects.create(
            fixture_id=9901, day=timezone.localdate(), kickoff=self.now - timedelta(hours=3), status="FT",
            home="Arsenal", away="Chelsea", home_goals=2, away_goals=1, competition="Premier League", rank=1)
        self.calls = []

    def _video(self, vid, title, channel, blocked=None, duration="PT11M2S"):
        cd = {"duration": duration, **({"regionRestriction": {"blocked": blocked}} if blocked else {})}
        return {"id": vid, "status": {"embeddable": True, "privacyStatus": "public"}, "contentDetails": cd,
                "snippet": {"title": title, "channelId": channel, "channelTitle": channel,
                            "publishedAt": _iso_z(self.now - timedelta(minutes=40)), "liveBroadcastContent": "none",
                            "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"}}}}

    def _fake(self, videos, channels):
        def fake_get(url, params=None, timeout=None):
            self.calls.append((url.rsplit("/", 1)[-1], params))
            if url.endswith("/search"):
                return _yt_resp({"items": [{"id": {"videoId": v["id"]}, "snippet": {"title": v["snippet"]["title"]}}
                                           for v in videos]})
            if url.endswith("/videos"):
                ids = params["id"].split(",")
                return _yt_resp({"items": [v for v in videos if v["id"] in ids]})
            if url.endswith("/channels"):
                ids = params["id"].split(",")
                return _yt_resp({"items": [{"id": c, "snippet": {"title": t}, "statistics": {"subscriberCount": str(n)}}
                                           for c, (t, n) in channels.items() if c in ids]})
            return _yt_resp({"items": []})
        return fake_get

    def _standard(self):
        videos = [self._video(self.FAN, "Arsenal vs Chelsea 2-1 | HIGHLIGHTS reaction", "UCfan"),
                  self._video(self.BLOCKED, "Arsenal 2-1 Chelsea | Premier League Highlights", "UCnbc", blocked=["TZ"]),
                  self._video(self.GAME, "Arsenal vs Chelsea highlights", "UCgame"),
                  self._video(self.SHORT, "Arsenal vs Chelsea goals #shorts", "UCpl", duration="PT25S"),
                  self._video(self.OFFICIAL, "HIGHLIGHTS | Arsenal 2-1 Chelsea | Premier League", "UCpl")]
        channels = {"UCfan": ("Arsenal Fan TV", 1_500_000), "UCnbc": ("NBC Sports", 9_000_000),
                    "UCgame": ("eFootball Arsenal Gaming", 400_000), "UCpl": ("Premier League", 20_000_000)}
        return videos, channels

    def test_finished_match_gets_official_video_only(self):
        from news import youtube
        from news.templatetags.news_tags import match_video
        with mock.patch("news.youtube.requests.get", side_effect=self._fake(*self._standard())):
            out = youtube.run(now=self.now)
            again = youtube.run(now=self.now + timedelta(hours=13))     # tayari ina video: haitafutwi tena
        self.assertEqual(out["sent"], 1, out)
        self.assertEqual(again["sent"], 0, again)
        self.assertEqual([c[0] for c in self.calls], ["search", "videos", "channels"])
        q = self.calls[0][1]
        self.assertEqual((q["q"], q["regionCode"], q["videoEmbeddable"]), ("Arsenal vs Chelsea highlights", "TZ", "true"))
        h = match_video(self.match)
        self.assertIsNotNone(h)
        self.assertEqual((h.key, h.channel, h.home, h.away, h.competition),
                         (f"yt:{self.OFFICIAL}", "Premier League", "Arsenal", "Chelsea", "Premier League"))
        self.assertIn("youtube-nocookie.com/embed/" + self.OFFICIAL, h.main["src"])

    def test_untrusted_results_store_nothing_and_retry_once_later(self):
        from news import youtube
        from news.models import Highlight
        videos, channels = self._standard()
        videos = videos[:3]                                   # bila video ya chaneli rasmi
        with mock.patch("news.youtube.requests.get", side_effect=self._fake(videos, channels)):
            youtube.run(now=self.now)
            youtube.run(now=self.now + timedelta(hours=1))     # bado kabla ya saa 12: hapana
            youtube.run(now=self.now + timedelta(hours=10))    # saa 13 baada ya mechi: jaribio la pili
            youtube.run(now=self.now + timedelta(hours=20))    # majaribio yamekwisha
        self.assertFalse(Highlight.objects.exists())
        self.assertEqual([c[0] for c in self.calls].count("search"), 2)

    def test_too_early_and_daily_limit(self):
        from news import youtube
        from news.models import Fixture
        Fixture.objects.filter(pk=self.match.pk).update(kickoff=self.now - timedelta(minutes=90))
        with mock.patch("news.youtube.requests.get", side_effect=self._fake(*self._standard())):
            youtube.run(now=self.now)                          # chini ya saa 2: highlights bado hazijapakiwa
            self.assertEqual(self.calls, [])
            with self.settings(YOUTUBE_SEARCHES_PER_DAY=0):
                youtube.run(now=self.now + timedelta(hours=1))
            self.assertEqual(self.calls, [])

    def test_admin_channel_is_trusted_even_if_small(self):
        from news import youtube
        from news.models import VideoChannel
        VideoChannel.objects.create(link="https://www.youtube.com/@ndogo", channel_id="UCsmall", name="Klabu Ndogo TV",
                                    checked_at=self.now)
        videos = [self._video(self.OFFICIAL, "Arsenal vs Chelsea | Magoli yote", "UCsmall")]
        with mock.patch("news.youtube.requests.get", side_effect=self._fake(videos, {})):
            out = youtube.run(now=self.now)
        self.assertEqual(out["sent"], 1, out)
        self.assertNotIn("channels", [c[0] for c in self.calls])

    def test_aliases_and_region_rules(self):
        from news import youtube
        self.assertTrue(youtube._mentions("Yanga 2-0 Simba | Highlights", youtube._aliases("Young Africans")))
        self.assertFalse(youtube._mentions("Azam vs Simba highlights", youtube._aliases("Young Africans")))
        ok = {"status": {"embeddable": True, "privacyStatus": "public"}, "snippet": {}}
        self.assertTrue(youtube._playable(ok))
        self.assertFalse(youtube._playable({**ok, "contentDetails": {"regionRestriction": {"allowed": ["US"]}}}))
        self.assertTrue(youtube._playable({**ok, "contentDetails": {"regionRestriction": {"allowed": ["TZ", "KE"]}}}))
        self.assertFalse(youtube._playable({**ok, "contentDetails": {"regionRestriction": {"blocked": ["TZ"]}}}))

    def test_quota_error_is_recorded(self):
        from news import youtube
        with mock.patch("news.youtube.requests.get",
                        return_value=_yt_resp({"error": {"message": "quotaExceeded"}}, status=403)):
            out = youtube.run(now=self.now)
        self.assertEqual(out["failed"], 1, out)
        self.assertIn("utafutaji", out["detail"])


def _iso_z(dt):
    return dt.astimezone(dt_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@override_settings(WHATSAPP_CHANNEL="120363000000000000@newsletter", AGENT_ENABLED=True, WHATSAPP_ENABLED=True,
                   SITE_URL="https://www.wilife.online", SOKA360_POSTS=True, SOKA360_GAP_MINUTES=10,
                   SOKA360_RESULTS_PER_DAY=15, SOKA360_HIGHLIGHTS_PER_DAY=20, SOKA360_WINDOW_HOURS=48,
                   SOKA360_FOLLOW_URL="https://whatsapp.com/channel/0029VbEUHxl3GJOvK9RLS93F")
class Soka360ChannelTest(TestCase):
    """Channel ya Soka360: video yetu ya matokeo (MP4) + link za highlights rasmi zinazochezwa ndani ya WhatsApp."""

    def setUp(self):
        from news.models import Fixture, Highlight
        self.now = timezone.now()
        self.match = Fixture.objects.create(
            fixture_id=7701, day=timezone.localdate(), kickoff=self.now - timedelta(hours=26), status="FT",
            home="Simba SC", away="Young Africans", home_goals=2, away_goals=1, competition="NBC Premier League",
            rank=0, extra={"events": [{"label": "23'", "kind": "goal",
                                       "text": "⚽ GOLI! Jean Ahoua ameifungia Simba SC. (Simba SC 1-0 Young Africans)"}]})
        self.hl = Highlight.objects.create(
            provider="youtube", key="yt:CCCCCCCCCC1", slug="simba-vs-yanga", title="HIGHLIGHTS | Simba SC 2-1 Yanga",
            competition="NBC Premier League", home="Simba SC", away="Young Africans", home_key="simba",
            away_key="young africans", date=self.now - timedelta(hours=20), channel="Simba SC Tanzania",
            thumbnail="https://i.ytimg.com/vi/CCCCCCCCCC1/hqdefault.jpg",
            source_url="https://www.youtube.com/watch?v=CCCCCCCCCC1",
            videos=[{"title": "Video", "src": "https://www.youtube-nocookie.com/embed/CCCCCCCCCC1"}])

    def _run(self, at, **kw):
        from news import soka360
        with mock.patch("core.agent.whatsapp.send_channel_video", return_value="v1") as video, \
                mock.patch("core.agent.whatsapp.send_channel", return_value="t1") as text, \
                mock.patch("news.football._image", return_value=None), \
                mock.patch("news.match_video.render", return_value=b"mp4") as render:
            out = soka360.run(now=at, **kw)
        return out, video, text, render

    def test_posts_oldest_first_one_per_gap(self):
        from news.models import ChannelPost
        out, video, text, render = self._run(self.now)
        self.assertEqual((out["sent"], out["detail"]), (1, "result: Simba SC vs Young Africans"), out)
        channel, data, caption = video.call_args.args
        self.assertEqual((channel, data), ("120363000000000000@newsletter", b"mp4"))
        self.assertTrue(caption.startswith("🔥 KARIAKOO DERBY | FULL-TIME 🔥\n\n🔴⚪ Simba SC 2–1 Young Africans 🟢🟡\n"),
                        caption)
        self.assertIn("🏆 Competition: NBC Premier League", caption)
        self.assertIn("📊 MATOKEO NA TAKWIMU:\nhttps://www.wilife.online/", caption)
        self.assertIn("23' Jean Ahoua ameifungia Simba SC.", caption)
        self.assertIn("https://www.wilife.online/", caption)
        self.assertIn("📲 FOLLOW Soka360 kwa updates za soka kila siku:\nhttps://whatsapp.com/channel/", caption)
        self.assertIn("https://whatsapp.com/channel/0029VbEUHxl3GJOvK9RLS93F", caption)
        self.assertEqual(render.call_args.kwargs["site"], "www.wilife.online")

        out, video, text, _ = self._run(self.now + timedelta(minutes=5))     # nafasi kati ya posti
        self.assertEqual((out["sent"], video.called, text.called), (0, False, False))

        out, video, text, _ = self._run(self.now + timedelta(minutes=11))
        self.assertEqual(out["sent"], 1, out)
        body = text.call_args.args[1]
        self.assertTrue(body.startswith("🔥 KARIAKOO DERBY | FULL-TIME 🔥\n\n🔴⚪ Simba SC 2–1 Young Africans 🟢🟡\n"), body)
        self.assertIn("▶️ TAZAMA HIGHLIGHTS:\nhttps://www.youtube.com/watch?v=CCCCCCCCCC1", body)
        self.assertIn("⚽ Usipitwe na highlights za mechi nyingine!", body)
        self.assertIn("🏆 Competition: NBC Premier League", body)
        self.assertIn("https://whatsapp.com/channel/0029VbEUHxl3GJOvK9RLS93F", body)
        self.assertIn("(video rasmi ya Simba SC Tanzania)", body)
        preview = text.call_args.kwargs["preview"]
        self.assertEqual((preview["url"], preview["thumbnail"]),
                         ("https://www.youtube.com/watch?v=CCCCCCCCCC1", "https://i.ytimg.com/vi/CCCCCCCCCC1/hqdefault.jpg"))

        out, video, text, _ = self._run(self.now + timedelta(minutes=30))    # zote zimetumwa: hakuna kurudia
        self.assertEqual(out["detail"], "hakuna jipya")
        self.assertEqual(ChannelPost.objects.exclude(posted_at=None).count(), 2)

    def test_one_highlight_per_match(self):
        from news.models import ChannelPost, Fixture, Highlight
        Fixture.objects.all().delete()
        Highlight.objects.create(
            provider="youtube", key="yt:CCCCCCCCCC2", slug="simba-vs-yanga-2", title="Magoli | Simba SC 2-1 Yanga",
            competition="Azam TV", home="Simba SC", away="Young Africans", home_key="simba",
            away_key="young africans", date=self.now - timedelta(hours=19), channel="Azam TV",
            source_url="https://www.youtube.com/watch?v=CCCCCCCCCC2",
            videos=[{"title": "Video", "src": "https://www.youtube-nocookie.com/embed/CCCCCCCCCC2"}])
        sent = []
        for i in range(3):
            out, video, text, _ = self._run(self.now + timedelta(minutes=15 * i))
            sent += [c.args[1] for c in text.call_args_list]
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("CCCCCCCCCC1", sent[0])
        self.assertNotIn("Competition: Azam TV", sent[0])
        # posti za zamani (key ya video) hazirudiwi kwa mpangilio mpya
        ChannelPost.objects.all().delete()
        ChannelPost.objects.create(key="highlight:yt:CCCCCCCCCC1", kind="highlight", posted_at=self.now)
        ChannelPost.objects.create(key="highlight:yt:CCCCCCCCCC2", kind="highlight", posted_at=self.now)
        out, video, text, _ = self._run(self.now + timedelta(hours=1))
        self.assertEqual(out["detail"], "hakuna jipya")

    def test_derby_names_and_colours(self):
        from news import soka360
        self.assertEqual(soka360.headline("FC Barcelona", "Real Madrid", "La Liga", (1, 2)),
                         ("🔥 EL CLÁSICO | FULL-TIME 🔥", "🔵🔴 FC Barcelona 1–2 Real Madrid ⚪👑"))
        self.assertEqual(soka360.headline("Namungo FC", "Coastal Union", "NBC Premier League", (2, 1)),
                         ("🔥 NBC PREMIER LEAGUE | FULL-TIME 🔥", "Namungo FC 2–1 Coastal Union"))
        self.assertEqual(soka360.derby("Inter Miami", "AC Milan"), "")          # jina lote, si sehemu yake
        self.assertEqual(soka360.derby("Simba Queens", "Yanga Princess"), "")

    def test_headline_from_official_title_when_match_unknown(self):
        from news import soka360
        from news.models import Fixture, Highlight
        Fixture.objects.all().delete()
        h = Highlight.objects.create(
            provider="youtube", key="yt:DDDDDDDDDD1", slug="dodoma-jiji-vs-jkt", channel="Azam TV",
            title="Highlights | Dodoma Jiji 0-0 JKT Tanzania | NBC Premier League 27/10/2026", competition="Azam TV",
            home="Dodoma Jiji", away="JKT Tanzania", home_key="dodoma jiji", away_key="jkt tanzania",
            date=self.now - timedelta(hours=10), source_url="https://www.youtube.com/watch?v=DDDDDDDDDD1",
            videos=[{"title": "Video", "src": "https://www.youtube-nocookie.com/embed/DDDDDDDDDD1"}])
        body = soka360.highlight_text(h)
        self.assertTrue(body.startswith("🔥 NBC PREMIER LEAGUE | FULL-TIME 🔥\n\nDodoma Jiji 0–0 JKT Tanzania\n"), body)
        self.assertIn("🏆 Competition: NBC Premier League", body)

    def test_admin_send_now_buttons(self):
        from news import soka360
        from news.models import ChannelPost
        admin_user = User.objects.create_superuser("mmiliki", "m@example.tz", "x")
        self.client.force_login(admin_user)
        with mock.patch("core.agent.whatsapp.send_channel", return_value="t1") as text, \
                mock.patch("core.agent.whatsapp.send_channel_video", side_effect=RuntimeError("bridge HTTP 503: not connected")), \
                mock.patch("news.football._image", return_value=None), \
                mock.patch("news.match_video.render", return_value=b"mp4"):
            r = self.client.post("/admin/news/highlight/", {"action": "send_to_soka360",
                                                             "_selected_action": [self.hl.pk]}, follow=True)
            self.assertContains(r, "Simba SC vs Young Africans: imetumwa kwenye channel")
            self.assertTrue(text.called)
            r = self.client.post("/admin/news/fixture/", {"action": "send_to_soka360",
                                                           "_selected_action": [self.match.pk]}, follow=True)
            self.assertContains(r, "imeshindwa — bridge HTTP 503: not connected")
        self.assertIsNotNone(ChannelPost.objects.get(key=soka360.highlight_key(self.hl)).posted_at)
        self.assertIn("503", ChannelPost.objects.get(key=soka360.result_key(self.match)).error)
        with self.settings(WHATSAPP_CHANNEL=""):
            self.assertEqual(soka360.send_now(self.hl, "highlight")[1], "WHATSAPP_CHANNEL haijawekwa kwenye Render")

    def test_failure_retries_then_gives_up(self):
        from news import soka360
        from news.models import ChannelPost, Highlight
        Highlight.objects.all().delete()
        with mock.patch("news.match_video.render", side_effect=RuntimeError("ffmpeg haipo")), \
                mock.patch("news.football._image", return_value=None):
            for i in range(5):
                out = soka360.run(now=self.now + timedelta(minutes=i))
        post = ChannelPost.objects.get()
        self.assertEqual((post.tries, post.posted_at), (3, None))
        self.assertIn("ffmpeg", post.error)
        self.assertEqual(out["detail"], "hakuna jipya")

    def test_limits_window_and_switches(self):
        from news.models import Fixture
        Fixture.objects.filter(pk=self.match.pk).update(kickoff=self.now - timedelta(minutes=60))   # bado inachezwa/ni mapema
        with self.settings(SOKA360_HIGHLIGHTS_PER_DAY=0):
            out, video, text, _ = self._run(self.now)
        self.assertEqual(out["detail"], "hakuna jipya")
        with self.settings(WHATSAPP_CHANNEL=""):
            self.assertIn("WHATSAPP_CHANNEL", self._run(self.now)[0]["detail"])
        with self.settings(WHATSAPP_ENABLED=False):
            out, video, text, _ = self._run(self.now)
        self.assertEqual((out["sent"], video.called, text.called), (0, False, False))
        out, video, text, _ = self._run(self.now, deadline=time.monotonic() + 3)   # muda wa tick hautoshi video
        self.assertTrue(text.called)
        self.assertFalse(video.called)

    def test_render_real_video(self):
        import shutil
        from news import match_video
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg haipo")
        data = match_video.render(self.match, site="www.wilife.online")
        self.assertGreater(len(data), 10_000)
        self.assertEqual(data[4:8], b"ftyp")
        self.assertEqual(match_video.goals_of(self.match), [("23'", "Jean Ahoua ameifungia Simba SC.")])

    def test_bridge_payloads(self):
        from core.agent import whatsapp
        with self.settings(WHATSAPP_PROVIDER="baileys", WHATSAPP_BRIDGE_URL="http://bridge", WHATSAPP_BRIDGE_KEY="k"), \
                mock.patch("core.agent.whatsapp.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"id": "x"}
            whatsapp.send_channel_video("120363000000000000@newsletter", b"abc", "Maelezo")
            whatsapp.send_channel("120363000000000000@newsletter", "Habari https://y.t/v", preview={"url": "https://y.t/v"})
        (url1,), kw1 = post.call_args_list[0]
        (url2,), kw2 = post.call_args_list[1]
        self.assertEqual(url1, "http://bridge/send-video")
        self.assertEqual((kw1["json"]["video"], kw1["json"]["text"]), ("YWJj", "Maelezo"))
        self.assertEqual(url2, "http://bridge/send")
        self.assertEqual(kw2["json"]["preview"], {"url": "https://y.t/v"})


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", API_FOOTBALL_LIVE_SECONDS=0,
                   HIGHLIGHTLY_LEAGUES=["England:Premier League", "Spain:La Liga"], API_FOOTBALL_MAX_PER_DAY=1,
                   HIGHLIGHTLY_SCHEDULE_HOURS=24, SITE_URL="https://www.wlife.online", TIME_ZONE="Africa/Dar_es_Salaam")
class ScheduleRecoveryTest(TestCase):
    """'Mechi za leo' haibaki tupu siku nzima: ratiba inasomwa kila siku mpya ya EAT na kujaribiwa tena ikishindwa."""

    def setUp(self):
        from zoneinfo import ZoneInfo
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("news.models.Article.render_cover", "news.indexnow.submit", "news.football._image"):
            p = mock.patch(target, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        for target in ("news.enrich.run", "news.fixtures.rescan_day", "news.fixtures.refresh_results",
                       "news.fixtures.backfill", "news.football.promote_favourites", "news.football.finalize",
                       "news.football.sync_live", "news.fixtures.read_ahead", "news.leagues.refresh_standings",
                       "news.results.backfill", "news.odds.fetch"):
            p = mock.patch(target, return_value=0)
            p.start()
            self.addCleanup(p.stop)
        self.eat = ZoneInfo("Africa/Dar_es_Salaam")
        self.calls = []

    def _at(self, day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=self.eat)

    def _run(self, now, fail=False):
        from news import football

        def fake_get(url, params=None, headers=None, timeout=None):
            self.calls.append(params.get("date"))
            if fail:
                raise requests.ConnectionError("mtandao")
            kickoff = now.replace(hour=20, minute=0)
            return _HLResp({"data": [_hl(mid=8800 + now.day, kickoff=kickoff)], "pagination": {"totalCount": 1}})
        with mock.patch("news.highlightly.requests.get", side_effect=fake_get), \
                mock.patch("django.utils.timezone.now", return_value=now):
            return football.run(now=now)

    def test_failed_morning_read_is_retried_not_after_24h(self):
        from news.models import Fixture
        self._run(self._at(26, 3, 5), fail=True)
        self.assertEqual(self.calls, ["2026-10-26"])
        self._run(self._at(26, 3, 20))                             # chini ya dakika 30: subiri
        self.assertEqual(len(self.calls), 1)
        self._run(self._at(26, 3, 40))                             # jaribio jipya, linafaulu
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(Fixture.objects.filter(day=date(2026, 10, 26)).exists())
        self._run(self._at(26, 12, 34))                            # leo ina ratiba: hakuna kusoma tena
        self.assertEqual(len(self.calls), 2)

    def test_retries_are_capped(self):
        for minute in range(5, 60 * 4, 31):
            self._run(self._at(26, 3) + timedelta(minutes=minute), fail=True)
        self.assertEqual(len(self.calls), 1 + 4)

    def test_new_local_day_reads_at_midnight_once(self):
        from news.models import Fixture
        self._run(self._at(25, 15))                                # Jumapili mchana
        self._run(self._at(26, 0, 10))                             # Jumatatu 00:10 EAT (bado siku ya UTC ya jana)
        self.assertEqual(self.calls, ["2026-10-25", "2026-10-26"])
        self.assertTrue(Fixture.objects.filter(day=date(2026, 10, 26)).exists())
        self._run(self._at(26, 3, 5))                              # siku mpya ya UTC: haisomi ratiba tena
        self.assertEqual(len(self.calls), 2)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", SITE_URL="https://www.wlife.online",
                   HIGHLIGHTLY_LEAGUES=["England:Premier League", "Tanzania:Premier League"], LEAGUE_STANDINGS_MAX=8)
class LeaguePagesTest(TestCase):
    """Kurasa za ligi: zinaundwa kutoka ratiba, msimamo kutoka API (ombi 1 kwa ligi kwa siku), viungo na sitemap."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture
        cache.clear()
        self.addCleanup(cache.clear)
        now = timezone.now()
        self.epl = Fixture.objects.create(
            fixture_id=501, day=timezone.localdate(), kickoff=now + timedelta(hours=3), status="NS", home="Arsenal",
            away="Chelsea", competition="Premier League", competition_id=33, country="England", rank=1,
            extra={"season": 2026, "league_id": 33})
        self.tz = Fixture.objects.create(
            fixture_id=502, day=timezone.localdate() - timedelta(days=1), kickoff=now - timedelta(hours=20),
            status="FT", home="Simba SC", away="Young Africans", home_goals=2, away_goals=1,
            competition="Premier League", competition_id=77, country="Tanzania", rank=0, extra={"season": 2026})

    def test_sync_makes_distinct_pages_per_country(self):
        from news import leagues
        from news.models import League
        self.assertEqual(leagues.sync(), 2)
        self.assertEqual(leagues.sync(), 0)
        self.assertEqual(sorted(League.objects.values_list("slug", flat=True)),
                         ["premier-league-england", "premier-league-tanzania"])
        self.assertEqual(League.objects.get(competition_id=77).name, "Premier League (Tanzania)")

    def test_parse_standings_shapes(self):
        from news import leagues
        flat = [{"position": 2, "team": {"name": "Yanga", "logo": "https://x/y.png"}, "points": 20, "games": 9,
                 "wins": 6, "draws": 2, "loses": 1, "scoredGoals": 15, "receivedGoals": 5},
                {"position": 1, "team": {"name": "Simba"}, "points": 22, "played": 9, "won": 7, "drawn": 1, "lost": 1}]
        nested = {"league": {"name": "X"}, "groups": [{"name": "Kundi A", "standings": flat},
                                                       {"name": "Kundi B", "standings": flat}]}
        one = leagues.parse({"data": flat})
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["group"], "")
        self.assertEqual([r["team"] for r in one[0]["rows"]], ["Simba", "Yanga"])
        self.assertEqual((one[0]["rows"][1]["gd"], one[0]["rows"][0]["w"]), (10, 7))
        self.assertEqual([t["group"] for t in leagues.parse(nested)], ["Kundi A", "Kundi B"])
        self.assertEqual(leagues.parse({"message": "no data"}), [])

    def test_refresh_one_league_per_run_and_keeps_note(self):
        from news import football, leagues
        from news.models import League
        leagues.sync()
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append((url, params))
            if params["leagueId"] == 77:
                return _HLResp({"groups": [{"standings": [
                    {"position": 1, "team": {"name": "Simba SC"}, "points": 22, "games": 9},
                    {"position": 2, "team": {"name": "Young Africans"}, "points": 20, "games": 9}]}]})
            return _HLResp({"unexpected": True})
        state = football._state()
        now = timezone.now()
        with mock.patch("news.highlightly.requests.get", side_effect=fake_get):
            self.assertEqual(leagues.refresh_standings(state, now, 90), 1)
            self.assertEqual(leagues.refresh_standings(state, now, 90), 1)
            self.assertEqual(leagues.refresh_standings(state, now, 90), 0)            # zote zimesomwa leo
            self.assertEqual(leagues.refresh_standings(state, now + timedelta(days=1), 30), 0)   # maombi machache
        self.assertEqual([c[1]["leagueId"] for c in calls], [77, 33])               # kipaumbele: Tanzania (rank 0)
        self.assertTrue(calls[0][0].endswith("/standings"))
        self.assertEqual(calls[0][1]["season"], 2026)
        tz = League.objects.get(competition_id=77)
        self.assertEqual(tz.standings[0]["rows"][0]["team"], "Simba SC")
        self.assertIn("jibu halikueleweka", League.objects.get(competition_id=33).standings_note)
        self.assertEqual(leagues.standing_of(tz, "Simba")["pos"], 1)

    def test_pages_links_and_sitemap(self):
        from news import leagues
        from news.models import League
        leagues.sync()
        tz = League.objects.get(competition_id=77)
        League.objects.filter(pk=tz.pk).update(standings=[{"group": "", "rows": [
            {"pos": 1, "team": "Simba SC", "logo": "", "p": 9, "w": 7, "d": 1, "l": 1, "gf": 18, "ga": 4, "gd": 14,
             "pts": 22}]}])
        page = self.client.get("/ligi/premier-league-tanzania/").content.decode()
        for text in ("<title>Premier League (Tanzania): Msimamo wa Ligi", "Simba SC", "+14", "<b>22</b>",
                     '"@type": "SportsOrganization"', "Matokeo ya karibuni", "Young Africans"):
            self.assertIn(text, page)
        hub = self.client.get("/ligi/").content.decode()
        self.assertIn("/ligi/premier-league-england/", hub)
        day = self.client.get("/mechi-za-leo/").content.decode()
        self.assertIn('href="/ligi/premier-league-england/"', day)
        fx = self.client.get(self.epl.get_absolute_url()).content.decode()
        self.assertIn('href="/ligi/premier-league-england/"', fx)
        sm = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("https://www.wlife.online/ligi/premier-league-tanzania/", sm)
        self.assertIn("https://www.wlife.online" + self.epl.get_absolute_url(), sm)
        self.assertEqual(self.client.get("/ligi/haipo/").status_code, 404)


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", API_FOOTBALL_KEY="", SITE_URL="https://www.wlife.online",
                   HIGHLIGHTLY_LEAGUES=["Tanzania:Premier League"], TIME_ZONE="Africa/Dar_es_Salaam")
class TomorrowAndPreviewTest(TestCase):
    """Ratiba ya kesho (mara moja, maombi yakiwa mengi) na hakikisho la mechi kutoka data halisi tu."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_read_ahead_once_in_the_afternoon(self):
        from zoneinfo import ZoneInfo
        from news import fixtures, football
        from news.models import Fixture
        eat = ZoneInfo("Africa/Dar_es_Salaam")
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(params.get("date"))
            kickoff = datetime(2026, 10, 27, 19, 0, tzinfo=eat)
            return _HLResp({"data": [_hl(mid=9901, kickoff=kickoff, league="Premier League", country="Tanzania")],
                            "pagination": {"totalCount": 1}})
        state = football._state()
        with mock.patch("news.highlightly.requests.get", side_effect=fake_get), \
                mock.patch("news.indexnow.submit") as ping:
            self.assertEqual(fixtures.read_ahead(state, datetime(2026, 10, 26, 11, 0, tzinfo=eat), 90), 0)  # asubuhi
            self.assertEqual(fixtures.read_ahead(state, datetime(2026, 10, 26, 16, 0, tzinfo=eat), 40), 0)  # maombi machache
            self.assertEqual(fixtures.read_ahead(state, datetime(2026, 10, 26, 16, 0, tzinfo=eat), 90), 1)
            self.assertEqual(fixtures.read_ahead(state, datetime(2026, 10, 26, 18, 0, tzinfo=eat), 90), 0)  # mara moja
        self.assertEqual(calls, ["2026-10-27"])
        f = Fixture.objects.get(fixture_id=9901)
        self.assertEqual(f.day, date(2026, 10, 27))
        self.assertIn(f.get_absolute_url(), ping.call_args.args[0])
        self.assertIn("/mechi-za-kesho/", ping.call_args.args[0])

    def test_tomorrow_page_and_preview(self):
        from news.models import Fixture, League
        now = timezone.now()
        tomorrow = timezone.localdate() + timedelta(days=1)
        f = Fixture.objects.create(
            fixture_id=9902, day=tomorrow, kickoff=now + timedelta(days=1), status="NS", home="Simba SC",
            away="Young Africans", competition="Premier League", competition_id=77, country="Tanzania", rank=0,
            extra={"venue": "Benjamin Mkapa", "city": "Dar es Salaam"})
        Fixture.objects.create(fixture_id=9800, day=timezone.localdate() - timedelta(days=5),
                               kickoff=now - timedelta(days=5), status="FT", home="KMC", away="Simba SC",
                               home_goals=0, away_goals=3, competition="Premier League", competition_id=77,
                               country="Tanzania", rank=0)
        Fixture.objects.create(fixture_id=9801, day=timezone.localdate() - timedelta(days=4),
                               kickoff=now - timedelta(days=4), status="FT", home="Simba Queens", away="JKT Queens",
                               home_goals=0, away_goals=1, competition="Women", competition_id=78,
                               country="Tanzania", rank=5)
        League.objects.create(competition_id=77, competition="Premier League", country="Tanzania",
                              name="Ligi Kuu Tanzania", slug="ligi-kuu-tanzania", standings=[{"group": "", "rows": [
                                  {"pos": 1, "team": "Simba", "pts": 22, "p": 9},
                                  {"pos": 2, "team": "Yanga", "pts": 20, "p": 9}]}])
        page = self.client.get("/mechi-za-kesho/").content.decode()
        self.assertIn("<title>Mechi za Kesho ", page)
        self.assertIn(f.get_absolute_url(), page)
        self.assertIn('href="/mechi-za-kesho/"', self.client.get("/mechi-za-leo/").content.decode())
        fx = self.client.get(f.get_absolute_url()).content.decode()
        self.assertIn("<title>Simba SC vs Young Africans: Hakikisho, Saa ya Mechi na Msimamo", fx)
        self.assertIn("Simba SC watakuwa wenyeji wa Young Africans", fx)
        self.assertIn("uwanjani Benjamin Mkapa, Dar es Salaam", fx)
        self.assertIn("Simba SC wako nafasi ya 1 wakiwa na pointi 22 baada ya mechi 9", fx)
        self.assertIn("Young Africans wako nafasi ya 2 wakiwa na pointi 20", fx)          # Yanga = Young Africans
        self.assertIn("dhidi ya KMC (ugenini) 3-0", fx)
        self.assertNotIn("JKT Queens", fx)                                                  # si Simba Queens
        r = self.client.get(f"/mechi/{tomorrow.isoformat()}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/mechi/{(tomorrow + timedelta(days=1)).isoformat()}/").status_code, 404)


@override_settings(SITE_URL="https://www.wlife.online", JAMIITEK_EMAIL="habari@example.tz")
class TrustPagesTest(TestCase):
    """E-E-A-T: mhariri halisi kwenye habari anazochapisha, sera ya uhariri, masahihisho na data ya Google."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Author
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("mhariri", password="x", is_staff=True)
        self.author = Author.objects.create(user=self.user, name="Asha Mohamed", slug="asha-mohamed",
                                            role="Mhariri wa Michezo", bio="Miaka 6 ya uandishi wa michezo.",
                                            links="https://www.linkedin.com/in/asha\nsi-link")

    def test_publishing_signs_article_and_shows_author(self):
        a = Article.objects.create(title="Simba yashinda", category="tanzania", excerpt="x", body="Aya moja.")
        self.client.force_login(self.user)
        with mock.patch("news.indexnow.submit"):
            self.client.post("/habari-admin/rasimu/action/", {"action": "publish_one", "article": a.pk})
        a.refresh_from_db()
        self.assertEqual((a.status, a.author), ("published", self.author))
        self.client.logout()
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn('<a href="/mwandishi/asha-mohamed/" rel="author">Asha Mohamed</a>, Mhariri wa Michezo', page)
        self.assertIn('"@type": "Person", "name": "Asha Mohamed"', page)
        profile = self.client.get("/mwandishi/asha-mohamed/").content.decode()
        for text in ("Asha Mohamed", "Miaka 6 ya uandishi", '"@type": "ProfilePage"', "Simba yashinda",
                     "https://www.linkedin.com/in/asha"):
            self.assertIn(text, profile)
        self.assertNotIn("si-link", profile)
        self.assertIn("/mwandishi/asha-mohamed/", self.client.get("/kuhusu/").content.decode())

    def test_unsigned_article_keeps_news_desk_and_correction_note(self):
        a = Article.objects.create(title="Bajeti mpya", category="tanzania", excerpt="x", body="Aya.",
                                   status="published", published_at=timezone.now(),
                                   correction="Tarehe ya kikao ilisahihishwa kuwa 12 Oktoba.")
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn("<strong>Dawati la Habari, wILife</strong>", page)
        self.assertIn("<b>Sahihisho:</b> Tarehe ya kikao ilisahihishwa", page)
        fixes = self.client.get("/masahihisho/").content.decode()
        self.assertIn("Bajeti mpya", fixes)
        self.assertIn("mailto:habari@example.tz", fixes)

    def test_policy_pages_and_organization_data(self):
        policy = self.client.get("/sera-ya-uhariri/").content.decode()
        for text in ("Hatubuni nukuu", "huhakikiwa na mhariri kabla ya kuchapishwa", "hatupakui wala kupakia upya",
                     'href="/masahihisho/"'):
            self.assertIn(text, policy)
        home = self.client.get("/").content.decode()
        self.assertIn('"correctionsPolicy": "https://www.wlife.online/masahihisho/"', home)
        self.assertIn('"publishingPrinciples": "https://www.wlife.online/sera-ya-uhariri/"', home)
        sm = self.client.get("/sitemap.xml").content.decode()
        for path in ("/sera-ya-uhariri/", "/masahihisho/", "/mwandishi/asha-mohamed/"):
            self.assertIn("https://www.wlife.online" + path, sm)


@override_settings(SITE_URL="https://www.wlife.online")
class PageCacheTest(TestCase):
    """Nakala moja ya ukurasa kwa wageni wote (si moja kwa kila cookie), tofauti kwa giza, na hakuna kwa mhariri."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_one_copy_for_all_cookies_but_not_for_editors(self):
        with mock.patch("news.views.render", wraps=render_shortcut) as r:
            self.client.cookies["_ga"] = "GA1.1.1"
            first = self.client.get("/kuhusu/")
            self.client.cookies["_ga"] = "GA1.1.2"                      # mgeni mwingine, cookie nyingine
            again = self.client.get("/kuhusu/")
            self.assertEqual(r.call_count, 1)
            self.assertEqual(first.content, again.content)
            self.assertIn("max-age=", again["Cache-Control"])
            self.client.cookies["theme"] = "dark"
            dark = self.client.get("/kuhusu/")
            self.assertEqual(r.call_count, 2)
            self.assertIn('data-bs-theme="dark"', dark.content.decode())
            self.client.force_login(User.objects.create_user("mhariri2", password="x", is_staff=True))
            self.client.get("/kuhusu/")
            self.assertEqual(r.call_count, 3)


@override_settings(SITE_URL="https://www.wlife.online", PAGE_CACHE_BACKGROUND=False, NEWS_CACHE_SECONDS=300)
class StaleWhileRevalidateTest(TestCase):
    """Nakala iliyopitwa na muda inatolewa papo hapo; mpya inatengenezwa nyuma, mara moja tu."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_stale_copy_served_then_refreshed_once(self):
        start = timezone.now()
        with mock.patch("news.views.render", wraps=render_shortcut) as r:
            with mock.patch("news.views.timezone.now", return_value=start):
                self.client.get("/kuhusu/")
            self.assertEqual(r.call_count, 1)
            with mock.patch("news.views.timezone.now", return_value=start + timedelta(seconds=200)):
                self.client.get("/kuhusu/")                       # bado mpya: hakuna kutengeneza
            self.assertEqual(r.call_count, 1)
            with mock.patch("news.views.timezone.now", return_value=start + timedelta(seconds=400)):
                stale = self.client.get("/kuhusu/")               # imepitwa: inatolewa, na mpya inatengenezwa
                self.assertEqual(stale.status_code, 200)
                self.assertEqual(r.call_count, 2)
                self.client.get("/kuhusu/")                       # mpya tayari: hakuna kutengeneza tena
            self.assertEqual(r.call_count, 2)


    def test_background_refresh_closes_its_database_connection(self):
        """Thread ya nyuma isiache muunganisho wazi — vinginevyo database inajaa na tovuti nzima inaanguka."""
        from django.http import HttpResponse
        from django.test import RequestFactory
        from news import views
        with mock.patch("threading.Thread") as thread, mock.patch("django.db.connections.close_all") as close:
            with self.settings(PAGE_CACHE_BACKGROUND=True):
                views._refresh_later(lambda request: HttpResponse("x"), RequestFactory().get("/kuhusu/"), (), {}, "page:k", 300)
            run = thread.call_args.kwargs["target"]
            run()
        close.assert_called_once()

    def test_broken_cache_does_not_break_the_page(self):
        """Cache (diski) ikishindwa, mgeni bado anapata ukurasa — si Server Error (500)."""
        from django.core.cache.backends.filebased import FileBasedCache
        full = OSError(28, "No space left on device")
        with mock.patch.object(FileBasedCache, "set", side_effect=full), \
                mock.patch.object(FileBasedCache, "add", side_effect=full):
            self.assertEqual(self.client.get("/kuhusu/").status_code, 200)
            self.assertEqual(self.client.get("/habari/").status_code, 200)
        with mock.patch.object(FileBasedCache, "get", side_effect=full):
            self.assertEqual(self.client.get("/kuhusu/").status_code, 200)

class SpeedDetailsTest(TestCase):
    """Picha ndogo za kadi za video, na Server-Timing ya kuona kinachochelewesha ukurasa."""

    def test_card_thumb_uses_small_youtube_image(self):
        from news.models import Highlight
        self.assertEqual(Highlight(thumbnail="https://i.ytimg.com/vi/abc/maxresdefault.jpg").card_thumb,
                         "https://i.ytimg.com/vi/abc/hqdefault.jpg")
        self.assertEqual(Highlight(thumbnail="https://cdn.example/x.jpg").card_thumb, "https://cdn.example/x.jpg")

    def test_server_timing_on_render_and_hit(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        first = self.client.get("/kuhusu/")
        self.assertRegex(first["Server-Timing"], r'^db;dur=\d+;desc="\d+ queries", render;dur=\d+$')
        self.assertEqual(self.client.get("/kuhusu/")["Server-Timing"], 'cache;desc="hit"')


def render_shortcut(*args, **kwargs):
    from django.shortcuts import render as _render
    return _render(*args, **kwargs)


@override_settings(SITE_URL="https://www.wlife.online")
class InternalLinksTest(TestCase):
    """Viungo vya ndani: habari -> timu/ligi, timu -> msimamo wa ligi zake."""

    def setUp(self):
        from django.core.cache import cache
        from news.models import Fixture, League
        cache.clear()
        self.addCleanup(cache.clear)
        Fixture.objects.create(fixture_id=6601, day=timezone.localdate(), kickoff=timezone.now(), status="NS",
                               home="Simba SC", away="KMC", competition="Premier League", competition_id=77,
                               country="Tanzania", rank=0)
        League.objects.create(competition_id=77, competition="Premier League", country="Tanzania",
                              name="Ligi Kuu Tanzania", slug="ligi-kuu-tanzania")

    def _article(self, title, keywords):
        return Article.objects.create(title=title, category="tanzania", excerpt="x", body="Aya.", keywords=keywords,
                                      status="published", published_at=timezone.now())

    def test_football_article_links_team_but_wildlife_story_does_not(self):
        sport = self._article("Kocha wa Simba azungumzia usajili", "Simba, usajili, Ligi Kuu")
        lion = self._article("Simba wa Serengeti waonekana tena", "Simba, Serengeti, utalii")
        self.assertIn('href="/timu/simba/"', self.client.get(sport.get_absolute_url()).content.decode())
        self.assertNotIn('href="/timu/simba/"', self.client.get(lion.get_absolute_url()).content.decode())

    def test_match_article_links_teams_and_league(self):
        from news.models import LiveMatch
        a = self._article("Simba SC dhidi ya KMC LIVE", "")
        LiveMatch.objects.create(article=a, fixture_id=6601, provider="highlightly", home="Simba SC", away="KMC",
                                 competition="Premier League", status="NS", kickoff=timezone.now())
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn('href="/timu/simba/"', page)
        self.assertIn('href="/ligi/ligi-kuu-tanzania/"', page)

    def test_team_page_links_its_leagues(self):
        page = self.client.get("/timu/simba/").content.decode()
        self.assertIn('href="/ligi/ligi-kuu-tanzania/"', page)


class LiveHubLogicTest(TestCase):
    """/live/: matokeo kwa siku, LIVE isiyo ya mechi ina sehemu yake, na majina ya ligi yasiyochanganya."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch("news.indexnow.submit")
        p.start()
        self.addCleanup(p.stop)

    def _match(self, home, away, kickoff, status="FT", hg=1, ag=0, **kw):
        from news.models import LiveMatch
        a = Article.objects.create(title=f"{home} vs {away}", category="dunia", excerpt="x", body="x",
                                   status="published", published_at=timezone.now(), origin="football",
                                   live_started_at=kickoff, live_ended_at=kickoff + timedelta(hours=2))
        LiveMatch.objects.create(article=a, home=home, away=away, kickoff=kickoff, status=status,
                                 home_goals=hg, away_goals=ag, **kw)
        return a

    def test_results_grouped_by_day_and_news_kept_apart(self):
        now = timezone.now()
        self._match("Kitayosce", "Geita Gold", now - timedelta(days=3))
        self._match("Malole", "Simba", now - timedelta(hours=3), competition="Ligue 1", extra={"country": "Congo DR"})
        self._match("Old", "Stale", now - timedelta(hours=5), status="NS", hg=None, ag=None)   # LIVE ilifungwa bila matokeo
        bunge = Article.objects.create(title="LIVE: Bunge la bajeti", category="tanzania", excerpt="x", body="x",
                                       status="published", published_at=now, live_started_at=now - timedelta(hours=4),
                                       live_ended_at=now - timedelta(hours=1))
        r = self.client.get("/live/")
        days = r.context["result_days"]
        self.assertEqual([len(rows) for _, rows in days], [1, 1])                  # siku mbili tofauti, mpya kwanza
        self.assertEqual(days[0][1][0].match.home, "Malole")
        self.assertNotIn("Old", [a.match.home for _, rows in days for a in rows])
        self.assertEqual(r.context["done_other"], [bunge])
        html = r.content.decode()
        self.assertIn("Ligue 1 · Congo DR", html)
        self.assertIn('class="sc-day"', html)

    def test_other_matches_on_an_article_are_from_today_only(self):
        now = timezone.now()
        old = self._match("PSG", "Lyon", now - timedelta(days=2))
        today = self._match("Arsenal", "Chelsea", now - timedelta(minutes=10), status="2H", hg=2, ag=1)
        Article.objects.filter(pk=today.pk).update(live_ended_at=None)
        page = self._match("Simba", "Yanga", now - timedelta(minutes=5), status="1H", hg=0, ag=0)
        Article.objects.filter(pk=page.pk).update(live_ended_at=None)
        from news.views import _other_matches
        page.refresh_from_db()
        self.assertEqual([a.pk for a in _other_matches(page)], [today.pk])
        self.assertNotIn(old.pk, [a.pk for a in _other_matches(page)])

    def test_card_helpers(self):
        from news.models import LiveMatch
        from news.templatetags.news_tags import team_initials, team_hue
        self.assertEqual(team_initials("Young Africans"), "YA")
        self.assertEqual(team_initials("Simba SC"), "S")
        self.assertEqual(team_hue("Simba"), team_hue("simba"))
        m = LiveMatch(home="A", away="B", competition="Ligue 1", extra={"country": "France"}, status="FT",
                      home_goals=0, away_goals=2)
        self.assertEqual((m.comp_label, m.result), ("Ligue 1", "away"))
        m.status, m.kickoff = "1H", timezone.now() + timedelta(hours=3)        # API: "inaendelea" kabla ya muda
        self.assertFalse(m.shows_score)

    def test_congo_league_is_africa(self):
        from news import football
        from news.models import Category
        self.assertEqual(football._category({"country": "Congo DR", "name": "Ligue 1"}), Category.AFRIKA)
        self.assertEqual(football._category({"country": "DR Congo", "name": "Linafoot"}), Category.AFRIKA)
        self.assertEqual(football._category({"country": "France", "name": "Ligue 1"}), Category.DUNIA)


class CrestFallbackTest(TestCase):
    """Nembo za klabu: API -> Admin (Teams) -> iliyoonekana kwenye mechi nyingine, bila kuchanganya nchi."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch("news.indexnow.submit")
        p.start()
        self.addCleanup(p.stop)

    def test_admin_logo_respects_country(self):
        from news import crests
        from news.models import Team
        Team.objects.create(name="Simba SC", slug="simba-sc-x", match_names="Simba, Simba SC",
                            logo="https://cdn.example/simba-tz.png", country="Tanzania", active=False)
        self.assertEqual(crests.logo_for("Simba", "", "Tanzania"), "https://cdn.example/simba-tz.png")
        self.assertEqual(crests.logo_for("Simba SC", "", "World"), "https://cdn.example/simba-tz.png")   # CAF
        self.assertEqual(crests.logo_for("Simba", "", "Congo DR"), "")                                  # Simba ya DRC
        self.assertEqual(crests.logo_for("Simba", "https://api.example/s.png", "Tanzania"), "https://api.example/s.png")
        from news.models import LiveMatch
        old_drc = LiveMatch(home="Malole", away="Simba", competition="Ligue 1")                        # kabla ya nchi kuhifadhiwa
        self.assertEqual(old_drc.away_crest, "")
        caf = LiveMatch(home="Al Ahly", away="Simba SC", competition="CAF Champions League")
        self.assertEqual(caf.away_crest, "https://cdn.example/simba-tz.png")

    def test_logo_seen_on_another_match_of_the_same_club(self):
        from news import crests
        from news.models import Fixture
        Fixture.objects.create(provider="highlightly", fixture_id=7001, day=timezone.localdate(), home="Geita Gold",
                               away="KMC", home_logo="https://hl.example/geita.png", kickoff=timezone.now(),
                               country="Tanzania", status="FT")
        self.assertEqual(crests.logo_for("Geita Gold FC", "", "Tanzania"), "https://hl.example/geita.png")
        self.assertEqual(crests.logo_for("Geita Gold", "", "Kenya"), "")

    def test_card_uses_admin_logo_and_cache_refreshes_on_save(self):
        from news.models import LiveMatch, Team
        a = Article.objects.create(title="Kitayosce vs Pamba", category="tanzania", excerpt="x", body="x",
                                   status="published", published_at=timezone.now(), origin="football",
                                   live_started_at=timezone.now())
        m = LiveMatch.objects.create(article=a, home="Kitayosce", away="Pamba Jiji", kickoff=timezone.now(),
                                     status="1H", minute=10, home_goals=0, away_goals=0, extra={"country": "Tanzania"})
        self.assertEqual(m.home_crest, "")
        Team.objects.create(name="Kitayosce", slug="kitayosce", match_names="Kitayosce",
                            logo="https://cdn.example/kitayosce.png", active=False)
        self.assertEqual(LiveMatch.objects.get(pk=m.pk).home_crest, "https://cdn.example/kitayosce.png")
        self.assertIn('src="https://cdn.example/kitayosce.png"', self.client.get("/live/").content.decode())
        self.assertEqual(self.client.get("/timu/kitayosce/").status_code, 404)        # nembo tu, hakuna ukurasa


@override_settings(HIGHLIGHTLY_API_KEY="hl-key", HIGHLIGHTLY_LEAGUES=["England:Premier League"], HIGHLIGHTLY_DAILY_LIMIT=100)
class HeroAndLiveStateTest(TestCase):
    """Mechi zilizokwisha hazibaki "LIVE", ratiba inajaribiwa tena maombi yakianza upya, na hero za michezo."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch("news.indexnow.submit")
        p.start()
        self.addCleanup(p.stop)

    def _finished_but_open(self):
        from news.models import LiveMatch
        a = Article.objects.create(title="Liverpool vs Brighton", category="dunia", excerpt="x", body="x",
                                   status="published", published_at=timezone.now(), origin="football",
                                   live_started_at=timezone.now() - timedelta(days=5))
        LiveMatch.objects.create(article=a, home="Liverpool", away="Brighton", status="FT", home_goals=2, away_goals=0,
                                 kickoff=timezone.now() - timedelta(days=5))
        return a

    def test_finished_match_is_not_live_and_gets_closed(self):
        from news import football
        from news.context_processors import _live_now
        a = self._finished_but_open()
        r = self.client.get("/live/")
        self.assertEqual(r.context["live_count"], 0)
        self.assertEqual(_live_now(), 0)
        self.assertEqual(football.close_finished(timezone.now()), 1)
        a.refresh_from_db()
        self.assertIsNotNone(a.live_ended_at)

    def test_schedule_retries_again_after_utc_quota_reset(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from news import football
        eat = ZoneInfo("Africa/Dar_es_Salaam")
        midnight = datetime(2026, 10, 30, 0, 0, tzinfo=eat)
        state = football._state(midnight)                     # siku ya UTC 29 — maombi yamekwisha
        state.schedule_at, state.requests = midnight, 100
        state.save()
        self.assertFalse(football.schedule_due(state, midnight + timedelta(minutes=40), timedelta(hours=24)))
        later = datetime(2026, 10, 30, 3, 10, tzinfo=eat)     # saa 03:10 EAT: siku mpya ya UTC, maombi mapya
        fresh = football._state(later)
        self.assertEqual(fresh.requests, 0)
        self.assertTrue(football.schedule_due(fresh, later, timedelta(hours=24)))

    def test_hero_shows_stadium_art_spotlight_and_admin_photos(self):
        from news.models import Fixture, HeroPhoto
        Fixture.objects.create(provider="highlightly", fixture_id=8801, day=timezone.localdate(), home="Simba SC",
                               away="Young Africans", kickoff=timezone.now() + timedelta(hours=2), status="NS",
                               competition="Ligi Kuu Bara", rank=0)
        html = self.client.get("/mechi-za-leo/").content.decode()
        self.assertIn('class="ha"', html)
        self.assertIn('class="spot"', html)
        self.assertIn("Simba SC", html.split('class="spot"')[1][:3000])
        self.assertNotIn("ha-photos", html)
        HeroPhoto.objects.create(image="https://cdn.example/uwanja.jpg", credit="Picha: wILife")
        html = self.client.get("/utabiri/").content.decode()
        self.assertIn('src="https://cdn.example/uwanja.jpg"', html)
        self.assertIn("Picha: wILife", html)



class HomeAndArticleDesignTest(TestCase):
    """Ukurasa wa mwanzo: ukanda wa LIVE na jalada la habari kuu; habari: kibandiko cha aina na nukuu."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch("news.indexnow.submit")
        p.start()
        self.addCleanup(p.stop)

    def test_quote_and_category_chip(self):
        from news.templatetags.news_tags import render_body
        html = str(render_body("Utangulizi.\n\n> Tutaajiri vijana 200,000 <b>mwaka huu</b>."))
        self.assertIn("<blockquote><p>Tutaajiri vijana 200,000 &lt;b&gt;mwaka huu&lt;/b&gt;.</p></blockquote>", html)
        a = Article.objects.create(title="Mpango wa ajira", category="tanzania", excerpt="x", body="x",
                                   status="published", published_at=timezone.now())
        page = self.client.get(a.get_absolute_url()).content.decode()
        self.assertIn('class="art-cat" href="/habari/tanzania/"', page)

    def test_home_live_band_and_photo_lead(self):
        from news.models import LiveMatch
        story = Article.objects.create(title="Habari kuu yenye picha", category="tanzania", excerpt="x", body="x",
                                       status="published", published_at=timezone.now())
        m = Article.objects.create(title="Simba vs Yanga LIVE", category="tanzania", excerpt="x", body="x",
                                   status="published", published_at=timezone.now() - timedelta(hours=1),
                                   live_started_at=timezone.now(), origin="football")
        LiveMatch.objects.create(article=m, home="Simba", away="Yanga", status="1H", minute=12, home_goals=0,
                                 away_goals=0, kickoff=timezone.now() - timedelta(minutes=12))
        html = self.client.get("/").content.decode()
        self.assertIn('class="ls ls-band"', html)
        self.assertIn('class="ha"', html)
        lead = html.split('<article class="lead ')[1][:200]
        self.assertIn("lead-ovl", lead)                       # habari kuu isiyo ya mechi: jalada (kichwa juu ya picha)
        self.assertIn(story.get_absolute_url(), lead)


def _season(n_rounds=4, start=None, comp=39, country="England", seed=7):
    """Ligi ya kubuni yenye nguvu zinazojulikana: Lions ni bora, Snails ni dhaifu (MatchResult)."""
    import math
    import random
    from news.models import MatchResult
    rnd = random.Random(seed)
    power = {"Lions": 1.6, "Tigers": 1.3, "Bears": 1.1, "Wolves": 1.0, "Foxes": 0.95, "Owls": 0.85, "Crows": 0.75,
             "Snails": 0.5}
    teams = list(power)
    start = start or timezone.now() - timedelta(days=420)

    def goals(lam):
        k, p, limit = 0, 1.0, math.exp(-lam)
        while True:
            p *= rnd.random()
            if p < limit:
                return k
            k += 1
    rows, when, fid = [], start, comp * 100000
    for _ in range(n_rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                when += timedelta(hours=34)
                fid += 1
                rows.append(MatchResult(fixture_id=fid, competition_id=comp, competition="Premier League",
                                        country=country, played_at=when, home=h, away=a,
                                        home_goals=goals(1.35 * 1.15 * power[h] / power[a] ** 0.5),
                                        away_goals=goals(1.35 * power[a] / power[h] ** 0.5)))
    MatchResult.objects.bulk_create(rows)
    return rows


class PredictionEngineTest(TestCase):
    """Hifadhi ya matokeo, Dixon–Coles + Elo, utabiri wa ratiba nzima, upimaji na backtest."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_collect_stores_finished_fixtures_and_live_matches_once(self):
        from news import results
        from news.models import Fixture, LiveMatch, MatchResult
        now = timezone.now()
        Fixture.objects.create(fixture_id=1, day=now.date(), kickoff=now - timedelta(hours=3), competition="Ligi Kuu",
                               competition_id=5, country="Tanzania", home="Simba SC", away="Azam FC", home_goals=2,
                               away_goals=1, status="FT", extra={"season": 2026})
        Fixture.objects.create(fixture_id=2, day=now.date(), kickoff=now + timedelta(hours=3), home="A", away="B")
        a = Article.objects.create(title="x", category="dunia", excerpt="x", body="b", status="published",
                                   published_at=now, origin="football")
        with mock.patch("news.models.Article.render_cover"):
            LiveMatch.objects.create(article=a, fixture_id=1, provider="api-football", home="Simba SC", away="Azam FC",
                                     kickoff=now - timedelta(hours=3), status="FT", home_goals=2, away_goals=1)
        self.assertEqual(results.collect(days=3), 1)
        self.assertEqual(results.collect(days=3), 0)
        r = MatchResult.objects.get()
        self.assertEqual((r.home, r.home_goals, r.away_goals, r.competition_id, r.season), ("Simba SC", 2, 1, 5, 2026))

    def test_ratings_find_the_strong_and_weak_teams(self):
        from news import ratings
        _season()
        model = ratings.build()
        lg = model.leagues[39]
        self.assertGreater(lg.attack["lions"], lg.attack["snails"])
        self.assertLess(lg.defence["lions"], lg.defence["snails"])           # chini = ulinzi bora
        self.assertGreater(lg.home, 1.0)
        self.assertGreater(model.elo["england|lions"], model.elo["england|snails"])

    def test_fixture_prediction_has_markets_confidence_and_reasons(self):
        from news import predict
        from news.models import Fixture
        _season()
        now = timezone.now()
        f = Fixture.objects.create(fixture_id=900, day=now.date(), kickoff=now + timedelta(hours=6), competition_id=39,
                                   competition="Premier League", country="England", home="Lions", away="Snails")
        made, settled = predict.refresh_fixtures(now)
        f.refresh_from_db()
        p = f.extra["prediction"]
        self.assertEqual((made, settled), (1, 0))
        self.assertEqual(p["home"] + p["draw"] + p["away"], 100)
        self.assertEqual(p["pick"], "1")
        self.assertGreater(p["markets"]["1x"], p["home"])
        self.assertGreaterEqual(p["markets"]["over15"], p["markets"]["over25"])
        self.assertGreaterEqual(p["markets"]["over25"], p["markets"]["over35"])
        self.assertEqual(len(p["scores"]), 3)
        self.assertIn(p["confidence"], ("juu", "wastani", "chini"))
        self.assertEqual(p["models"], ["goals", "elo"])
        self.assertTrue(any("Elo" in x for x in p["factors"]))
        self.assertEqual(predict.refresh_fixtures(now), (0, 0))       # hakuna matokeo mapya: haihesabiwi tena
        f.status, f.home_goals, f.away_goals = "FT", 3, 0
        f.save()
        self.assertEqual(predict.refresh_fixtures(now + timedelta(days=1)), (0, 1))
        f.refresh_from_db()
        self.assertTrue(f.extra["prediction_hit"])
        self.assertIn("over25", f.extra["prediction_markets"])
        rec = predict.record()
        self.assertEqual((rec["total"], rec["hits"]), (1, 1))
        self.assertEqual(rec["tiers"][p["confidence"]]["total"], 1)

    def test_unknown_teams_and_elo_only_matches(self):
        from news import predict
        from news.models import Fixture
        _season()
        now = timezone.now()
        f = Fixture(fixture_id=901, day=now.date(), kickoff=now + timedelta(hours=6), competition_id=777,
                    country="World", home="Lions", away="Owls")
        p = predict.compute(f)
        self.assertEqual(p["models"], ["elo"])                          # kombe: hakuna Dixon–Coles ya mashindano haya
        self.assertEqual(p["pick"], "1")
        f.home = "Nobody FC"
        self.assertIsNone(predict.compute(f))

    def test_backtest_reports_accuracy_calibration_and_weights(self):
        from news import backtest, predict
        _season(n_rounds=6)
        data = backtest.run(days=120)
        self.assertGreater(data["tested"], 50)
        ens = data["models"]["ensemble"]
        self.assertLess(ens["brier"], 0.667)                            # bora kuliko kubahatisha 33/33/33
        self.assertTrue(data["calibration"])
        self.assertAlmostEqual(sum(data["weights"].values()), 1.0, places=2)
        self.assertEqual(predict.weights(), data["weights"])
        self.assertFalse(backtest.due(timezone.now()))
        page = self.client.get("/utabiri/").content.decode()
        self.assertIn("Majaribio ya mechi", page)
        self.assertIn("Tukisema 60%", page)

    def test_backtest_waits_for_enough_data(self):
        from news import backtest
        _season(n_rounds=1)
        self.assertIn("note", backtest.run())

    def test_backfill_reads_league_seasons_with_spare_quota_only(self):
        from news import results
        from news.models import FootballState, League, MatchResult, ModelReport
        League.objects.create(competition_id=39, competition="Premier League", country="England", name="EPL",
                              slug="epl", season=2026, rank=1)
        state = FootballState.objects.create(day=timezone.now().date())
        when = (timezone.now() - timedelta(days=20)).isoformat()
        page = {"data": [{"id": 5000 + i, "date": when, "state": {"description": "Finished", "score": {"current": "2 - 1"}},
                          "league": {"id": 39, "name": "Premier League", "season": 2026},
                          "homeTeam": {"id": 1, "name": f"H{i}"}, "awayTeam": {"id": 2, "name": f"A{i}"}}
                         for i in range(3)], "pagination": {"totalCount": 3}}
        calls = []

        def fake(path, params, st):
            calls.append(params)
            if params["season"] == 2025:
                raise RuntimeError("Highlightly 400: season")
            return page
        with mock.patch("news.football.provider", return_value="highlightly"), \
                mock.patch("news.highlightly._get", side_effect=fake):
            self.assertEqual(results.backfill(state, timezone.now(), 10), 0)        # maombi machache: subiri
            self.assertEqual(results.backfill(state, timezone.now(), 80), 2)
            self.assertEqual(results.backfill(state, timezone.now(), 80), 0)        # kazi zote zimeisha
        self.assertEqual([c["season"] for c in calls], [2026, 2025])
        self.assertEqual(MatchResult.objects.count(), 3)
        prog = ModelReport.objects.get(key=results.PROGRESS_KEY).data
        self.assertTrue(prog["39:2026"]["done"] and prog["39:2025"]["error"])


def _odds_item(mid, home=2.0, draw=3.4, away=4.0, over=1.9, under=1.95, books=("Bet365", "Pinnacle")):
    return {"matchId": mid, "odds": [
        {"bookmakerName": b, "type": "prematch", "market": "Full Time Result",
         "values": [{"value": "Home", "odd": home + i * 0.1}, {"value": "Draw", "odd": draw},
                    {"value": "Away", "odd": away}]} for i, b in enumerate(books)] + [
        {"bookmakerName": "Bet365", "market": "Over/Under", "values": [
            {"value": "Over 2.5", "odd": over}, {"value": "Under 2.5", "odd": under},
            {"value": "Over 1.5", "odd": 1.3}, {"value": "Under 1.5", "odd": 3.2}]},
        {"bookmakerName": "Bet365", "market": "1st Half Winner", "values": [
            {"value": "Home", "odd": 9.0}, {"value": "Draw", "odd": 9.0}, {"value": "Away", "odd": 9.0}]}]}


class OddsTest(TestCase):
    """Odds za kampuni: kusoma kwa uvumilivu, soko dhidi ya utabiri, thamani na rekodi ya wazi."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_parse_averages_bookmakers_and_removes_margin(self):
        from news import odds
        o = odds.parse([_odds_item(10)])[10]
        self.assertEqual(o["1x2"]["books"], 2)
        self.assertEqual(o["1x2"]["avg"], [2.05, 3.4, 4.0])
        self.assertEqual(o["1x2"]["best"], [2.1, 3.4, 4.0])         # nusu ya kwanza haichanganywi
        self.assertAlmostEqual(sum(o["fair"]["1x2"]), 1.0, places=3)
        self.assertGreater(o["margin"]["1x2"], 0)
        self.assertEqual(o["ou25"]["avg"], [1.9, 1.95])               # mstari wa 2.5 tu
        self.assertNotIn("btts", o)
        self.assertEqual(odds.parse([{"matchId": 1, "odds": [{"market": "Full Time Result", "values": []}]},
                                     {"foo": "bar"}, "x"]), {})

    def test_value_needs_edge_and_confidence(self):
        from news import odds
        o = odds.parse([_odds_item(10, home=2.6, draw=3.4, away=2.8)])[10]
        p = {"home": 55, "draw": 25, "away": 20, "confidence": "wastani", "markets": {"over25": 50, "btts": 50}}
        rows, value = odds.compare(p, o)
        self.assertEqual((value["market"], value["i"]), ("1x2", 0))
        self.assertGreater(value["ev"], 40)
        self.assertEqual(len(rows), 5)
        self.assertIsNone(odds.compare({**p, "confidence": "chini"}, o)[1])
        self.assertIsNone(odds.compare({**p, "home": 37, "draw": 28, "away": 35}, o)[1])      # sawa na soko: hakuna thamani

    def test_fetch_stores_prematch_odds_and_backs_off_on_error(self):
        from news import odds
        from news.models import Fixture, FootballState, ModelReport
        now = timezone.now()
        f = Fixture.objects.create(fixture_id=10, day=timezone.localdate(now), kickoff=now + timedelta(hours=4),
                                   home="Lions", away="Snails")
        state = FootballState.objects.create(day=now.date())
        calls = []

        def fake(path, params, st):
            calls.append((path, params["date"]))
            if len(calls) > 1:
                raise RuntimeError("Highlightly 403: plan")
            return {"data": [_odds_item(10)], "pagination": {"totalCount": 1}}
        with mock.patch("news.football.provider", return_value="highlightly"), \
                mock.patch("news.highlightly._get", side_effect=fake):
            self.assertEqual(odds.fetch(state, now, 10), 0)                 # maombi machache: subiri
            self.assertEqual(odds.fetch(state, now, 80), 2)                 # leo + kesho (kesho ilikosa)
            self.assertEqual(odds.fetch(state, now + timedelta(hours=1), 80), 0)   # bado mapema / kosa: subiri
        self.assertEqual(calls[0], ("/odds", timezone.localdate(now).isoformat()))
        f.refresh_from_db()
        self.assertEqual(f.extra["odds"]["1x2"]["books"], 2)
        self.assertIn("Full Time Result", ModelReport.objects.get(key=odds.SAMPLE_KEY).data["raw"])
        prog = ModelReport.objects.get(key=odds.PROGRESS_KEY).data
        self.assertTrue(any("error" in v for v in prog.values()))

    def test_value_bet_is_settled_and_shown_with_market_comparison(self):
        from news import odds, predict
        from news.models import Fixture
        _season()
        now = timezone.now()
        f = Fixture.objects.create(fixture_id=900, day=timezone.localdate(now), kickoff=now + timedelta(hours=6),
                                   competition_id=39, competition="Premier League", country="England",
                                   home="Lions", away="Snails", rank=1)
        predict.refresh_fixtures(now)
        f.refresh_from_db()
        f.extra = {**f.extra, "odds": odds.parse([_odds_item(900, home=2.5, draw=3.6, away=3.0)])[900]}
        f.save()
        page = self.client.get("/utabiri/").content.decode()
        for text in ("Odds za kampuni za kubeti", "Chaguo zenye thamani dhidi ya odds", "Thamani:"):
            self.assertIn(text, page)
        f.status, f.home_goals, f.away_goals = "FT", 2, 0
        f.save()
        predict.refresh_fixtures(now + timedelta(days=1))
        f.refresh_from_db()
        self.assertTrue(f.extra["value_result"]["won"])
        self.assertEqual(f.extra["value_result"]["profit"], 1.55)         # wastani wa 2.5 na 2.6
        rec = predict.record()
        self.assertEqual((rec["value"]["total"], rec["value"]["profit"]), (1, 1.55))
        self.assertEqual(rec["market"], {"total": 1, "pct": 100, "ours": 100})


class OddsDiagnosticsTest(TestCase):
    """Admin inaonyesha kwa nini odds hazijasomwa, na amri ya odds_check inajaribu ombi moja."""

    def test_skip_reason_is_recorded(self):
        from news import odds
        from news.models import FootballState, ModelReport
        state = FootballState.objects.create(day=timezone.now().date())
        with mock.patch("news.football.provider", return_value="highlightly"):
            self.assertEqual(odds.fetch(state, timezone.now(), 10), 0)
        hali = ModelReport.objects.get(key=odds.PROGRESS_KEY).data["_hali"]
        self.assertIn("yaliyobaki 10", hali["sababu"])
        odds.waiting(timezone.now())
        self.assertIn("LIVE", ModelReport.objects.get(key=odds.PROGRESS_KEY).data["_hali"]["sababu"])

    def test_odds_check_command_saves_sample_even_when_empty(self):
        from io import StringIO
        from django.core.management import call_command
        from news import odds
        from news.models import ModelReport
        out = StringIO()
        with mock.patch("news.highlightly._get", return_value={"data": [], "pagination": {"totalCount": 0}}):
            call_command("odds_check", "--date", "2026-10-30", stdout=out)
        self.assertIn("mechi 0 kwenye jibu", out.getvalue())
        sample = ModelReport.objects.get(key=odds.SAMPLE_KEY).data
        self.assertEqual((sample["rows"], sample["date"]), (0, "2026-10-30"))
        out = StringIO()
        with mock.patch("news.highlightly._get", return_value={"data": [_odds_item(10)]}):
            call_command("odds_check", stdout=out)
        self.assertIn("zilizoeleweka 1", out.getvalue())
        with mock.patch("news.highlightly._get", side_effect=RuntimeError("Highlightly 403: plan")):
            call_command("odds_check", stdout=out)
        self.assertIn("Kosa: Highlightly 403", out.getvalue())

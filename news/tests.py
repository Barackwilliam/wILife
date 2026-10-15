import json
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

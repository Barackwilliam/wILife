import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.agent import channels, whatsapp
from core.models import ApprovalRequest


def _response(status, payload):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


ALL_CHANNELS = dict(
    AGENT_ENABLED=True,
    AGENT_SELF_CHANNEL="all",
    EMAIL_CHANNEL_ENABLED=True,
    RESEND_API_KEY="re_test",
    AGENT_EMAIL_FROM="wILife <agent@example.com>",
    AGENT_EMAIL_TO="owner@example.com",
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="123:abc",
    AGENT_TELEGRAM_CHAT_ID="42",
    WHATSAPP_ENABLED=True,
    WHATSAPP_PROVIDER="baileys",
    WHATSAPP_BRIDGE_URL="http://bridge.test",
    WHATSAPP_BRIDGE_KEY="bridge-key",
    AGENT_DEFAULT_RECIPIENT="0712345678",
    AGENT_DEFAULT_COUNTRY_CODE="255",
)


def _fake_post(url, json=None, headers=None, timeout=None):
    if "resend" in url:
        return _response(200, {"id": "email-1"})
    if "telegram" in url:
        return _response(200, {"result": {"message_id": 7}})
    if url.startswith("http://bridge.test"):
        return _response(200, {"success": True, "id": "wa-1"})
    raise AssertionError(f"unexpected URL {url}")


@override_settings(**ALL_CHANNELS)
class SendToSelfAllChannelsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="x")

    def test_sends_on_every_configured_channel(self):
        with mock.patch("requests.post", side_effect=_fake_post) as post:
            result = channels.send_to_self(self.user, "*Kumbusho* test")

        self.assertEqual(result, {"email": "email-1", "telegram": 7, "whatsapp": "wa-1"})
        urls = [c.args[0] for c in post.call_args_list]
        self.assertEqual(len(urls), 3)

        bridge_call = next(c for c in post.call_args_list if c.args[0].startswith("http://bridge.test"))
        self.assertEqual(bridge_call.args[0], "http://bridge.test/send")
        self.assertEqual(bridge_call.kwargs["json"]["to"], "255712345678")
        self.assertTrue(bridge_call.kwargs["json"]["text"].startswith("*Kumbusho* test\n━"))
        self.assertEqual(bridge_call.kwargs["headers"]["X-Bridge-Key"], "bridge-key")

    def test_one_failing_channel_does_not_stop_the_others(self):
        def post(url, **kw):
            if "telegram" in url:
                return _response(401, {"description": "Unauthorized"})
            return _fake_post(url, **kw)

        with mock.patch("requests.post", side_effect=post):
            result = channels.send_to_self(self.user, "hello")

        self.assertEqual(set(result), {"email", "whatsapp"})

    def test_raises_when_every_channel_fails(self):
        with mock.patch("requests.post", return_value=_response(401, {})):
            with self.assertRaises(channels.DeliveryError):
                channels.send_to_self(self.user, "hello")

    @override_settings(TELEGRAM_ENABLED=False, WHATSAPP_BRIDGE_URL="")
    def test_skips_unconfigured_channels(self):
        self.assertEqual(channels.ready_channels(self.user), ["email"])
        with mock.patch("requests.post", side_effect=_fake_post):
            self.assertEqual(channels.send_to_self(self.user, "hi"), {"email": "email-1"})

    @override_settings(AGENT_SELF_CHANNEL="telegram")
    def test_single_channel_mode_still_uses_one_channel(self):
        with mock.patch("requests.post", side_effect=_fake_post) as post:
            self.assertEqual(channels.send_to_self(self.user, "hi"), 7)
        self.assertEqual(post.call_count, 1)


@override_settings(**ALL_CHANNELS)
class BaileysSendTests(TestCase):
    def test_retries_while_bridge_reconnects(self):
        responses = [_response(503, {"error": "not connected"}), _response(200, {"id": "wa-9"})]
        with mock.patch("requests.post", side_effect=responses), mock.patch("time.sleep"):
            self.assertEqual(whatsapp.send_whatsapp("0712345678", "hi"), "wa-9")

    def test_unknown_number_is_a_permanent_failure(self):
        with mock.patch("requests.post", return_value=_response(404, {"error": "not on WhatsApp"})) as post:
            with self.assertRaises(whatsapp.WhatsAppError):
                whatsapp.send_whatsapp("0712345678", "hi")
        self.assertEqual(post.call_count, 1)

    @override_settings(WHATSAPP_BRIDGE_KEY="")
    def test_not_ready_without_bridge_key(self):
        self.assertFalse(whatsapp.configured())


@override_settings(**ALL_CHANNELS)
class BaileysIncomingTests(TestCase):
    url = "/agent/whatsapp/baileys/"

    def setUp(self):
        self.user = User.objects.create_user("owner", password="x")
        self.approval = ApprovalRequest.objects.create(
            user=self.user, tool="draft_client_message", code="1234",
            recipient_name="Client", recipient_number="255700000001",
            body="Habari", expires_at=timezone.now() + timezone.timedelta(hours=1),
        )

    def post(self, data, key="bridge-key"):
        return self.client.post(self.url, json.dumps(data), content_type="application/json",
                                HTTP_X_BRIDGE_KEY=key)

    def test_rejects_wrong_key(self):
        self.assertEqual(self.post({"phone": "255712345678", "message": "OK 1234"}, key="nope").status_code, 403)

    def test_owner_can_approve_and_gets_reply(self):
        with mock.patch("requests.post", side_effect=_fake_post) as post:
            r = self.post({"phone": "255712345678", "message": "OK 1234"})

        self.assertEqual(r.status_code, 200)
        self.assertIn("Imetumwa", r.json()["reply"])
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, "sent")
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent, {"to": "255700000001", "text": "Habari"})

    def test_stranger_is_ignored(self):
        with mock.patch("requests.post") as post:
            r = self.post({"phone": "255799999999", "message": "OK 1234"})
        self.assertEqual(r.json()["reply"], "")
        post.assert_not_called()
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, "pending")

    def test_ordinary_chat_is_ignored(self):
        r = self.post({"phone": "255712345678", "message": "habari yako"})
        self.assertEqual(r.json()["reply"], "")


class ExportTests(TestCase):
    def setUp(self):
        from core.models import Income, Schedule
        self.user = User.objects.create_user("owner", password="x")
        self.client.force_login(self.user)
        today = timezone.localdate()
        Income.objects.create(user=self.user, amount=1000, source="Salary", date=today)
        start = timezone.make_aware(timezone.datetime(2026, 10, 2, 9, 30))
        Schedule.objects.create(user=self.user, title="Kikao", start_datetime=start,
                                end_datetime=start + timezone.timedelta(hours=1))

    def test_excel_export_with_schedules(self):
        import io
        import pandas as pd

        r = self.client.get("/export/excel/")
        self.assertEqual(r.status_code, 200)
        sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
        self.assertEqual(sheets["Incomes"]["source"].tolist(), ["Salary"])
        # Stored in UTC, written in local (Dar es Salaam) time.
        self.assertEqual(str(sheets["Schedule"]["start_datetime"][0]), "2026-10-02 09:30:00")

    def test_pdf_export(self):
        r = self.client.get("/export/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))


@override_settings(**ALL_CHANNELS)
class WhatsAppQrPageTests(TestCase):
    url = "/agent/whatsapp/qr/"

    def test_requires_staff(self):
        user = User.objects.create_user("plain", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_sees_bridge_page_without_key_in_html(self):
        admin = User.objects.create_user("admin", password="x", is_staff=True)
        self.client.force_login(admin)
        bridge = mock.Mock(status_code=200, content=b"<h2>Scan with WhatsApp</h2>",
                           headers={"Content-Type": "text/html"})
        with mock.patch("requests.get", return_value=bridge) as get:
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Scan with WhatsApp", r.content)
        self.assertNotIn(b"bridge-key", r.content)
        self.assertEqual(get.call_args.kwargs["params"], {"key": "bridge-key"})


@override_settings(**ALL_CHANNELS)
class ScheduleReminderJobTests(TestCase):
    def test_due_reminder_is_sent_and_marked(self):
        from core.agent.jobs import run_schedule_reminders
        from core.models import Profile, Schedule

        user = User.objects.create_user("owner", password="x")
        Profile.objects.get_or_create(user=user)
        now = timezone.now()
        schedule = Schedule.objects.create(
            user=user, title="Kikao", start_datetime=now + timezone.timedelta(minutes=30),
            end_datetime=now + timezone.timedelta(hours=1), reminder_datetime=now - timezone.timedelta(minutes=1),
        )
        with mock.patch("requests.post", side_effect=_fake_post):
            result = run_schedule_reminders(now=now)

        self.assertEqual((result["sent"], result["failed"]), (1, 0))
        schedule.refresh_from_db()
        self.assertTrue(schedule.reminder_sent)


class UiFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="x")
        self.client.force_login(self.user)

    def test_home_redirects_signed_in_users_to_dashboard(self):
        self.assertRedirects(self.client.get("/app/"), "/dashboard/")

    def test_task_toggle_flips_status(self):
        from core.models import Task
        task = Task.objects.create(user=self.user, title="T", date=timezone.localdate())
        self.client.post(f"/task/{task.pk}/toggle/")
        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        self.client.post(f"/task/{task.pk}/toggle/")
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")

    def test_task_toggle_rejects_get_and_other_users(self):
        from core.models import Task
        other = User.objects.create_user("other", password="x")
        task = Task.objects.create(user=other, title="T", date=timezone.localdate())
        self.assertEqual(self.client.get(f"/task/{task.pk}/toggle/").status_code, 405)
        self.assertEqual(self.client.post(f"/task/{task.pk}/toggle/").status_code, 404)

    def test_dashboard_prefs_reach_js_as_valid_json(self):
        from core.models import DashboardPreference
        DashboardPreference.objects.create(user=self.user, hidden_widgets='{"goals": true}')
        r = self.client.get("/dashboard/")
        self.assertContains(r, '<script id="hidden-widgets" type="application/json">{"goals": true}</script>', html=False)

    def test_cycle_calendar_requires_login_and_escapes_text(self):
        from core.models import MenstrualCycleRecord
        MenstrualCycleRecord.objects.create(user=self.user, start_date=timezone.localdate(),
                                            end_date=timezone.localdate(), symptoms="it's </script>")
        r = self.client.get("/period")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "it's </script>")
        self.client.logout()
        self.assertEqual(self.client.get("/period").status_code, 302)

    def test_calendar_includes_tasks_and_events(self):
        from core.models import Schedule, Task
        Task.objects.create(user=self.user, title="Pay rent", date=timezone.localdate(), priority="high")
        now = timezone.now()
        Schedule.objects.create(user=self.user, title="Dentist", start_datetime=now, end_datetime=now)
        r = self.client.get("/calendar/")
        self.assertContains(r, "Pay rent")
        self.assertContains(r, "task-high")
        self.assertContains(r, "Dentist")

    def test_profile_saves_whatsapp_number(self):
        self.client.post("/profile/", {"username": "owner", "email": "o@x.com", "whatsapp_number": "0712345678"})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.whatsapp_number, "0712345678")


class MessageFormatTests(TestCase):
    BRIEF = (
        "☀️ *Habari za asubuhi, William*\n_Jumamosi_\n\n📅 *Ratiba ya leo*\n  09:00  Kikao\n\n"
        "✅ *Vipaumbele*\n  🔴 Invoice\n  Kupiga simu\n\n💰 *Mwezi huu*\n  Mapato:   TZS 1,000\n"
    )
    APPROVAL = (
        "✍️ *Rasimu inasubiri idhini*\n\n*Kwenda kwa:* Asha\n────────────\nHabari <b>Asha</b>\n────────────\n\n"
        "Soma: https://example.com/r/\nJibu *OK 1234* kutuma\nJibu *NO 1234* kufuta\n"
    )

    def test_parse_reads_the_message_structure(self):
        from core.agent.message_format import parse
        emoji, title, blocks = parse(self.BRIEF)
        self.assertEqual((emoji, title), ("☀️", "Habari za asubuhi, William"))
        kinds = [k for k, _ in blocks if k != "space"]
        self.assertEqual(kinds, ["note", "section", "item", "section", "item", "item", "section", "kv"])
        self.assertIn(("kv", ("Mapato", "TZS 1,000")), blocks)

        _, _, blocks = parse(self.APPROVAL)
        self.assertIn(("label", {"label": "Kwenda kwa", "value": "Asha"}), blocks)
        self.assertIn(("quote", ["Habari <b>Asha</b>"]), blocks)
        self.assertIn(("link", ("Soma", "https://example.com/r/")), blocks)
        self.assertEqual(blocks[-1], ("replies", [("OK", "1234", "kutuma"), ("NO", "1234", "kufuta")]))

    def test_email_html_is_designed_and_escaped(self):
        from core.agent.email_channel import to_html
        out = to_html(self.APPROVAL)
        self.assertIn("Rasimu inasubiri idhini", out)
        self.assertIn('href="https://example.com/r/"', out)
        self.assertIn("OK 1234", out)
        self.assertIn("&lt;b&gt;Asha&lt;/b&gt;", out)
        self.assertNotIn("<b>Asha</b>", out)

    def test_chat_version_adds_rule_bullets_and_signature(self):
        from core.agent.message_format import CHAT_RULE, for_chat
        out = for_chat(self.BRIEF)
        lines = out.splitlines()
        self.assertEqual(lines[1], CHAT_RULE)
        self.assertIn("  • 09:00  Kikao", lines)
        self.assertIn("  🔴 Invoice", lines)
        self.assertIn("  • Kupiga simu", lines)
        self.assertTrue(out.endswith("_— wILife_"))
        numbered = for_chat("📰 *Rasimu*\n\n1. [Dunia] A\n2. [Afrika] B")
        self.assertIn("1. [Dunia] A", numbered)

    @override_settings(**{**ALL_CHANNELS, "EMAIL_CHANNEL_ENABLED": False, "TELEGRAM_ENABLED": False})
    def test_messages_to_william_use_the_chat_format_but_messages_to_clients_do_not(self):
        from core.agent import channels
        with mock.patch("requests.post", side_effect=_fake_post) as post:
            channels.send_to_self(None, "⏰ *Kumbusho*\n\nKikao")
            channels.send_to_other("255700000009", "Habari Asha")
        texts = [c.kwargs["json"]["text"] for c in post.call_args_list if c.args[0].endswith("/send")]
        self.assertTrue(texts[0].startswith("⏰ *Kumbusho*\n━"))
        self.assertEqual(texts[1], "Habari Asha")

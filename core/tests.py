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
        self.assertEqual(bridge_call.kwargs["json"], {"to": "255712345678", "text": "*Kumbusho* test"})
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

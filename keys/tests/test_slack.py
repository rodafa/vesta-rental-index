"""Tests for KeyBot Slack integration — signature verification, view routing, post_message."""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings

from keys.slack import post_message, strip_mention, verify_slack_signature
from keys.views import slack_events

SIGNING_SECRET = "test_signing_secret_abc"
CHANNEL_ID = "C_KEYBOT_TEST"


def _sign(body: bytes, secret: str = SIGNING_SECRET, ts: str | None = None) -> tuple[str, str]:
    """Return (timestamp, signature) for a Slack request body."""
    ts = ts or str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return ts, sig


def _make_request(payload: dict, ts: str | None = None, signature: str | None = None,
                  extra_headers: dict | None = None) -> "WSGIRequest":
    """Build a signed POST request for the slack_events view."""
    body = json.dumps(payload).encode()
    computed_ts, computed_sig = _sign(body, ts=ts)
    ts = ts or computed_ts
    sig = signature or computed_sig
    factory = RequestFactory()
    request = factory.post(
        "/slack/keybot/events/",
        data=body,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=ts,
        HTTP_X_SLACK_SIGNATURE=sig,
        **({"HTTP_" + k.upper().replace("-", "_"): v
            for k, v in (extra_headers or {}).items()}),
    )
    return request


@override_settings(
    SLACK_SIGNING_SECRET=SIGNING_SECRET,
    SLACK_BOT_TOKEN="xoxb-test-token",
    KEYBOT_CHANNEL_ID=CHANNEL_ID,
)
class SlackSignatureTests(TestCase):

    def test_valid_signature_passes(self):
        body = b'{"type": "url_verification", "challenge": "abc"}'
        ts, sig = _sign(body)
        self.assertTrue(verify_slack_signature(SIGNING_SECRET, ts, body, sig))

    def test_bad_signature_returns_401(self):
        request = _make_request(
            {"type": "url_verification", "challenge": "x"},
            signature="v0=bad",
        )
        resp = slack_events(request)
        self.assertEqual(resp.status_code, 401)

    def test_old_timestamp_returns_401(self):
        old_ts = str(int(time.time()) - 600)
        body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
        _, sig = _sign(body, ts=old_ts)
        request = _make_request(
            {"type": "url_verification", "challenge": "x"},
            ts=old_ts,
            signature=sig,
        )
        resp = slack_events(request)
        self.assertEqual(resp.status_code, 401)

    def test_empty_secret_returns_401(self):
        body = b'{"type": "url_verification", "challenge": "x"}'
        self.assertFalse(verify_slack_signature("", str(int(time.time())), body, "v0=anything"))


@override_settings(
    SLACK_SIGNING_SECRET=SIGNING_SECRET,
    SLACK_BOT_TOKEN="xoxb-test-token",
    KEYBOT_CHANNEL_ID=CHANNEL_ID,
)
class SlackEventsViewTests(TestCase):

    def test_url_verification_returns_challenge(self):
        request = _make_request({"type": "url_verification", "challenge": "test_challenge_123"})
        resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["challenge"], "test_challenge_123")

    def test_retry_header_returns_200_no_post(self):
        payload = {
            "type": "event_callback",
            "event": {"type": "app_mention", "channel": CHANNEL_ID, "text": "hi", "ts": "1.1"},
        }
        request = _make_request(payload, extra_headers={"X-Slack-Retry-Num": "1"})
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_not_called()

    def test_app_mention_keybot_channel_posts_reply(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": CHANNEL_ID,
                "text": "<@U123BOT> hello",
                "ts": "1234567890.123456",
            },
        }
        request = _make_request(payload)
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_called_once_with(
            CHANNEL_ID,
            "KeyBot is connected \u2705 \u2014 lookups coming soon.",
            thread_ts="1234567890.123456",
        )

    def test_app_mention_wrong_channel_no_post(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C_OTHER",
                "text": "hi",
                "ts": "1.1",
            },
        }
        request = _make_request(payload)
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_not_called()

    def test_bot_message_ignored(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": CHANNEL_ID,
                "text": "bot echo",
                "ts": "1.1",
                "bot_id": "B_OTHER",
            },
        }
        request = _make_request(payload)
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_not_called()


class PostMessageTests(TestCase):

    @patch("keys.slack.requests.post")
    def test_post_message_slack_error_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_post.return_value = mock_resp
        with self.assertRaises(RuntimeError) as ctx:
            post_message("C_BAD", "hello")
        self.assertIn("channel_not_found", str(ctx.exception))


class StripMentionTests(TestCase):

    def test_removes_mention(self):
        self.assertEqual(strip_mention("<@U123ABC> look up unit 4"), "look up unit 4")

    def test_no_mention_unchanged(self):
        self.assertEqual(strip_mention("just text"), "just text")

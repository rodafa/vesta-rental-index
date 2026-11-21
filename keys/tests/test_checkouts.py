"""Tests for key checkout/return service, reminders, and interactions."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Property, Unit
from keys.checkouts import (
    checkout_tag,
    due_reminders,
    in_reminder_window,
    open_checkouts,
    record_reminder_sent,
    return_tag,
    snooze_reminder,
)
from keys.models import Checkout, KeyChange, Tag
from keys.replies import (
    format_unit,
    lookup_blocks,
    reminder_blocks,
    take_buttons,
)
from keys.selectors import parse_query

ET = ZoneInfo("America/New_York")


class _CheckoutFixture(TestCase):
    """Property → Unit → Tag, status on_hook."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="10 Elm Street",
            address_line_1="10 Elm Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9000,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="10 Elm Street",
            unit_number="1",
            rentvine_id=9001,
        )
        cls.tag = Tag.objects.create(
            box="A", number=99, unit=cls.unit, ring_notes="front door",
        )


class CheckoutServiceTests(_CheckoutFixture):

    def test_checkout_success(self):
        result = checkout_tag(self.tag, "U_ALICE", "Alice")
        self.assertIsInstance(result, Checkout)
        self.assertEqual(result.checked_out_by_slack_id, "U_ALICE")
        self.assertEqual(result.checked_out_by_name, "Alice")
        self.assertIsNone(result.returned_at)

        # Tag status updated.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "checked_out")

        # KeyChange recorded.
        kc = KeyChange.objects.filter(tag=self.tag, reason="checkout").first()
        self.assertIsNotNone(kc)
        self.assertEqual(kc.old_value, "on_hook")
        self.assertEqual(kc.new_value, "checked_out")

    def test_checkout_already_out(self):
        checkout_tag(self.tag, "U_ALICE", "Alice")
        result = checkout_tag(self.tag, "U_BOB", "Bob")
        self.assertIsInstance(result, str)
        self.assertIn("already checked out", result)
        self.assertIn("Alice", result)

    def test_checkout_retired(self):
        self.tag.status = "retired"
        self.tag.save(update_fields=["status"])
        result = checkout_tag(self.tag, "U_ALICE", "Alice")
        self.assertIsInstance(result, str)
        self.assertIn("retired", result)

    def test_checkout_missing(self):
        self.tag.status = "missing"
        self.tag.save(update_fields=["status"])
        result = checkout_tag(self.tag, "U_ALICE", "Alice")
        self.assertIsInstance(result, str)
        self.assertIn("missing", result)

    def test_return_success(self):
        checkout_tag(self.tag, "U_ALICE", "Alice")
        closed = return_tag(self.tag, "U_ALICE")
        self.assertIsNotNone(closed)
        self.assertIsNotNone(closed.returned_at)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "on_hook")

        kc = KeyChange.objects.filter(tag=self.tag, reason="return").first()
        self.assertIsNotNone(kc)

    def test_return_not_checked_out(self):
        result = return_tag(self.tag, "U_ALICE")
        self.assertIsNone(result)


class ReminderServiceTests(_CheckoutFixture):

    def _make_old_checkout(self, hours_ago=48):
        co = checkout_tag(self.tag, "U_ALICE", "Alice")
        co.checked_out_at = timezone.now() - timedelta(hours=hours_ago)
        co.save(update_fields=["checked_out_at"])
        return co

    def test_due_reminders_finds_old_checkout(self):
        self._make_old_checkout(hours_ago=48)
        now = timezone.now()
        qs = due_reminders(now)
        self.assertEqual(qs.count(), 1)

    def test_due_reminders_skips_recent(self):
        checkout_tag(self.tag, "U_ALICE", "Alice")
        now = timezone.now()
        qs = due_reminders(now)
        self.assertEqual(qs.count(), 0)

    def test_due_reminders_skips_recently_reminded(self):
        co = self._make_old_checkout(hours_ago=48)
        record_reminder_sent(co)
        now = timezone.now()
        qs = due_reminders(now)
        self.assertEqual(qs.count(), 0)

    def test_snooze_resets_timer(self):
        co = self._make_old_checkout(hours_ago=48)
        snooze_reminder(co)
        co.refresh_from_db()
        self.assertIsNotNone(co.last_reminder_at)
        # Should no longer be due.
        qs = due_reminders(timezone.now())
        self.assertEqual(qs.count(), 0)

    def test_record_reminder_increments_count(self):
        co = self._make_old_checkout(hours_ago=48)
        self.assertEqual(co.reminder_count, 0)
        record_reminder_sent(co)
        co.refresh_from_db()
        self.assertEqual(co.reminder_count, 1)

    def test_open_checkouts_returns_open(self):
        checkout_tag(self.tag, "U_ALICE", "Alice")
        self.assertEqual(open_checkouts().count(), 1)

    def test_open_checkouts_excludes_returned(self):
        checkout_tag(self.tag, "U_ALICE", "Alice")
        return_tag(self.tag, "U_ALICE")
        self.assertEqual(open_checkouts().count(), 0)


class ReminderWindowTests(TestCase):

    def test_9am_in_window(self):
        t = datetime(2026, 11, 20, 9, 0, tzinfo=ET)
        self.assertTrue(in_reminder_window(t))

    def test_4pm_in_window(self):
        t = datetime(2026, 11, 20, 16, 0, tzinfo=ET)
        self.assertTrue(in_reminder_window(t))

    def test_8am_outside(self):
        t = datetime(2026, 11, 20, 8, 59, tzinfo=ET)
        self.assertFalse(in_reminder_window(t))

    def test_5pm_outside(self):
        t = datetime(2026, 11, 20, 17, 0, tzinfo=ET)
        self.assertFalse(in_reminder_window(t))


class ParseQueryCommandTests(TestCase):
    """parse_query recognizes take/return/whats_out."""

    def test_take(self):
        self.assertEqual(
            parse_query("take A-038"),
            {"type": "take", "value": "A-038"},
        )

    def test_take_loose_key_id(self):
        self.assertEqual(
            parse_query("take a38"),
            {"type": "take", "value": "A-038"},
        )

    def test_checkout_alias(self):
        self.assertEqual(
            parse_query("checkout B-001"),
            {"type": "take", "value": "B-001"},
        )

    def test_check_out_alias(self):
        self.assertEqual(
            parse_query("check out A-010"),
            {"type": "take", "value": "A-010"},
        )

    def test_return(self):
        self.assertEqual(
            parse_query("return A-038"),
            {"type": "return", "value": "A-038"},
        )

    def test_bring_back_alias(self):
        self.assertEqual(
            parse_query("bring back A-005"),
            {"type": "return", "value": "A-005"},
        )

    def test_whats_out(self):
        self.assertEqual(
            parse_query("whats out"),
            {"type": "whats_out", "value": ""},
        )

    def test_whats_out_apostrophe(self):
        self.assertEqual(
            parse_query("what's out"),
            {"type": "whats_out", "value": ""},
        )

    def test_whats_out_question_mark(self):
        self.assertEqual(
            parse_query("whats out?"),
            {"type": "whats_out", "value": ""},
        )

    def test_take_non_key_falls_to_address(self):
        """'take hello' — take regex matches but key_id extraction fails → address."""
        result = parse_query("take hello")
        self.assertEqual(result["type"], "address")


@override_settings(KEYBOT_CHANNEL_ID="C_TEST")
class KeysCronTests(TestCase):
    """keys_cron orchestration logic."""

    @patch("keys.management.commands.keys_cron.post_message")
    @patch("keys.management.commands.keys_cron.call_command")
    @patch("keys.management.commands.keys_cron.datetime")
    def test_remind_every_hour_refresh_at_6_18(self, mock_dt, mock_call, mock_post):
        from io import StringIO
        from django.core.management import call_command as real_call

        # Simulate hour 10 ET — remind runs, refresh does not.
        mock_dt.now.return_value = datetime(2026, 11, 20, 10, 0, tzinfo=ET)
        out = StringIO()
        real_call("keys_cron", stdout=out)

        # keys_remind called, keys_refresh NOT called.
        mock_call.assert_called_once_with("keys_remind", stdout=mock_call.call_args[1]["stdout"])
        self.assertIn("ok", out.getvalue())

    @patch("keys.management.commands.keys_cron.post_message")
    @patch("keys.management.commands.keys_cron.call_command")
    @patch("keys.management.commands.keys_cron.datetime")
    def test_refresh_runs_at_hour_6(self, mock_dt, mock_call, mock_post):
        from io import StringIO
        from django.core.management import call_command as real_call

        mock_dt.now.return_value = datetime(2026, 11, 20, 6, 0, tzinfo=ET)
        out = StringIO()
        real_call("keys_cron", stdout=out)

        # Both keys_remind and keys_refresh called.
        calls = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("keys_remind", calls)
        self.assertIn("keys_refresh", calls)

    @patch("keys.management.commands.keys_cron.post_message")
    @patch("keys.management.commands.keys_cron.call_command")
    @patch("keys.management.commands.keys_cron.datetime")
    def test_remind_failure_does_not_block_refresh(self, mock_dt, mock_call, mock_post):
        from io import StringIO
        from django.core.management import call_command as real_call

        mock_dt.now.return_value = datetime(2026, 11, 20, 18, 0, tzinfo=ET)

        def _side_effect(cmd, **kwargs):
            if cmd == "keys_remind":
                raise RuntimeError("remind broke")

        mock_call.side_effect = _side_effect

        with self.assertRaises(SystemExit) as ctx:
            real_call("keys_cron", stdout=StringIO())

        # Failed overall, but keys_refresh was still attempted.
        calls = [c[0][0] for c in mock_call.call_args_list]
        self.assertIn("keys_refresh", calls)
        self.assertEqual(ctx.exception.code, 1)


# ── Interaction button tests ────────────────────────────────────────


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    ROOT_URLCONF="config.urls",
)
class LookupButtonTests(_CheckoutFixture):
    """Take / Return buttons on lookup messages."""

    def _interaction_post(self, action_id, value, user_id="U_ALICE"):
        payload = {
            "type": "block_actions",
            "user": {"id": user_id},
            "channel": {"id": "C_TEST"},
            "message": {"ts": "1234567890.123456"},
            "actions": [{"action_id": action_id, "value": value}],
        }
        return self.client.post(
            "/slack/keybot/interactions/",
            urlencode({"payload": json.dumps(payload)}),
            content_type="application/x-www-form-urlencoded",
        )

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Alice")
    @patch("keys.views.post_message")
    @patch("keys.views.update_message")
    def test_take_button_creates_checkout(
        self, mock_update, mock_post, mock_name, mock_sig,
    ):
        resp = self._interaction_post("keybot_take:A-099", "A-099")
        self.assertEqual(resp.status_code, 200)

        # Checkout created.
        co = Checkout.objects.filter(tag=self.tag, returned_at__isnull=True)
        self.assertEqual(co.count(), 1)

        # chat.update refreshed the lookup message.
        mock_update.assert_called_once()

        # Threaded confirmation posted.
        mock_post.assert_called_once()
        self.assertIn("checked out", mock_post.call_args[0][1])

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Bob")
    @patch("keys.views.post_ephemeral")
    @patch("keys.views.update_message")
    @patch("keys.views.post_message")
    def test_take_already_out_ephemeral(
        self, mock_post, mock_update, mock_ephemeral, mock_name, mock_sig,
    ):
        checkout_tag(self.tag, "U_ALICE", "Alice")

        resp = self._interaction_post("keybot_take:A-099", "A-099", user_id="U_BOB")
        self.assertEqual(resp.status_code, 200)

        # Ephemeral refusal.
        mock_ephemeral.assert_called_once()
        self.assertIn("already checked out", mock_ephemeral.call_args[0][2])

        # No new Checkout, no message update, no thread reply.
        self.assertEqual(
            Checkout.objects.filter(returned_at__isnull=True).count(), 1,
        )
        mock_update.assert_not_called()
        mock_post.assert_not_called()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Alice")
    @patch("keys.views.post_message")
    @patch("keys.views.update_message")
    def test_return_button_from_lookup(
        self, mock_update, mock_post, mock_name, mock_sig,
    ):
        checkout_tag(self.tag, "U_ALICE", "Alice")

        resp = self._interaction_post("keybot_return:A-099", "A-099")
        self.assertEqual(resp.status_code, 200)

        # Checkout closed.
        self.assertEqual(
            Checkout.objects.filter(returned_at__isnull=True).count(), 0,
        )

        # chat.update refreshed the lookup message.
        mock_update.assert_called_once()

        # Threaded confirmation.
        mock_post.assert_called_once()
        self.assertIn("returned", mock_post.call_args[0][1])


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    ROOT_URLCONF="config.urls",
)
class ReminderButtonTests(_CheckoutFixture):
    """Reminder Return (anyone) and Snooze (holder-only) buttons."""

    def _interaction_post(self, action_id, value, user_id="U_ALICE"):
        payload = {
            "type": "block_actions",
            "user": {"id": user_id},
            "channel": {"id": "C_TEST"},
            "message": {"ts": "1234567890.123456"},
            "actions": [{"action_id": action_id, "value": value}],
        }
        return self.client.post(
            "/slack/keybot/interactions/",
            urlencode({"payload": json.dumps(payload)}),
            content_type="application/x-www-form-urlencoded",
        )

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Bob")
    @patch("keys.views.post_message")
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_non_holder_reminder_return_closes_checkout(
        self, mock_ephemeral, mock_update, mock_post, mock_name, mock_sig,
    ):
        """Anyone can press the reminder Return button."""
        co = checkout_tag(self.tag, "U_ALICE", "Alice")

        resp = self._interaction_post(
            "keybot_reminder_return", str(co.pk), user_id="U_BOB",
        )
        self.assertEqual(resp.status_code, 200)

        # Checkout closed.
        self.assertEqual(
            Checkout.objects.filter(returned_at__isnull=True).count(), 0,
        )

        # Message updated, no ephemeral refusal.
        mock_update.assert_called_once()
        mock_ephemeral.assert_not_called()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Bob")
    @patch("keys.views.post_ephemeral")
    @patch("keys.views.update_message")
    def test_non_holder_snooze_refused(
        self, mock_update, mock_ephemeral, mock_name, mock_sig,
    ):
        """Only the holder can press Still have it."""
        co = checkout_tag(self.tag, "U_ALICE", "Alice")

        resp = self._interaction_post(
            "keybot_snooze", str(co.pk), user_id="U_BOB",
        )
        self.assertEqual(resp.status_code, 200)

        # Ephemeral refusal.
        mock_ephemeral.assert_called_once()
        self.assertIn("Only Alice", mock_ephemeral.call_args[0][2])

        # No snooze applied, no message update.
        co.refresh_from_db()
        self.assertIsNone(co.last_reminder_at)
        mock_update.assert_not_called()


# ── Block Kit validation helpers & tests ───────────────────────────


def _validate_blocks(test_case, blocks):
    """Assert a Block Kit payload satisfies Slack's validation rules."""
    test_case.assertIsInstance(blocks, list)
    action_ids = []
    for block in blocks:
        btype = block.get("type")
        if btype == "section":
            text = block.get("text", {}).get("text", "")
            test_case.assertLessEqual(
                len(text), 3000,
                f"section text exceeds 3000 chars ({len(text)})",
            )
            test_case.assertTrue(text, "section text must not be empty")
        elif btype == "actions":
            for elem in block.get("elements", []):
                aid = elem.get("action_id", "")
                test_case.assertNotIn(
                    aid, action_ids, f"duplicate action_id: {aid}",
                )
                action_ids.append(aid)
                btn_text = elem.get("text", {}).get("text", "")
                test_case.assertLessEqual(
                    len(btn_text), 75,
                    f"button text exceeds 75 chars: {btn_text!r}",
                )
                val = elem.get("value", "")
                test_case.assertLessEqual(
                    len(val), 2000, "button value exceeds 2000 chars",
                )


class BlockKitValidationTests(TestCase):
    """Block Kit payload validation for Slack compliance."""

    def test_lookup_blocks_one_on_hook_tag(self):
        tags = [{"key_id": "A-038", "status_raw": "on_hook",
                 "checkout": None, "ring_notes": ""}]
        buttons = take_buttons(tags)
        blocks = lookup_blocks("some lookup text", buttons)
        self.assertIsNotNone(blocks)
        _validate_blocks(self, blocks)

    def test_lookup_blocks_two_on_hook_tags(self):
        """The bug case: 2 on_hook tags now produce unique action_ids."""
        tags = [
            {"key_id": "A-038", "status_raw": "on_hook",
             "checkout": None, "ring_notes": ""},
            {"key_id": "A-134", "status_raw": "on_hook",
             "checkout": None, "ring_notes": ""},
        ]
        buttons = take_buttons(tags)
        blocks = lookup_blocks("some lookup text", buttons)
        self.assertIsNotNone(blocks)
        _validate_blocks(self, blocks)
        # Verify two distinct buttons.
        self.assertEqual(len(blocks[1]["elements"]), 2)

    def test_lookup_blocks_no_tags(self):
        buttons = take_buttons([])
        blocks = lookup_blocks("text", buttons)
        self.assertIsNone(blocks)

    def test_reminder_blocks_valid(self):
        now = timezone.now()
        checked_out_at = now - timedelta(hours=48)
        text, blocks = reminder_blocks(
            "A-038", "U_ALICE", checked_out_at, now, checkout_id=1,
        )
        _validate_blocks(self, blocks)

    def test_format_unit_long_notes_under_3000(self):
        """Even with 5 long notes, section text stays under 3000 chars."""
        info = {
            "address": "28 Bass Lane",
            "unit_number": "1",
            "tags": [{"key_id": "A-038", "status_raw": "on_hook",
                       "checkout": None, "ring_notes": "front door"}],
            "property_tags": [],
            "notes": [
                {"text": "x" * 300, "source": "RV", "date": "2026-01-01"}
                for _ in range(5)
            ],
        }
        text = format_unit(info)
        buttons = take_buttons(info["tags"])
        blocks = lookup_blocks(text, buttons)
        self.assertIsNotNone(blocks)
        _validate_blocks(self, blocks)


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    ROOT_URLCONF="config.urls",
)
class PostPressBlockTests(TestCase):
    """Validate blocks after button presses on multi-tag units."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="28 Bass Lane",
            address_line_1="28 Bass Lane",
            city="Asheville", state="NC", postal_code="28801",
            rentvine_id=9100,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="28 Bass Lane",
            unit_number="1",
            rentvine_id=9101,
        )
        cls.tag1 = Tag.objects.create(
            box="A", number=38, unit=cls.unit, ring_notes="front door",
        )
        cls.tag2 = Tag.objects.create(
            box="A", number=134, unit=cls.unit, ring_notes="back door",
        )

    def _interaction_post(self, action_id, value, user_id="U_ALICE"):
        payload = {
            "type": "block_actions",
            "user": {"id": user_id},
            "channel": {"id": "C_TEST"},
            "message": {"ts": "1234567890.123456"},
            "actions": [{"action_id": action_id, "value": value}],
        }
        return self.client.post(
            "/slack/keybot/interactions/",
            urlencode({"payload": json.dumps(payload)}),
            content_type="application/x-www-form-urlencoded",
        )

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Alice")
    @patch("keys.views.post_message")
    @patch("keys.views.update_message")
    def test_post_press_blocks_mixed_statuses(
        self, mock_update, mock_post, mock_name, mock_sig,
    ):
        """After taking one of two tags, rebuilt blocks have mixed buttons."""
        resp = self._interaction_post("keybot_take:A-038", "A-038")
        self.assertEqual(resp.status_code, 200)

        # Validate the blocks passed to chat.update.
        mock_update.assert_called_once()
        blocks = mock_update.call_args[1]["blocks"]
        _validate_blocks(self, blocks)

        # Should have 2 buttons: Return A-038, Take A-134.
        actions_block = blocks[1]
        aids = [e["action_id"] for e in actions_block["elements"]]
        self.assertIn("keybot_return:A-038", aids)
        self.assertIn("keybot_take:A-134", aids)

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Alice")
    @patch("keys.views.post_message")
    @patch("keys.views.update_message",
           side_effect=RuntimeError("invalid_blocks"))
    def test_update_message_failure_falls_back_to_thread(
        self, mock_update, mock_post, mock_name, mock_sig,
    ):
        """When chat.update fails, a plain-text threaded reply is still sent."""
        resp = self._interaction_post("keybot_take:A-038", "A-038")
        self.assertEqual(resp.status_code, 200)

        # update_message was attempted but failed.
        mock_update.assert_called_once()

        # _safe_reply still posted a threaded confirmation.
        mock_post.assert_called_once()
        self.assertIn("checked out", mock_post.call_args[0][1])


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    ROOT_URLCONF="config.urls",
)
class EventReplyFallbackTests(_CheckoutFixture):
    """post_message blocks failure retries as plain text via _safe_reply."""

    def _event_body(self, text):
        return json.dumps({
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C_TEST",
                "user": "U_ALICE",
                "text": f"<@UBOT> {text}",
                "ts": "111.222",
            },
        }).encode()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.post_message")
    def test_blocks_failure_retries_plain_text(self, mock_post, mock_sig):
        """When post_message with blocks raises, _safe_reply sends plain text."""
        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1 and kwargs.get("blocks"):
                raise RuntimeError("invalid_blocks")

        mock_post.side_effect = _side_effect

        resp = self.client.post(
            "/slack/keybot/events/",
            self._event_body("A-099"),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # First call had blocks and failed; second call (plain text) succeeded.
        self.assertEqual(mock_post.call_count, 2)
        # Second call should have no blocks kwarg.
        second_kwargs = mock_post.call_args_list[1][1]
        self.assertNotIn("blocks", second_kwargs)

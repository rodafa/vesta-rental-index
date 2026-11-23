"""Tests for the KeyBot polish round: encoding, timezone, parsing, display, chunking, confirm text."""

from datetime import datetime, timezone as dt_tz
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Property, Unit
from keys.models import PendingKeyChange, Tag
from keys.replies import _chunk_urgent, _display_address
from keys.selectors import _fmt_date, parse_query
from keys.slack import _FORM_METHODS, _slack_api
from keys.views import _bad_command_usage, _confirm_text


# ── Slack API encoding ────────────────────────────────────────────


class SlackApiEncodingTests(TestCase):
    """_slack_api uses data= for form-encoded methods, json= for the rest."""

    @patch("keys.slack.settings")
    @patch("keys.slack.requests.post")
    def test_users_info_uses_data(self, mock_post, mock_settings):
        mock_settings.SLACK_BOT_TOKEN = "xoxb-test"
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"ok": True, "user": {}}),
        )
        mock_post.return_value.raise_for_status = MagicMock()

        _slack_api("users.info", {"user": "U123"})

        _, kwargs = mock_post.call_args
        self.assertIn("data", kwargs)
        self.assertNotIn("json", kwargs)
        self.assertEqual(kwargs["data"], {"user": "U123"})

    @patch("keys.slack.settings")
    @patch("keys.slack.requests.post")
    def test_chat_postmessage_uses_json(self, mock_post, mock_settings):
        mock_settings.SLACK_BOT_TOKEN = "xoxb-test"
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"ok": True}),
        )
        mock_post.return_value.raise_for_status = MagicMock()

        _slack_api("chat.postMessage", {"channel": "C1", "text": "hi"})

        _, kwargs = mock_post.call_args
        self.assertIn("json", kwargs)
        self.assertNotIn("data", kwargs)


# ── _fmt_date ET timezone ─────────────────────────────────────────


class FmtDateETTests(TestCase):
    """_fmt_date converts datetime objects to ET before formatting."""

    def test_utc_next_day_formats_as_et_date(self):
        """8pm ET on Nov 22 is 01:00 UTC on Nov 23 — should show Nov 22."""
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        # 2026-11-23 01:00 UTC = 2026-11-22 20:00 ET
        utc_dt = datetime(2026, 11, 23, 1, 0, tzinfo=dt_tz.utc)
        result = _fmt_date(utc_dt)
        self.assertEqual(result, "Nov 22, 2026")

    def test_plain_date_unchanged(self):
        """Plain date objects pass through without conversion."""
        from datetime import date
        d = date(2026, 11, 22)
        result = _fmt_date(d)
        self.assertEqual(result, "Nov 22, 2026")


# ── Set code loose parsing ────────────────────────────────────────


class SetCodeLooseParseTests(TestCase):
    """parse_query handles all set_code variants."""

    def test_primary_set_code_to(self):
        result = parse_query("set code 28 Bass Lane to 1234")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "28 Bass Lane")
        self.assertEqual(result["value"]["code"], "1234")

    def test_set_lockbox_to(self):
        """'set lockbox <addr> to <code>' (no word 'code')."""
        result = parse_query("set lockbox 90 moody ave unit 2 to 1776")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["code"], "1776")

    def test_set_lockbox_code_to(self):
        result = parse_query("set lockbox code 28 Bass to 1234")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["code"], "1234")

    def test_set_code_for_to(self):
        result = parse_query("set code for 28 Bass Lane to 1234")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "28 Bass Lane")
        self.assertEqual(result["value"]["code"], "1234")

    def test_set_lockbox_code_for_to(self):
        result = parse_query("set lockbox code for 28 Bass Lane to 1234")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "28 Bass Lane")

    def test_no_to_with_unit(self):
        """'set lockbox code for 90 moody unit 2 1776, lockbox on porch'."""
        result = parse_query(
            "set lockbox code for 90 moody unit 2 1776, lockbox on porch"
        )
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "90 moody unit 2")
        self.assertEqual(
            result["value"]["code"], "1776, lockbox on porch",
        )

    def test_no_to_with_suffix(self):
        """'set code 90 moody ave 1776' — suffix as boundary."""
        result = parse_query("set code 90 moody ave 1776")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "90 moody ave")
        self.assertEqual(result["value"]["code"], "1776")

    def test_no_to_no_boundary_bad_command(self):
        """'set code 90 moody 1776' — no suffix, no unit → bad_command."""
        result = parse_query("set code 90 moody 1776")
        self.assertEqual(result["type"], "bad_command")
        self.assertEqual(result["value"], "set")


# ── Bad command detection ─────────────────────────────────────────


class BadCommandTests(TestCase):
    """Commands that start with a keyword but don't match any pattern."""

    def test_bad_set_command(self):
        result = parse_query("set something weird")
        self.assertEqual(result["type"], "bad_command")

    def test_bad_assign_command(self):
        result = parse_query("assign something weird")
        self.assertEqual(result["type"], "bad_command")

    def test_bare_missing_is_key_health_not_bad_command(self):
        """'missing' alone must still be key_health."""
        result = parse_query("missing")
        self.assertEqual(result["type"], "key_health")

    def test_whats_missing_is_key_health(self):
        result = parse_query("what's missing")
        self.assertEqual(result["type"], "key_health")

    def test_bad_command_usage_hint(self):
        hint = _bad_command_usage("set")
        self.assertIn("set code", hint)

    def test_bad_command_usage_unknown(self):
        hint = _bad_command_usage("xyz")
        self.assertIn("help", hint)


# ── Address display dedup ─────────────────────────────────────────


class DisplayAddressDedupTests(TestCase):
    """_display_address deduplicates redundant address variants."""

    def test_same_address_different_suffix(self):
        """'12 La Salle Lane' / '12 La Salle Ln' → property address only."""
        result = _display_address("12 La Salle Lane", "12 La Salle Ln")
        self.assertEqual(result, "12 La Salle Lane")
        self.assertNotIn("\u2014", result)

    def test_normal_unit_number(self):
        result = _display_address("28 Bass Lane", "2")
        self.assertIn("\u2014", result)
        self.assertIn("Unit 2", result)

    def test_unit_prefix_kept(self):
        result = _display_address("28 Bass Lane", "Unit 2")
        self.assertIn("\u2014", result)
        self.assertIn("Unit 2", result)
        # Should not say "Unit Unit 2"
        self.assertNotIn("Unit Unit", result)

    def test_empty_unit(self):
        result = _display_address("28 Bass Lane", "")
        self.assertEqual(result, "28 Bass Lane")


# ── _chunk_urgent ─────────────────────────────────────────────────


class ChunkUrgentTests(TestCase):
    """_chunk_urgent splits lines into multiple section blocks."""

    def test_three_items_one_block(self):
        lines = ["header", "item 1", "item 2"]
        blocks = _chunk_urgent(lines, max_lines=5)
        self.assertEqual(len(blocks), 1)
        self.assertIn("header", blocks[0]["text"]["text"])

    def test_seven_items_two_blocks(self):
        lines = [f"line {i}" for i in range(7)]
        blocks = _chunk_urgent(lines, max_lines=5)
        self.assertEqual(len(blocks), 2)
        # First block has 5 lines, second has 2
        self.assertEqual(blocks[0]["text"]["text"].count("\n"), 4)
        self.assertEqual(blocks[1]["text"]["text"].count("\n"), 1)

    def test_twelve_items_three_blocks(self):
        lines = [f"line {i}" for i in range(12)]
        blocks = _chunk_urgent(lines, max_lines=5)
        self.assertEqual(len(blocks), 3)

    def test_empty_list(self):
        blocks = _chunk_urgent([], max_lines=5)
        self.assertEqual(blocks, [])


# ── _confirm_text ─────────────────────────────────────────────────


class ConfirmTextTests(TestCase):
    """_confirm_text produces descriptive confirmation messages."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="10 Elm Street",
            address_line_1="10 Elm Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=8000,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="10 Elm Street",
            unit_number="1",
            rentvine_id=8001,
        )
        cls.tag = Tag.objects.create(
            box="A", number=99, unit=cls.unit, ring_notes="front",
        )

    def _make_pending(self, action, payload):
        return PendingKeyChange.objects.create(
            requester_slack_id="U_TEST",
            requester_name="rodrigo",
            action=action,
            payload=payload,
        )

    def test_set_code(self):
        p = self._make_pending("set_code", {
            "unit_id": self.unit.pk,
            "property_id": self.prop.pk,
            "code": "1234",
        })
        text = _confirm_text(p)
        self.assertIn("Code set for", text)
        self.assertIn("rodrigo", text)

    def test_assign(self):
        p = self._make_pending("assign", {
            "tag_id": self.tag.pk,
            "unit_id": self.unit.pk,
        })
        text = _confirm_text(p)
        self.assertIn("A-099", text)
        self.assertIn("assigned to", text)
        self.assertIn("rodrigo", text)

    def test_retire(self):
        p = self._make_pending("retire", {"tag_id": self.tag.pk})
        text = _confirm_text(p)
        self.assertIn("A-099", text)
        self.assertIn("retired", text)
        self.assertIn("rodrigo", text)

    def test_mark_missing(self):
        p = self._make_pending("mark_missing", {"tag_id": self.tag.pk})
        text = _confirm_text(p)
        self.assertIn("A-099", text)
        self.assertIn("marked missing", text)

    def test_found(self):
        p = self._make_pending("found", {"tag_id": self.tag.pk})
        text = _confirm_text(p)
        self.assertIn("A-099", text)
        self.assertIn("marked found", text)

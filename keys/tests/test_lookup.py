"""Tests for KeyBot selectors, reply formatting, and view dispatch."""

import hashlib
import hmac
import json
import logging
import time
from datetime import date
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from core.models import Property, Unit
from keys.models import AccessNote, Tag
from keys.replies import (
    _display_address,
    format_did_you_mean,
    format_help,
    format_not_found,
    format_tag,
    format_unit,
    format_unit_list,
)
from keys.selectors import (
    find_tag_by_key_id,
    find_units,
    find_units_by_street,
    parse_query,
    tag_info,
    unit_key_info,
)
from keys.views import slack_events

SIGNING_SECRET = "test_signing_secret_abc"
CHANNEL_ID = "C_KEYBOT_TEST"


def _sign(body: bytes, secret: str = SIGNING_SECRET) -> tuple[str, str]:
    ts = str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return ts, sig


def _make_mention_request(text: str, channel: str = CHANNEL_ID) -> "WSGIRequest":
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": channel,
            "text": f"<@U123BOT> {text}",
            "ts": "1234567890.123456",
        },
    }
    body = json.dumps(payload).encode()
    ts, sig = _sign(body)
    factory = RequestFactory()
    return factory.post(
        "/slack/keybot/events/",
        data=body,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=ts,
        HTTP_X_SLACK_SIGNATURE=sig,
    )


class _BassLaneFixture(TestCase):
    """28 Bass Lane with 3 units, tags, and access notes."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="28 Bass Lane",
            address_line_1="28 Bass Lane",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9000,
        )
        cls.unit1 = Unit.objects.create(
            property=cls.prop,
            address_line_1="28 Bass Lane",
            unit_number="1",
            rentvine_id=9001,
        )
        cls.unit2 = Unit.objects.create(
            property=cls.prop,
            address_line_1="28 Bass Lane",
            unit_number="2",
            rentvine_id=9002,
        )
        cls.unit3 = Unit.objects.create(
            property=cls.prop,
            address_line_1="28 Bass Lane",
            unit_number="3",
            rentvine_id=9003,
        )
        # Tags: A-038 + A-134 on unit 1 (duplicate), A-039 on unit 2, none on unit 3
        cls.tag_038 = Tag.objects.create(
            box="A", number=38, unit=cls.unit1,
            ring_notes="Schlage front / Kwikset back",
        )
        cls.tag_134 = Tag.objects.create(
            box="A", number=134, unit=cls.unit1,
        )
        cls.tag_039 = Tag.objects.create(
            box="A", number=39, unit=cls.unit2,
            ring_notes="single deadbolt",
        )
        # Unit-level Rentvine note (property=None per loader.py convention)
        cls.rv_note = AccessNote.objects.create(
            unit=cls.unit1,
            source="rentvine_unit_note",
            source_ref="note-1",
            text="Lockbox 1776",
            source_date=date(2025, 10, 22),
        )
        # Unit-level RentEngine note (no date)
        cls.re_note = AccessNote.objects.create(
            unit=cls.unit1,
            source="rentengine_internal_notes",
            source_ref="re-1",
            text="Accompanied Showing",
            source_date=None,
        )
        # Property-level note (unit=None)
        cls.prop_note = AccessNote.objects.create(
            property=cls.prop,
            source="rentvine_property_note",
            source_ref="note-2",
            text="Gate code 4455",
            source_date=date(2025, 9, 1),
        )


class SelectorTests(_BassLaneFixture):

    def test_find_units_exact_with_unit_number(self):
        result = find_units("28 Bass ln unit 1")
        self.assertTrue(result["exact"])
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0].pk, self.unit1.pk)

    def test_find_units_filler_stripped(self):
        result = find_units("what's the lockbox for 28 bass lane unit 1?")
        self.assertTrue(result["exact"])
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0].pk, self.unit1.pk)

    def test_find_units_curly_apostrophe_filler(self):
        result = find_units("what\u2019s the lockbox for 28 bass lane unit 1?")
        self.assertTrue(result["exact"])
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0].pk, self.unit1.pk)

    def test_find_units_address_only_returns_all(self):
        result = find_units("28 Bass")
        self.assertTrue(result["exact"])
        self.assertEqual(len(result["units"]), 3)

    def test_find_tag_by_key_id_loose(self):
        tag = find_tag_by_key_id("a-38")
        self.assertIsNotNone(tag)
        self.assertEqual(tag.key_id, "A-038")

    def test_find_tag_not_found(self):
        self.assertIsNone(find_tag_by_key_id("A-999"))

    def test_parse_query_help(self):
        self.assertEqual(parse_query("help")["type"], "help")

    def test_parse_query_empty(self):
        self.assertEqual(parse_query("")["type"], "empty")

    def test_parse_query_key_id(self):
        p = parse_query("a-38")
        self.assertEqual(p["type"], "key_id")
        self.assertEqual(p["value"], "A-038")

    def test_parse_query_greeting_is_help(self):
        self.assertEqual(parse_query("Hello!")["type"], "help")

    def test_parse_query_address_not_key_id(self):
        """'17 Clyde St B' must be type address, not key_id."""
        p = parse_query("17 Clyde St B")
        self.assertEqual(p["type"], "address")

    def test_unit_key_info_has_tags_and_notes(self):
        info = unit_key_info(self.unit1)
        self.assertEqual(len(info["tags"]), 2)
        key_ids = {t["key_id"] for t in info["tags"]}
        self.assertEqual(key_ids, {"A-038", "A-134"})
        # 2 unit-level + 1 property-level note
        self.assertEqual(len(info["notes"]), 3)

    def test_unit_key_info_no_tags(self):
        info = unit_key_info(self.unit3)
        self.assertEqual(info["tags"], [])

    def test_tag_info_returns_dict(self):
        info = tag_info(self.tag_038)
        self.assertEqual(info["key_id"], "A-038")
        self.assertEqual(info["unit_address"], "28 Bass Lane")
        self.assertEqual(info["unit_number"], "1")
        self.assertFalse(info["is_orphan"])


class ReplyFormattingTests(_BassLaneFixture):

    def test_format_unit_duplicate_warning(self):
        info = unit_key_info(self.unit1)
        reply = format_unit(info)
        self.assertIn("\u26a0\ufe0f", reply)
        self.assertIn("2 tags on this unit", reply)
        self.assertIn("A-038", reply)
        self.assertIn("A-134", reply)

    def test_format_unit_no_key(self):
        info = unit_key_info(self.unit3)
        reply = format_unit(info)
        self.assertIn("No key on file", reply)

    def test_format_note_rentvine_has_date(self):
        info = unit_key_info(self.unit1)
        reply = format_unit(info)
        self.assertIn("Rentvine", reply)
        self.assertIn("Oct 22, 2025", reply)

    def test_format_note_rentengine_no_date(self):
        info = unit_key_info(self.unit1)
        reply = format_unit(info)
        self.assertIn("_(RentEngine)_", reply)

    def test_format_unit_property_level_note(self):
        info = unit_key_info(self.unit1)
        reply = format_unit(info)
        self.assertIn("Gate code 4455", reply)

    def test_format_unit_list_shows_all(self):
        infos = [unit_key_info(u) for u in [self.unit1, self.unit2, self.unit3]]
        reply = format_unit_list(infos, 3)
        self.assertIn("Found 3 units", reply)
        self.assertIn("Unit 1", reply)
        self.assertIn("Unit 2", reply)
        self.assertIn("Unit 3", reply)

    def test_format_tag_unit_linked(self):
        info = tag_info(self.tag_038)
        reply = format_tag(info)
        self.assertIn("A-038", reply)
        self.assertIn("28 Bass Lane", reply)
        self.assertIn("Unit 1", reply)

    def test_format_not_found(self):
        reply = format_not_found("999 Nowhere")
        self.assertIn("No results", reply)
        self.assertIn("999 Nowhere", reply)

    def test_format_not_found_truncates(self):
        long_query = "x" * 200
        reply = format_not_found(long_query)
        self.assertNotIn("x" * 81, reply)

    def test_format_help(self):
        reply = format_help()
        self.assertIn("KeyBot", reply)
        self.assertIn("help", reply)

    def test_escape_in_notes(self):
        """Angle brackets in note text are escaped."""
        note = AccessNote.objects.create(
            unit=self.unit1,
            source="keybot",
            text="Use <code> 1234 & press >",
        )
        info = unit_key_info(self.unit1)
        reply = format_unit(info)
        self.assertIn("&lt;code&gt;", reply)
        self.assertIn("&amp;", reply)
        note.delete()


@override_settings(
    SLACK_SIGNING_SECRET=SIGNING_SECRET,
    SLACK_BOT_TOKEN="xoxb-test-token",
    KEYBOT_CHANNEL_ID=CHANNEL_ID,
)
class ViewIntegrationTests(_BassLaneFixture):

    def test_mention_address_single_unit(self):
        request = _make_mention_request("28 Bass ln unit 1")
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_called_once()
        reply = mock_post.call_args[0][1]
        self.assertIn("28 Bass Lane", reply)
        self.assertIn("A-038", reply)

    def test_mention_address_multi_match(self):
        request = _make_mention_request("28 Bass")
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        reply = mock_post.call_args[0][1]
        self.assertIn("Found 3 units", reply)

    def test_mention_key_id(self):
        request = _make_mention_request("a-38")
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        reply = mock_post.call_args[0][1]
        self.assertIn("A-038", reply)
        self.assertIn("Unit 1", reply)

    def test_mention_not_found(self):
        request = _make_mention_request("999 Nowhere")
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        reply = mock_post.call_args[0][1]
        self.assertIn("No results", reply)

    def test_mention_help(self):
        request = _make_mention_request("help")
        with patch("keys.views.post_message") as mock_post:
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        reply = mock_post.call_args[0][1]
        self.assertIn("KeyBot", reply)

    def test_17_clyde_st_b_is_address(self):
        """'17 Clyde St B' parses as address, not key_id."""
        p = parse_query("17 Clyde St B")
        self.assertEqual(p["type"], "address")

    def test_selector_error_posts_error_reply(self):
        request = _make_mention_request("28 Bass ln unit 1")
        with (
            patch("keys.views.find_units", side_effect=RuntimeError("db boom")),
            patch("keys.views.post_message") as mock_post,
            self.assertLogs("keys.views", level="ERROR") as log_ctx,
        ):
            resp = slack_events(request)
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_called_once()
        reply = mock_post.call_args[0][1]
        self.assertIn("Something went wrong", reply)
        self.assertTrue(any("keybot_lookup_error" in m for m in log_ctx.output))


# ---------------------------------------------------------------------------
# Fallback / "did you mean" fixtures and tests
# ---------------------------------------------------------------------------

class _FallbackFixture(TestCase):
    """297 Cosmos Drive (1 unit) and 6 Dogwood Court (units A, B)."""

    @classmethod
    def setUpTestData(cls):
        cls.cosmos_prop = Property.objects.create(
            name="297 Cosmos Drive",
            address_line_1="297 Cosmos Drive",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9100,
        )
        cls.cosmos_unit = Unit.objects.create(
            property=cls.cosmos_prop,
            address_line_1="297 Cosmos Drive",
            unit_number="",
            rentvine_id=9101,
        )
        cls.cosmos_293 = Unit.objects.create(
            property=cls.cosmos_prop,
            address_line_1="297 Cosmos Drive",
            unit_number="293",
            rentvine_id=9102,
        )
        cls.dogwood_prop = Property.objects.create(
            name="6 Dogwood Court",
            address_line_1="6 Dogwood Court",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9200,
        )
        cls.dogwood_a = Unit.objects.create(
            property=cls.dogwood_prop,
            address_line_1="6 Dogwood Court",
            unit_number="A",
            rentvine_id=9201,
        )
        cls.dogwood_b = Unit.objects.create(
            property=cls.dogwood_prop,
            address_line_1="6 Dogwood Court",
            unit_number="B",
            rentvine_id=9202,
        )


class FallbackSelectorTests(_FallbackFixture):

    def test_unit_number_as_street_number_exact(self):
        """'293 Cosmos' → exact match to unit 293 at 297 Cosmos Drive."""
        result = find_units("293 Cosmos")
        self.assertTrue(result["exact"])
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0].pk, self.cosmos_293.pk)

    def test_wrong_number_no_unit_match_suggests(self):
        """'295 Cosmos' has no exact or unit-number match; fallback suggests."""
        result = find_units("295 Cosmos")
        self.assertFalse(result["exact"])
        self.assertEqual(len(result["units"]), 2)
        pks = {u.pk for u in result["units"]}
        self.assertEqual(pks, {self.cosmos_unit.pk, self.cosmos_293.pk})

    def test_street_name_only_dogwood(self):
        """'dogwood' (no number) returns both Dogwood units."""
        result = find_units("dogwood")
        self.assertFalse(result["exact"])
        self.assertEqual(len(result["units"]), 2)
        pks = {u.pk for u in result["units"]}
        self.assertEqual(pks, {self.dogwood_a.pk, self.dogwood_b.pk})

    def test_dog_wood_unit_a_closest_match(self):
        """'dog wood unit a' -> single unit A, narrowed by token."""
        result = find_units("dog wood unit a")
        self.assertFalse(result["exact"])
        self.assertTrue(result["narrowed_by_token"])
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0].pk, self.dogwood_a.pk)


class FallbackReplyTests(_FallbackFixture):

    def test_display_address_no_double_unit_prefix(self):
        """unit_number='Unit 1' should display once, not 'Unit Unit 1'."""
        self.assertEqual(
            _display_address("28 Bass Lane", "Unit 1"),
            "28 Bass Lane \u2014 Unit 1",
        )

    def test_display_address_plain_number(self):
        """unit_number='1' still gets the 'Unit' prefix."""
        self.assertEqual(
            _display_address("28 Bass Lane", "1"),
            "28 Bass Lane \u2014 Unit 1",
        )

    def test_display_address_letter(self):
        """unit_number='A' gets the 'Unit' prefix."""
        self.assertEqual(
            _display_address("6 Dogwood Court", "A"),
            "6 Dogwood Court \u2014 Unit A",
        )


@override_settings(
    SLACK_SIGNING_SECRET=SIGNING_SECRET,
    SLACK_BOT_TOKEN="xoxb-test-token",
    KEYBOT_CHANNEL_ID=CHANNEL_ID,
)
class FallbackViewTests(_FallbackFixture):

    def test_view_closest_match_reply(self):
        """'dog wood unit a' -> Closest match reply with full unit info."""
        request = _make_mention_request("dog wood unit a")
        with patch("keys.views.post_message") as mock_post:
            slack_events(request)
        reply = mock_post.call_args[0][1]
        self.assertIn("_Closest match:_", reply)
        self.assertIn("6 Dogwood Court", reply)
        self.assertIn("Unit A", reply)

    def test_view_did_you_mean_reply(self):
        """'295 Cosmos' -> Did you mean reply (no unit 295 on that street)."""
        request = _make_mention_request("295 Cosmos")
        with patch("keys.views.post_message") as mock_post:
            slack_events(request)
        reply = mock_post.call_args[0][1]
        self.assertIn("No exact match", reply)
        self.assertIn("Did you mean", reply)
        self.assertIn("297 Cosmos Drive", reply)

    def test_view_dogwood_lists_both(self):
        """'dogwood' -> did-you-mean with both units listed."""
        request = _make_mention_request("dogwood")
        with patch("keys.views.post_message") as mock_post:
            slack_events(request)
        reply = mock_post.call_args[0][1]
        self.assertIn("Did you mean", reply)
        self.assertIn("Unit A", reply)
        self.assertIn("Unit B", reply)


class _ManyUnitsFixture(TestCase):
    """Street with 15 units to test the max-10 / unit_key_info cap."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="100 Long Street",
            address_line_1="100 Long Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9300,
        )
        cls.units = []
        for i in range(1, 16):
            u = Unit.objects.create(
                property=cls.prop,
                address_line_1="100 Long Street",
                unit_number=str(i),
                rentvine_id=9300 + i,
            )
            cls.units.append(u)


@override_settings(
    SLACK_SIGNING_SECRET=SIGNING_SECRET,
    SLACK_BOT_TOKEN="xoxb-test-token",
    KEYBOT_CHANNEL_ID=CHANNEL_ID,
)
class ManyUnitsTests(_ManyUnitsFixture):

    def test_exact_multi_match_caps_unit_key_info(self):
        """15 units -> unit_key_info called at most 10 times, reply says '5 more'."""
        request = _make_mention_request("100 Long")
        with (
            patch("keys.views.post_message") as mock_post,
            patch("keys.views.unit_key_info", wraps=unit_key_info) as spy,
        ):
            slack_events(request)
        self.assertLessEqual(spy.call_count, 10)
        reply = mock_post.call_args[0][1]
        self.assertIn("5 more", reply)
        self.assertIn("Found 15 units", reply)

    def test_did_you_mean_caps_unit_key_info(self):
        """Wrong-number fallback to 15 units also caps at 10."""
        request = _make_mention_request("101 Long")
        with (
            patch("keys.views.post_message") as mock_post,
            patch("keys.views.unit_key_info", wraps=unit_key_info) as spy,
        ):
            slack_events(request)
        self.assertLessEqual(spy.call_count, 10)
        reply = mock_post.call_args[0][1]
        self.assertIn("5 more", reply)
        self.assertIn("No exact match", reply)

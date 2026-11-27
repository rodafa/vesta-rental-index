"""Tests for Key Health: selectors, formatters, parse_query, cron."""

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Property, Unit
from keys.models import AccessNote, Checkout, KeyHealthSnapshot, Tag
from keys.replies import format_key_health
from keys.selectors import key_health, parse_query
from keys.tests.test_checkouts import _validate_blocks

ET = ZoneInfo("America/New_York")


# ── Helpers ────────────────────────────────────────────────────────


def _empty_health():
    """Health dict with all categories empty."""
    return {
        "vacant_no_key_no_code": [],
        "vacant_no_key": [],
        "vacant_no_code": [],
        "overdue_checkouts": [],
        "missing_tags": [],
        "duplicate_tags": [],
        "building_tags": [],
        "orphan_tags": [],
        "occupied_no_key": [],
        "occupied_no_code": [],
        "unknown_no_key_count": 0,
        "unknown_no_code_count": 0,
        "total_units": 0,
        "with_key_count": 0,
        "with_code_count": 0,
        "with_both_count": 0,
    }


# ── Fixture ────────────────────────────────────────────────────────


class _KeyHealthFixture(TestCase):
    """Units covering every key health category."""

    @classmethod
    def setUpTestData(cls):
        # (a) Vacant, no key, no code
        p_a = Property.objects.create(
            name="1 Alpha St", address_line_1="1 Alpha St",
            rentvine_id=100,
        )
        cls.unit_a = Unit.objects.create(
            property=p_a, address_line_1="1 Alpha St",
            rentvine_id=1001, rentengine_status="Available",
        )

        # (b) Vacant, no key, HAS code
        p_b = Property.objects.create(
            name="2 Beta St", address_line_1="2 Beta St",
            rentvine_id=200,
        )
        cls.unit_b = Unit.objects.create(
            property=p_b, address_line_1="2 Beta St",
            rentvine_id=2001, rentengine_status="Available",
        )
        AccessNote.objects.create(
            unit=cls.unit_b, property=p_b,
            source="keybot", text="lockbox 1234",
        )

        # (c) Vacant, HAS key, no code
        p_c = Property.objects.create(
            name="3 Gamma St", address_line_1="3 Gamma St",
            rentvine_id=300,
        )
        cls.unit_c = Unit.objects.create(
            property=p_c, address_line_1="3 Gamma St",
            rentvine_id=3001, rentengine_status="Available",
        )
        Tag.objects.create(box="A", number=1, unit=cls.unit_c)

        # (d) Two overdue checkouts (30h and 80h)
        p_d = Property.objects.create(
            name="4 Delta St", address_line_1="4 Delta St",
            rentvine_id=400,
        )
        cls.unit_d = Unit.objects.create(
            property=p_d, address_line_1="4 Delta St",
            rentvine_id=4001,
        )
        cls.tag_24h = Tag.objects.create(
            box="A", number=10, unit=cls.unit_d, status="checked_out",
        )
        cls.tag_72h = Tag.objects.create(
            box="A", number=11, unit=cls.unit_d, status="checked_out",
        )
        now = timezone.now()
        Checkout.objects.create(
            tag=cls.tag_24h,
            checked_out_by_slack_id="U_BOB", checked_out_by_name="Bob",
            checked_out_at=now - timedelta(hours=30),
        )
        Checkout.objects.create(
            tag=cls.tag_72h,
            checked_out_by_slack_id="U_CAROL", checked_out_by_name="Carol",
            checked_out_at=now - timedelta(hours=80),
        )
        AccessNote.objects.create(
            unit=cls.unit_d, property=p_d,
            source="keybot", text="code 5678",
        )

        # (e) Missing tag
        p_e = Property.objects.create(
            name="5 Epsilon St", address_line_1="5 Epsilon St",
            rentvine_id=500,
        )
        cls.unit_e = Unit.objects.create(
            property=p_e, address_line_1="5 Epsilon St",
            rentvine_id=5001,
        )
        Tag.objects.create(
            box="A", number=20, unit=cls.unit_e, status="missing",
        )
        AccessNote.objects.create(
            unit=cls.unit_e, property=p_e,
            source="keybot", text="lockbox 9999",
        )

        # (f) Duplicate tags (2 non-retired tags on one unit)
        p_f = Property.objects.create(
            name="6 Zeta St", address_line_1="6 Zeta St",
            rentvine_id=600,
        )
        cls.unit_f = Unit.objects.create(
            property=p_f, address_line_1="6 Zeta St",
            rentvine_id=6001,
        )
        Tag.objects.create(box="A", number=30, unit=cls.unit_f)
        Tag.objects.create(box="A", number=31, unit=cls.unit_f)
        AccessNote.objects.create(
            unit=cls.unit_f, property=p_f,
            source="keybot", text="lockbox 7777",
        )

        # (g) Orphan tag (no unit, no property)
        Tag.objects.create(
            box="B", number=50, imported_address_raw="OLD ADDRESS",
        )

        # (h) Occupied, no key (has code)
        p_h1 = Property.objects.create(
            name="7 Eta St", address_line_1="7 Eta St",
            rentvine_id=700,
        )
        cls.unit_h1 = Unit.objects.create(
            property=p_h1, address_line_1="7 Eta St",
            rentvine_id=7001, rentengine_status="Leased",
        )
        AccessNote.objects.create(
            unit=cls.unit_h1, property=p_h1,
            source="keybot", text="lockbox 1111",
        )

        # (h) Occupied, no code (has key)
        p_h2 = Property.objects.create(
            name="8 Theta St", address_line_1="8 Theta St",
            rentvine_id=800,
        )
        cls.unit_h2 = Unit.objects.create(
            property=p_h2, address_line_1="8 Theta St",
            rentvine_id=8001, rentengine_status="Leased",
        )
        Tag.objects.create(box="A", number=40, unit=cls.unit_h2)

        # (i) Unknown (not in RentEngine), no key, no code
        p_i = Property.objects.create(
            name="9 Iota St", address_line_1="9 Iota St",
            rentvine_id=900,
        )
        cls.unit_i = Unit.objects.create(
            property=p_i, address_line_1="9 Iota St",
            rentvine_id=9001, rentengine_status="",
        )

        # Normal unit — has key AND code, appears in NO category
        p_ok = Property.objects.create(
            name="10 Kappa St", address_line_1="10 Kappa St",
            rentvine_id=1100,
        )
        cls.unit_ok = Unit.objects.create(
            property=p_ok, address_line_1="10 Kappa St",
            rentvine_id=11001,
        )
        Tag.objects.create(box="B", number=60, unit=cls.unit_ok)
        AccessNote.objects.create(
            unit=cls.unit_ok, property=p_ok,
            source="keybot", text="lockbox 0000",
        )

        # (f2) Building tags to sort — 4 property-level tags (unit=NULL)
        cls.prop_bldg = Property.objects.create(
            name="11 Lambda St", address_line_1="11 Lambda St",
            rentvine_id=1200,
        )
        cls.unit_bldg = Unit.objects.create(
            property=cls.prop_bldg, address_line_1="11 Lambda St",
            unit_number="1", rentvine_id=12001,
        )
        Tag.objects.create(box="B", number=70, property=cls.prop_bldg)
        Tag.objects.create(box="B", number=71, property=cls.prop_bldg)
        Tag.objects.create(box="B", number=72, property=cls.prop_bldg)
        Tag.objects.create(box="B", number=73, property=cls.prop_bldg)
        AccessNote.objects.create(
            unit=cls.unit_bldg, property=cls.prop_bldg,
            source="keybot", text="lockbox 8888",
        )


# ── Selector tests ─────────────────────────────────────────────────


class KeyHealthSelectorTests(_KeyHealthFixture):

    def test_vacant_no_key_no_code(self):
        h = key_health()
        pks = {i["unit_id"] for i in h["vacant_no_key_no_code"]}
        self.assertEqual(pks, {self.unit_a.pk})

    def test_vacant_no_key_excludes_a(self):
        """Category (b) must not include units already in (a)."""
        h = key_health()
        pks = {i["unit_id"] for i in h["vacant_no_key"]}
        self.assertEqual(pks, {self.unit_b.pk})
        self.assertNotIn(self.unit_a.pk, pks)

    def test_vacant_no_code_excludes_a(self):
        """Category (c) must not include units already in (a)."""
        h = key_health()
        pks = {i["unit_id"] for i in h["vacant_no_code"]}
        self.assertEqual(pks, {self.unit_c.pk})
        self.assertNotIn(self.unit_a.pk, pks)

    def test_overdue_checkouts(self):
        h = key_health()
        cos = h["overdue_checkouts"]
        self.assertEqual(len(cos), 2)
        by_key = {c["key_id"]: c for c in cos}
        self.assertFalse(by_key["A-010"]["is_72h"])
        self.assertTrue(by_key["A-011"]["is_72h"])

    def test_overdue_checkouts_include_address(self):
        h = key_health()
        by_key = {c["key_id"]: c for c in h["overdue_checkouts"]}
        self.assertEqual(by_key["A-010"]["address"], "4 Delta St")

    def test_missing_tags(self):
        h = key_health()
        self.assertEqual(len(h["missing_tags"]), 1)
        self.assertEqual(h["missing_tags"][0]["key_id"], "A-020")
        self.assertEqual(h["missing_tags"][0]["address"], "5 Epsilon St")

    def test_duplicate_tags(self):
        h = key_health()
        addrs = {d["address"] for d in h["duplicate_tags"]}
        # unit_d has 2 checked-out tags, unit_f has 2 on-hook tags
        self.assertIn("6 Zeta St", addrs)
        self.assertIn("4 Delta St", addrs)
        self.assertEqual(len(h["duplicate_tags"]), 2)

    def test_duplicate_tags_are_unit_level_only(self):
        """duplicate_tags must NOT contain property-level entries."""
        h = key_health()
        for d in h["duplicate_tags"]:
            self.assertIn("unit_id", d)
            self.assertNotIn("property_id", d)

    def test_building_tags(self):
        """Property with 4 property-level tags appears in building_tags."""
        h = key_health()
        self.assertEqual(len(h["building_tags"]), 1)
        entry = h["building_tags"][0]
        self.assertEqual(entry["address"], "11 Lambda St")
        self.assertEqual(entry["property_id"], self.prop_bldg.pk)
        self.assertEqual(len(entry["key_ids"]), 4)

    def test_building_tags_not_in_duplicate_tags(self):
        """building_tags entries must not appear in duplicate_tags."""
        h = key_health()
        dup_addrs = {d["address"] for d in h["duplicate_tags"]}
        bldg_addrs = {d["address"] for d in h["building_tags"]}
        self.assertFalse(dup_addrs & bldg_addrs)

    def test_duplicate_key_ids_sorted(self):
        h = key_health()
        for d in h["duplicate_tags"]:
            self.assertEqual(d["key_ids"], sorted(d["key_ids"]))

    def test_orphan_tags(self):
        h = key_health()
        self.assertEqual(len(h["orphan_tags"]), 1)
        self.assertEqual(h["orphan_tags"][0]["key_id"], "B-050")
        self.assertEqual(h["orphan_tags"][0]["imported_address"], "OLD ADDRESS")

    def test_occupied_no_key(self):
        h = key_health()
        pks = {i["unit_id"] for i in h["occupied_no_key"]}
        self.assertEqual(pks, {self.unit_h1.pk})

    def test_occupied_no_code(self):
        h = key_health()
        pks = {i["unit_id"] for i in h["occupied_no_code"]}
        self.assertEqual(pks, {self.unit_h2.pk})

    def test_unknown_counts(self):
        h = key_health()
        self.assertEqual(h["unknown_no_key_count"], 1)
        self.assertEqual(h["unknown_no_code_count"], 1)

    def test_normal_unit_not_in_any_category(self):
        h = key_health()
        all_unit_ids = set()
        for key in [
            "vacant_no_key_no_code", "vacant_no_key", "vacant_no_code",
            "occupied_no_key", "occupied_no_code",
        ]:
            all_unit_ids.update(i["unit_id"] for i in h[key])
        self.assertNotIn(self.unit_ok.pk, all_unit_ids)


# ── Tile filter tests ──────────────────────────────────────────────


class TileFilterTests(_KeyHealthFixture):
    """Each tile filter returns exactly the rows in that key_health category."""

    def setUp(self):
        from keys.dashboard_views import _build_rows

        self.health = key_health()
        self.rows = _build_rows()

    def _ids(self, rows):
        return {r["unit_id"] for r in rows}

    def _health_ids(self, key):
        return {i["unit_id"] for i in self.health[key]}

    def _filter_rows(self, f):
        """Apply tile-filter logic (mirrors the view)."""
        _unit_ids = {
            "vacant_no_key_no_code": self._health_ids("vacant_no_key_no_code"),
            "vacant_no_key": self._health_ids("vacant_no_key"),
            "vacant_no_code": self._health_ids("vacant_no_code"),
            "occupied_no_key": self._health_ids("occupied_no_key"),
            "occupied_no_code": self._health_ids("occupied_no_code"),
            "duplicates": {d["unit_id"] for d in self.health["duplicate_tags"]},
        }
        _prop_ids = {
            "building_tags": {d["property_id"] for d in self.health["building_tags"]},
        }
        if f in _unit_ids:
            return [r for r in self.rows if r["unit_id"] in _unit_ids[f]]
        if f in _prop_ids:
            return [r for r in self.rows if r["property_id"] in _prop_ids[f]]
        if f == "overdue":
            return [r for r in self.rows if "Overdue checkout" in r["flags"]]
        if f == "missing":
            return [r for r in self.rows if "Missing tag" in r["flags"]]
        if f == "with_key":
            return [r for r in self.rows if r["has_key"]]
        if f == "with_code":
            return [r for r in self.rows if r["has_code"]]
        if f == "with_both":
            return [r for r in self.rows if r["has_key"] and r["has_code"]]
        return self.rows

    def test_vacant_no_key_no_code(self):
        got = self._ids(self._filter_rows("vacant_no_key_no_code"))
        self.assertEqual(got, self._health_ids("vacant_no_key_no_code"))

    def test_vacant_no_key(self):
        got = self._ids(self._filter_rows("vacant_no_key"))
        self.assertEqual(got, self._health_ids("vacant_no_key"))

    def test_vacant_no_code(self):
        got = self._ids(self._filter_rows("vacant_no_code"))
        self.assertEqual(got, self._health_ids("vacant_no_code"))

    def test_overdue(self):
        got = self._filter_rows("overdue")
        # unit_d has 2 overdue checkouts
        self.assertEqual(self._ids(got), {self.unit_d.pk})

    def test_missing(self):
        got = self._filter_rows("missing")
        self.assertEqual(self._ids(got), {self.unit_e.pk})

    def test_duplicates(self):
        got = self._ids(self._filter_rows("duplicates"))
        expected = {d["unit_id"] for d in self.health["duplicate_tags"]}
        self.assertEqual(got, expected)

    def test_building_tags(self):
        got = self._filter_rows("building_tags")
        self.assertEqual(self._ids(got), {self.unit_bldg.pk})

    def test_with_key(self):
        got = self._filter_rows("with_key")
        self.assertTrue(all(r["has_key"] for r in got))
        self.assertEqual(len(got), self.health["with_key_count"])

    def test_with_code(self):
        got = self._filter_rows("with_code")
        self.assertTrue(all(r["has_code"] for r in got))
        self.assertEqual(len(got), self.health["with_code_count"])

    def test_with_both(self):
        got = self._filter_rows("with_both")
        self.assertTrue(all(r["has_key"] and r["has_code"] for r in got))
        self.assertEqual(len(got), self.health["with_both_count"])

    def test_occupied_no_key(self):
        got = self._ids(self._filter_rows("occupied_no_key"))
        self.assertEqual(got, self._health_ids("occupied_no_key"))

    def test_occupied_no_code(self):
        got = self._ids(self._filter_rows("occupied_no_code"))
        self.assertEqual(got, self._health_ids("occupied_no_code"))


# ── Formatter tests ────────────────────────────────────────────────


class FormatKeyHealthTests(TestCase):

    def test_no_urgent_issues(self):
        text, blocks = format_key_health(_empty_health())
        self.assertIn("No urgent key issues", text)
        _validate_blocks(self, blocks)

    def test_all_sections_present(self):
        health = _empty_health()
        health["duplicate_tags"] = [
            {"address": "10 Test St", "key_ids": ["A-001", "A-002"]},
        ]
        health["orphan_tags"] = [
            {"key_id": "A-099", "imported_address": "old"},
        ]
        health["unknown_no_key_count"] = 5
        health["unknown_no_code_count"] = 3
        text, blocks = format_key_health(health)
        _validate_blocks(self, blocks)
        self.assertIn("URGENT", text)
        self.assertIn("CLEANUP", text)
        self.assertIn("COUNTS ONLY", text)

    def test_cleanup_caps_at_10(self):
        health = _empty_health()
        health["orphan_tags"] = [
            {"key_id": f"A-{i:03d}", "imported_address": f"addr {i}"}
            for i in range(1, 25)
        ]
        text, blocks = format_key_health(health)
        self.assertIn("+14 more", text)
        # Only first 10 listed
        self.assertIn("A-010", text)
        self.assertNotIn("A-011", text)
        _validate_blocks(self, blocks)

    def test_occupied_caps_at_10(self):
        health = _empty_health()
        health["occupied_no_key"] = [
            {"address": f"{i} Leased St", "unit_id": i}
            for i in range(1, 16)
        ]
        text, blocks = format_key_health(health)
        self.assertIn("+5 more", text)
        _validate_blocks(self, blocks)

    def test_section_text_under_3000(self):
        """Even with many urgent items, sections stay under Slack's limit."""
        health = _empty_health()
        health["overdue_checkouts"] = [
            {
                "key_id": f"A-{i:03d}",
                "address": f"{i} Very Long Street Name Avenue",
                "checked_out_by_name": "SomeName",
                "checked_out_by_slack_id": "U_X",
                "hours": 48,
                "is_72h": False,
            }
            for i in range(1, 80)
        ]
        text, blocks = format_key_health(health)
        _validate_blocks(self, blocks)

    def test_text_is_full_report(self):
        """text delivers real content, not just a title."""
        health = _empty_health()
        health["duplicate_tags"] = [
            {"address": "10 Test St", "key_ids": ["A-001", "A-002"]},
        ]
        text, blocks = format_key_health(health)
        self.assertIn("10 Test St", text)
        self.assertIn("A-001", text)
        self.assertIn("duplicate", text.lower())

    def test_returns_tuple(self):
        text, blocks = format_key_health(_empty_health())
        self.assertIsInstance(text, str)
        self.assertIsInstance(blocks, list)

    def test_building_tags_section(self):
        """building_tags appear as a separate section in the Slack report."""
        health = _empty_health()
        health["building_tags"] = [
            {"address": "50 Main St", "key_ids": ["A-010", "A-011", "A-012"]},
        ]
        text, blocks = format_key_health(health)
        self.assertIn("building tags to sort", text.lower())
        self.assertIn("50 Main St", text)
        _validate_blocks(self, blocks)

    def test_building_tags_separate_from_duplicates(self):
        """Both sections render independently in the same report."""
        health = _empty_health()
        health["duplicate_tags"] = [
            {"address": "10 Unit St", "key_ids": ["A-001", "A-002"]},
        ]
        health["building_tags"] = [
            {"address": "20 Bldg St", "key_ids": ["B-001", "B-002"]},
        ]
        text, blocks = format_key_health(health)
        self.assertIn("duplicate tags", text.lower())
        self.assertIn("building tags to sort", text.lower())
        self.assertIn("10 Unit St", text)
        self.assertIn("20 Bldg St", text)
        _validate_blocks(self, blocks)

    def test_72h_marker(self):
        health = _empty_health()
        health["overdue_checkouts"] = [
            {
                "key_id": "A-005",
                "address": "99 Elm St",
                "checked_out_by_name": "Alice",
                "checked_out_by_slack_id": "U_A",
                "hours": 80,
                "is_72h": True,
            },
        ]
        text, blocks = format_key_health(health)
        self.assertIn("\u26a0\ufe0f", text)
        _validate_blocks(self, blocks)


# ── parse_query tests ──────────────────────────────────────────────


class ParseQueryHealthTests(TestCase):

    def test_health(self):
        self.assertEqual(parse_query("health")["type"], "key_health")

    def test_key_health(self):
        self.assertEqual(parse_query("key health")["type"], "key_health")

    def test_missing(self):
        self.assertEqual(parse_query("missing")["type"], "key_health")

    def test_whats_missing(self):
        self.assertEqual(parse_query("whats missing")["type"], "key_health")

    def test_whats_missing_apostrophe(self):
        self.assertEqual(parse_query("what's missing")["type"], "key_health")

    def test_whats_missing_curly_quote(self):
        self.assertEqual(
            parse_query("what\u2019s missing")["type"], "key_health",
        )

    def test_what_is_missing(self):
        self.assertEqual(parse_query("what is missing")["type"], "key_health")

    def test_trailing_punctuation(self):
        self.assertEqual(parse_query("health?")["type"], "key_health")
        self.assertEqual(parse_query("missing!")["type"], "key_health")
        self.assertEqual(parse_query("health.")["type"], "key_health")

    def test_address_with_missing_is_address(self):
        """'28 bass missing key' must NOT match key_health."""
        result = parse_query("28 bass missing key")
        self.assertEqual(result["type"], "address")

    def test_address_with_health_word_is_address(self):
        result = parse_query("123 Health Ave")
        self.assertEqual(result["type"], "address")


# ── Cron tests ─────────────────────────────────────────────────────

_CRON = "keys.management.commands.keys_cron"
_FAKE_BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "r"}}]


@override_settings(KEYBOT_CHANNEL_ID="C_TEST")
class KeysCronHealthTests(TestCase):
    """Monday health post and daily snapshot in keys_cron."""

    def _run_cron(self, fake_now):
        from keys.management.commands.keys_cron import Command

        with (
            patch(f"{_CRON}.datetime") as mock_dt,
            patch(f"{_CRON}.call_command"),
            patch(f"{_CRON}.post_message") as mock_post,
            patch("keys.selectors.key_health", return_value=_empty_health()),
            patch(
                "keys.replies.format_key_health",
                return_value=("report", _FAKE_BLOCKS),
            ),
        ):
            mock_dt.now.return_value = fake_now
            cmd = Command()
            cmd.stdout = StringIO()
            try:
                cmd.handle()
            except SystemExit:
                pass
            return mock_post

    def test_monday_9am_posts_health(self):
        monday_9am = datetime(2026, 11, 23, 9, 0, tzinfo=ET)
        mock_post = self._run_cron(monday_9am)
        health_calls = [
            c for c in mock_post.call_args_list
            if c.kwargs.get("blocks") is not None
        ]
        self.assertEqual(len(health_calls), 1)

    def test_tuesday_9am_no_health_post(self):
        tuesday_9am = datetime(2026, 11, 24, 9, 0, tzinfo=ET)
        mock_post = self._run_cron(tuesday_9am)
        health_calls = [
            c for c in mock_post.call_args_list
            if c.kwargs.get("blocks") is not None
        ]
        self.assertEqual(len(health_calls), 0)

    def test_monday_3pm_no_health_post(self):
        monday_3pm = datetime(2026, 11, 23, 15, 0, tzinfo=ET)
        mock_post = self._run_cron(monday_3pm)
        health_calls = [
            c for c in mock_post.call_args_list
            if c.kwargs.get("blocks") is not None
        ]
        self.assertEqual(len(health_calls), 0)

    def test_6am_creates_snapshot(self):
        day_6am = datetime(2026, 11, 22, 6, 0, tzinfo=ET)
        self._run_cron(day_6am)
        self.assertTrue(
            KeyHealthSnapshot.objects.filter(date=day_6am.date()).exists(),
        )

    def test_snapshot_idempotent(self):
        """Running twice on the same date updates, doesn't duplicate."""
        day_6am = datetime(2026, 11, 22, 6, 0, tzinfo=ET)
        self._run_cron(day_6am)
        self._run_cron(day_6am)
        self.assertEqual(
            KeyHealthSnapshot.objects.filter(date=day_6am.date()).count(), 1,
        )

    def test_9pm_no_snapshot(self):
        day_9pm = datetime(2026, 11, 22, 21, 0, tzinfo=ET)
        self._run_cron(day_9pm)
        self.assertFalse(KeyHealthSnapshot.objects.exists())

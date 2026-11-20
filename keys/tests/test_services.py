"""
Unit tests for keys.services — pure functions, no DB, no network.
"""

import os
import tempfile
import textwrap

import pytest

from keys.services import (
    build_review_rows,
    build_unit_address_index,
    compute_flags,
    extract_key_notes,
    match_tag_to_unit,
    normalize_address,
    normalize_unit_token,
    parse_key_box_csv,
)


# ---------------------------------------------------------------------------
# Stand-in objects (no Django ORM)
# ---------------------------------------------------------------------------

class FakeProperty:
    def __init__(self, rentvine_id, address_line_1=""):
        self.rentvine_id = rentvine_id
        self.address_line_1 = address_line_1


class FakeUnit:
    def __init__(self, rentvine_id, property, unit_number="",
                 address_line_1="", rentengine_id=None):
        self.rentvine_id = rentvine_id
        self.property = property
        self.unit_number = unit_number
        self.address_line_1 = address_line_1 or property.address_line_1
        self.rentengine_id = rentengine_id


# ---------------------------------------------------------------------------
# normalize_address
# ---------------------------------------------------------------------------

class TestNormalizeAddress:
    def test_bass_lane_unit_1(self):
        assert normalize_address(
            "28 Bass Lane Unit 1 Hendersonville, NC 28791"
        ) == ("28", "bass", "1")

    def test_weaverville_highway_lot_1(self):
        assert normalize_address(
            "245 Weaverville Highway Lot 1 Woodfin, NC 28804"
        ) == ("245", "weaverville", "1")

    def test_clyde_st_unit_b(self):
        assert normalize_address(
            "17 Clyde St unit B"
        ) == ("17", "clyde", "b")

    def test_buffalo_avl(self):
        assert normalize_address("52 Buffalo Avl") == ("52", "buffalo", None)

    def test_buffalo_street_matches_buffalo_avl(self):
        """Rentvine's 'Street' and sheet's 'Avl' both reduce to base 'buffalo'."""
        assert normalize_address("52 Buffalo Street")[1] == "buffalo"

    def test_pinehill_right(self):
        assert normalize_address(
            "118 Pinehill (right)"
        ) == ("118", "pinehill", "right")

    def test_pine_hill_dr_matches_pinehill(self):
        num, base, token = normalize_address("118 Pine Hill Dr.")
        assert (num, base) == ("118", "pinehill")
        assert token is None

    def test_lasalle_hendersonville(self):
        assert normalize_address(
            "12 LaSalle Hendersonville"
        ) == ("12", "lasalle", None)

    def test_la_salle_lane_matches_lasalle(self):
        assert normalize_address("12 La Salle Lane")[:2] == ("12", "lasalle")

    def test_third_street_ordinal(self):
        assert normalize_address(
            "2 3rd Street - B Woodfin, NC 28804"
        ) == ("2", "third", "b")

    def test_third_street_word_matches_ordinal(self):
        assert normalize_address("2 Third Street")[:2] == ("2", "third")

    def test_n_main_directional(self):
        assert normalize_address(
            "241 N Main Street"
        ) == ("241", "northmain", None)

    def test_north_main_matches_n_main(self):
        assert normalize_address(
            "241 North Main Street"
        ) == ("241", "northmain", None)

    def test_dogwood_ct_a(self):
        assert normalize_address(
            "6 Dogwood Ct. A"
        ) == ("6", "dogwood", "a")

    def test_ridgeview_ln_mars_hill(self):
        assert normalize_address(
            "240 Ridgeview Ln. Mars Hill"
        ) == ("240", "ridgeview", None)

    def test_empty_string(self):
        assert normalize_address("") == ("", "", None)

    def test_none(self):
        assert normalize_address(None) == ("", "", None)

    def test_smart_blanks_no_crash(self):
        """SMART BLANKS has no street number — returns empty base."""
        num, base, _ = normalize_address("SMART BLANKS")
        assert num == ""


# ---------------------------------------------------------------------------
# normalize_unit_token
# ---------------------------------------------------------------------------

class TestNormalizeUnitToken:
    def test_unit_prefix(self):
        assert normalize_unit_token("Unit 2") == "2"

    def test_hash_prefix(self):
        assert normalize_unit_token("#2") == "2"

    def test_bare_number(self):
        assert normalize_unit_token("2") == "2"

    def test_apt_prefix(self):
        assert normalize_unit_token("Apt A") == "a"

    def test_bare_letter(self):
        assert normalize_unit_token("A") == "a"

    def test_empty(self):
        assert normalize_unit_token("") == ""

    def test_none(self):
        assert normalize_unit_token(None) == ""

    def test_parens_stripped(self):
        assert normalize_unit_token("(Right)") == "right"


# ---------------------------------------------------------------------------
# parse_key_box_csv
# ---------------------------------------------------------------------------

class TestParseKeyBoxCsv:
    def _write_csv(self, lines):
        """Write lines to a temp CSV, return path."""
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            fh.write(textwrap.dedent(lines))
        return path

    def test_header_after_blank_row(self):
        path = self._write_csv("""\
            ,,,
            Tag Number,Address,Notes,Key With
            7,123 Main St,some note,Bill
        """)
        rows = parse_key_box_csv(path, "A")
        assert len(rows) == 1
        assert rows[0]["key_id"] == "A-007"
        assert rows[0]["address"] == "123 Main St"
        assert rows[0]["notes"] == "some note"
        assert rows[0]["key_with"] == "Bill"
        assert rows[0]["row_type"] == "property"
        os.unlink(path)

    def test_smart_blanks_non_property(self):
        path = self._write_csv("""\
            Tag Number,Address,Notes,Key With
            10,SMART BLANKS,,
        """)
        rows = parse_key_box_csv(path, "B")
        assert rows[0]["row_type"] == "non_property"
        assert rows[0]["key_id"] == "B-010"
        os.unlink(path)

    def test_empty_address_skipped(self):
        path = self._write_csv("""\
            Tag Number,Address,Notes,Key With
            1,123 Main St,,
            2,,,
            3,456 Oak Ave,,
        """)
        rows = parse_key_box_csv(path, "A")
        assert len(rows) == 2
        assert rows[0]["key_id"] == "A-001"
        assert rows[1]["key_id"] == "A-003"
        os.unlink(path)

    def test_no_header_returns_empty(self):
        path = self._write_csv("""\
            Foo,Bar,Baz
            1,123 Main St,note
        """)
        rows = parse_key_box_csv(path, "A")
        assert rows == []
        os.unlink(path)


# ---------------------------------------------------------------------------
# extract_key_notes
# ---------------------------------------------------------------------------

class TestExtractKeyNotes:
    def test_lockbox_match(self):
        notes = [
            {
                "noteID": 1,
                "note": "<p>Lockbox 1776 on front door</p>",
                "datePosted": "2026-03-15T10:00:00",
                "isPinned": False,
            },
        ]
        result = extract_key_notes(notes)
        assert len(result) == 1
        assert result[0]["note_id"] == 1
        assert result[0]["date"] == "2026-03-15"
        assert "Lockbox 1776" in result[0]["text"]

    def test_key_match(self):
        notes = [
            {
                "noteID": 2,
                "note": "New key cut for unit B",
                "datePosted": "2026-01-10T08:00:00",
                "isPinned": False,
            },
        ]
        assert len(extract_key_notes(notes)) == 1

    def test_no_match_on_unrelated(self):
        notes = [
            {
                "noteID": 3,
                "note": "Tenant moved furniture into garage",
                "datePosted": "2026-02-01",
                "isPinned": False,
            },
        ]
        assert extract_key_notes(notes) == []

    def test_code_matches_in_zip_code(self):
        r"""Document current behavior: \b code \b matches 'zip code'."""
        notes = [
            {
                "noteID": 4,
                "note": "Mailing address zip code 28806",
                "datePosted": "2026-04-01",
                "isPinned": False,
            },
        ]
        result = extract_key_notes(notes)
        assert len(result) == 1  # word-boundary 'code' matches

    def test_html_stripped(self):
        notes = [
            {
                "noteID": 5,
                "note": "<b>Lockbox</b> combo is <i>1234</i>",
                "datePosted": "2026-05-20",
                "isPinned": False,
            },
        ]
        result = extract_key_notes(notes)
        assert "<b>" not in result[0]["text"]
        assert "Lockbox" in result[0]["text"]

    def test_empty_note_skipped(self):
        notes = [
            {"noteID": 6, "note": "", "datePosted": "2026-06-01", "isPinned": False},
        ]
        assert extract_key_notes(notes) == []


# ---------------------------------------------------------------------------
# match_tag_to_unit
# ---------------------------------------------------------------------------

class TestMatchTagToUnit:
    """Uses the 28 Bass Lane shape: 3 units at the same street address."""

    @pytest.fixture()
    def bass_property(self):
        return FakeProperty(rentvine_id=100, address_line_1="28 Bass Ln")

    @pytest.fixture()
    def bass_units(self, bass_property):
        return [
            FakeUnit(rentvine_id=201, property=bass_property,
                     unit_number="1", address_line_1="28 Bass Ln"),
            FakeUnit(rentvine_id=202, property=bass_property,
                     unit_number="2", address_line_1="28 Bass Ln"),
            FakeUnit(rentvine_id=203, property=bass_property,
                     unit_number="3", address_line_1="28 Bass Ln"),
        ]

    @pytest.fixture()
    def bass_index(self, bass_units):
        return build_unit_address_index(bass_units)

    def test_unit_level_match(self, bass_index):
        tag = {"address": "28 Bass Lane Unit 2 Hendersonville, NC 28791",
               "row_type": "property"}
        unit, prop, level = match_tag_to_unit(tag, bass_index)
        assert level == "unit"
        assert unit.rentvine_id == 202

    def test_property_level_when_no_token(self, bass_index):
        tag = {"address": "28 Bass Ln Hendersonville, NC",
               "row_type": "property"}
        unit, prop, level = match_tag_to_unit(tag, bass_index)
        assert level == "property"
        assert unit is None
        assert prop.rentvine_id == 100

    def test_no_match_wrong_number(self, bass_index):
        tag = {"address": "99 Bass Lane Unit 1", "row_type": "property"}
        unit, prop, level = match_tag_to_unit(tag, bass_index)
        assert level == "none"

    def test_single_unit_property(self):
        prop = FakeProperty(rentvine_id=300, address_line_1="52 Buffalo St")
        units = [FakeUnit(rentvine_id=301, property=prop,
                          address_line_1="52 Buffalo St")]
        idx = build_unit_address_index(units)
        tag = {"address": "52 Buffalo Avl", "row_type": "property"}
        unit, _, level = match_tag_to_unit(tag, idx)
        assert level == "unit"
        assert unit.rentvine_id == 301


# ---------------------------------------------------------------------------
# build_review_rows — 28 Bass shape
# ---------------------------------------------------------------------------

def _make_tag(key_id, address, notes="", key_with=""):
    return {
        "key_id": key_id,
        "tag_number": int(key_id.split("-")[1]),
        "address": address,
        "notes": notes,
        "key_with": key_with,
        "row_type": "property",
        "box_letter": key_id[0],
    }


class TestBuildReviewRows:
    """
    28 Bass Lane: 3 units.
    Unit 1 gets tags A-038 & A-134 (MULTI_TAG).
    Unit 2 gets tags A-039, A-100, A-128 (MULTI_TAG).
    Unit 3 gets no tag (NO_TAG).
    """

    @pytest.fixture()
    def bass_setup(self):
        prop = FakeProperty(rentvine_id=100, address_line_1="28 Bass Ln")
        units = [
            FakeUnit(rentvine_id=201, property=prop,
                     unit_number="1", address_line_1="28 Bass Ln"),
            FakeUnit(rentvine_id=202, property=prop,
                     unit_number="2", address_line_1="28 Bass Ln"),
            FakeUnit(rentvine_id=203, property=prop,
                     unit_number="3", address_line_1="28 Bass Ln"),
        ]
        tags = [
            _make_tag("A-038", "28 Bass Lane Unit 1 Hendersonville, NC 28791"),
            _make_tag("A-134", "28 Bass Ln #1"),
            _make_tag("A-039", "28 Bass Lane Unit 2 Hendersonville, NC 28791"),
            _make_tag("A-100", "28 Bass Ln Unit 2"),
            _make_tag("A-128", "28 Bass Lane Unit 2"),
        ]
        rows, stats = build_review_rows(units, tags, {}, {})
        by_uid = {r["rentvine_unit_id"]: r for r in rows
                  if r["row_type"] == "unit"}
        return rows, stats, by_uid

    def test_unit1_multi_tag(self, bass_setup):
        _, _, by_uid = bass_setup
        row = by_uid[201]
        assert "MULTI_TAG" in row["flags"]
        assert "A-038" in row["sheet_key_ids"]
        assert "A-134" in row["sheet_key_ids"]

    def test_unit2_multi_tag(self, bass_setup):
        _, _, by_uid = bass_setup
        row = by_uid[202]
        assert "A-039" in row["sheet_key_ids"]
        assert "A-100" in row["sheet_key_ids"]
        assert "A-128" in row["sheet_key_ids"]
        assert "MULTI_TAG" in row["flags"]

    def test_unit3_no_tag(self, bass_setup):
        _, _, by_uid = bass_setup
        row = by_uid[203]
        assert "NO_TAG" in row["flags"]
        assert row["sheet_key_ids"] == ""

    def test_stats(self, bass_setup):
        _, stats, _ = bass_setup
        assert stats["units"] == 3
        assert stats["tagged"] == 2
        assert stats["untagged"] == 1
        assert stats["multi_tag"] == 2


class TestSingleCleanTag:
    """Single tag on a single-unit property — clean match."""

    def test_single_tag_match(self):
        prop = FakeProperty(rentvine_id=400, address_line_1="52 Buffalo St")
        units = [FakeUnit(rentvine_id=401, property=prop,
                          address_line_1="52 Buffalo St")]
        tags = [_make_tag("A-050", "52 Buffalo Avl")]
        rows, stats = build_review_rows(units, tags, {}, {})
        row = rows[0]
        assert row["sheet_key_ids"] == "A-050"
        assert row["match_level"] == "unit"
        assert row["flags"] == "NO_ACCESS_INFO"
        assert stats["tagged"] == 1


# ---------------------------------------------------------------------------
# Property-level columns
# ---------------------------------------------------------------------------

class TestPropertyLevelColumns:
    def test_property_level_ids_separate_from_sheet(self):
        prop = FakeProperty(rentvine_id=800, address_line_1="20 Pine Ln")
        u1 = FakeUnit(rentvine_id=801, property=prop,
                      unit_number="A", address_line_1="20 Pine Ln")
        u2 = FakeUnit(rentvine_id=802, property=prop,
                      unit_number="B", address_line_1="20 Pine Ln")
        tags = [
            _make_tag("A-090", "20 Pine Ln"),  # property-level (no token)
        ]
        rows, _ = build_review_rows([u1, u2], tags, {}, {})
        by_uid = {r["rentvine_unit_id"]: r for r in rows
                  if r["row_type"] == "unit"}
        # Both units see the property-level tag.
        assert by_uid[801]["property_level_key_ids"] == "A-090"
        assert by_uid[802]["property_level_key_ids"] == "A-090"
        # Neither has it in sheet_key_ids.
        assert by_uid[801]["sheet_key_ids"] == ""
        assert by_uid[802]["sheet_key_ids"] == ""
        # Both flagged PROPERTY_LEVEL_MATCH_ONLY (no unit-level tag).
        assert "PROPERTY_LEVEL_MATCH_ONLY" in by_uid[801]["flags"]
        assert "PROPERTY_LEVEL_MATCH_ONLY" in by_uid[802]["flags"]


# ---------------------------------------------------------------------------
# Unmatched tags
# ---------------------------------------------------------------------------

class TestUnmatchedTags:
    def test_unmatched_row(self):
        prop = FakeProperty(rentvine_id=900, address_line_1="1 Maple Ave")
        units = [FakeUnit(rentvine_id=901, property=prop,
                          address_line_1="1 Maple Ave")]
        tags = [_make_tag("B-005", "999 Nowhere Blvd")]
        rows, stats = build_review_rows(units, tags, {}, {})
        unmatched = [r for r in rows if r["row_type"] == "unmatched_tag"]
        assert len(unmatched) == 1
        assert "UNMATCHED_TAG" in unmatched[0]["flags"]
        assert stats["unmatched_tags"] == 1

    def test_property_filter_limits_unmatched(self):
        prop = FakeProperty(rentvine_id=900, address_line_1="1 Maple Ave")
        units = [FakeUnit(rentvine_id=901, property=prop,
                          address_line_1="1 Maple Ave")]
        tags = [
            _make_tag("B-005", "999 Nowhere Blvd"),
            _make_tag("B-006", "77 Maple Ct"),
        ]
        rows, stats = build_review_rows(
            units, tags, {}, {}, property_filter="Maple",
        )
        unmatched = [r for r in rows if r["row_type"] == "unmatched_tag"]
        # "999 Nowhere Blvd" filtered out; "77 Maple Ct" kept.
        assert len(unmatched) == 1
        assert unmatched[0]["sheet_raw_addresses"] == "77 Maple Ct"


# ---------------------------------------------------------------------------
# RentEngine fields
# ---------------------------------------------------------------------------

class TestRentEngineFields:
    def test_re_fields_read_correctly(self):
        prop = FakeProperty(rentvine_id=1000, address_line_1="3 Cedar Way")
        units = [FakeUnit(rentvine_id=1001, property=prop,
                          address_line_1="3 Cedar Way",
                          rentengine_id=5555)]
        re_index = {
            "5555": {
                "id": 5555,
                "key_access": "Lockbox",
                "showing_method": "Self-guided",
                "structured_access_instructions": {
                    "internal_notes": "Code is 1776",
                },
            },
        }
        tags = [_make_tag("A-010", "3 Cedar Way")]
        rows, _ = build_review_rows(units, tags, re_index, {})
        row = rows[0]
        assert row["rentengine_key_access"] == "Lockbox"
        assert row["rentengine_showing_method"] == "Self-guided"
        assert row["rentengine_internal_notes"] == "Code is 1776"


# ---------------------------------------------------------------------------
# fetch_errors
# ---------------------------------------------------------------------------

class TestFetchErrors:
    def test_errors_in_column_and_stats(self):
        prop = FakeProperty(rentvine_id=1100, address_line_1="7 Ash Pl")
        units = [FakeUnit(rentvine_id=1101, property=prop,
                          address_line_1="7 Ash Pl")]
        errors = {1101: ["unit notes (rv 1101): 500", "RE fetch: timeout"]}
        rows, stats = build_review_rows(units, [], {}, {},
                                        fetch_errors=errors)
        assert rows[0]["errors"] == "unit notes (rv 1101): 500; RE fetch: timeout"
        assert stats["errors"] == 1

    def test_no_errors_empty_string(self):
        prop = FakeProperty(rentvine_id=1200, address_line_1="9 Fir Ct")
        units = [FakeUnit(rentvine_id=1201, property=prop,
                          address_line_1="9 Fir Ct")]
        rows, stats = build_review_rows(units, [], {}, {})
        assert rows[0]["errors"] == ""
        assert stats["errors"] == 0

"""
Pure-function tests for utilities.services.

SimpleTestCase only — no database access.
"""

from django.test import SimpleTestCase

from utilities.services import (
    build_source_lookup,
    build_user_prompt,
    check_quote,
    collapse_ws,
    compute_fingerprint,
    compute_source_text_hash,
    convert_note_html,
    detect_conflicts,
    extract_json_object,
    has_utility_words,
    note_is_sendable,
    validate_and_build_proposals,
    verify_extras_values,
)


class CollapseWsTests(SimpleTestCase):
    def test_basic(self):
        self.assertEqual(collapse_ws("  hello   world  "), "hello world")

    def test_newlines_and_tabs(self):
        self.assertEqual(collapse_ws("a\n\n\tb"), "a b")

    def test_empty(self):
        self.assertEqual(collapse_ws(""), "")

    def test_already_clean(self):
        self.assertEqual(collapse_ws("hello world"), "hello world")


class CheckQuoteTests(SimpleTestCase):
    def test_exact_match(self):
        self.assertTrue(check_quote("hello", "say hello world"))

    def test_whitespace_collapsed(self):
        self.assertTrue(check_quote("hello  world", "say hello world today"))

    def test_not_found(self):
        self.assertFalse(check_quote("goodbye", "say hello world"))

    def test_none_quote(self):
        self.assertFalse(check_quote(None, "some text"))

    def test_empty_quote(self):
        self.assertFalse(check_quote("", "some text"))


class NoteIsSendableTests(SimpleTestCase):
    def test_normal_text(self):
        self.assertTrue(note_is_sendable("Water is tenant responsibility"))

    def test_lockbox(self):
        self.assertFalse(note_is_sendable("The lockbox code is 1234"))

    def test_door_code(self):
        self.assertFalse(note_is_sendable("door code: 5678"))

    def test_pin(self):
        self.assertFalse(note_is_sendable("enter pin to access"))

    def test_alarm_code(self):
        self.assertFalse(note_is_sendable("alarm code is 9999"))

    def test_smart_lock(self):
        self.assertFalse(note_is_sendable("there is a smart lock on the front door"))


class HasUtilityWordsTests(SimpleTestCase):
    def test_water(self):
        self.assertTrue(has_utility_words("Water is included"))

    def test_electric(self):
        self.assertTrue(has_utility_words("Electric bill"))

    def test_propane(self):
        self.assertTrue(has_utility_words("Propane tank"))

    def test_no_utility_words(self):
        self.assertFalse(has_utility_words("Lawn care schedule"))

    def test_utility_word_boundary(self):
        # "utilit" in the regex requires a word boundary — matches only
        # standalone. Full words like "utility" don't match via "utilit\b"
        # because 'y' follows. But "utility" contains "utilit" + "y" which
        # hits the word boundary. Actually the regex matches substrings
        # within word boundaries, so test with the actual matching words.
        self.assertTrue(has_utility_words("Check the gas meter"))
        self.assertTrue(has_utility_words("The heat is electric"))


class ConvertNoteHtmlTests(SimpleTestCase):
    def test_template_format(self):
        html = "<p>UTILITY INFO</p><p>WATER: City Water - Tenant</p>"
        text, fmt, flags = convert_note_html(html)
        self.assertEqual(fmt, "template")
        self.assertIsNone(flags)
        self.assertIn("WATER: City Water - Tenant", text)

    def test_table_format(self):
        html = (
            "<table><tr><td>Utility</td><td>Landlord</td><td>Resident</td></tr>"
            "<tr><td>Water</td><td></td><td>X</td></tr></table>"
        )
        text, fmt, flags = convert_note_html(html)
        self.assertEqual(fmt, "table")
        self.assertIsNotNone(flags)
        self.assertIn("|", text)
        self.assertIn("Water", text)
        # Header + one data row with exactly one X → no unclear rows
        self.assertEqual(flags["row_unclear"], [])

    def test_table_unclear_row(self):
        html = (
            "<table><tr><td>Utility</td><td>Landlord</td><td>Resident</td></tr>"
            "<tr><td>Water</td><td>X</td><td>X</td></tr></table>"
        )
        _, _, flags = convert_note_html(html)
        # Row with 2 X's is unclear
        self.assertEqual(flags["row_unclear"], [1])

    def test_entity_decoding(self):
        html = "<p>Water &amp; Sewer</p>"
        text, _, _ = convert_note_html(html)
        self.assertIn("Water & Sewer", text)

    def test_blank_line_collapse(self):
        html = "<p>Line 1</p><p></p><p></p><p>Line 2</p>"
        text, _, _ = convert_note_html(html)
        # Should not have more than one blank line between content
        self.assertNotIn("\n\n\n", text)


class ComputeSourceTextHashTests(SimpleTestCase):
    def test_deterministic(self):
        h1 = compute_source_text_hash("hello world")
        h2 = compute_source_text_hash("hello world")
        self.assertEqual(h1, h2)

    def test_whitespace_normalized(self):
        h1 = compute_source_text_hash("hello  world")
        h2 = compute_source_text_hash("hello world")
        self.assertEqual(h1, h2)

    def test_different_text(self):
        h1 = compute_source_text_hash("hello")
        h2 = compute_source_text_hash("world")
        self.assertNotEqual(h1, h2)

    def test_length(self):
        h = compute_source_text_hash("test")
        self.assertEqual(len(h), 64)  # SHA-256 hex


class ComputeFingerprintTests(SimpleTestCase):
    def test_deterministic(self):
        fp1 = compute_fingerprint(100, "water", "note", 55, "abc123", "WATER: Tenant")
        fp2 = compute_fingerprint(100, "water", "note", 55, "abc123", "WATER: Tenant")
        self.assertEqual(fp1, fp2)

    def test_different_unit_id(self):
        fp1 = compute_fingerprint(100, "water", "note", 55, "abc123", "WATER: Tenant")
        fp2 = compute_fingerprint(200, "water", "note", 55, "abc123", "WATER: Tenant")
        self.assertNotEqual(fp1, fp2)

    def test_different_utility_type(self):
        fp1 = compute_fingerprint(100, "water", "note", 55, "abc123", "quote")
        fp2 = compute_fingerprint(100, "electric", "note", 55, "abc123", "quote")
        self.assertNotEqual(fp1, fp2)

    def test_none_note_id(self):
        fp1 = compute_fingerprint(100, "water", "listing_description", None, "abc", "quote")
        fp2 = compute_fingerprint(100, "water", "listing_description", None, "abc", "quote")
        self.assertEqual(fp1, fp2)

    def test_length(self):
        fp = compute_fingerprint(1, "water", "note", 1, "hash", "quote")
        self.assertEqual(len(fp), 64)

    def test_whitespace_in_quote_normalized(self):
        fp1 = compute_fingerprint(1, "water", "note", 1, "h", "a  b")
        fp2 = compute_fingerprint(1, "water", "note", 1, "h", "a b")
        self.assertEqual(fp1, fp2)


class VerifyExtrasValuesTests(SimpleTestCase):
    def test_all_verified(self):
        source = "The 275 gallon tank is in the basement. Delivered 2024-01-15, 150 gallons."
        extras = {
            "tank_size": "275 gallon",
            "tank_location": "basement",
            "last_delivery_date": "2024-01-15",
            "last_delivery_amount": "150 gallons",
        }
        result = verify_extras_values(extras, source)
        self.assertEqual(result["tank_size"], "275 gallon")
        self.assertEqual(result["tank_location"], "basement")

    def test_unverified_dropped(self):
        source = "Oil heat with 275 gallon tank"
        extras = {
            "tank_size": "275 gallon",
            "tank_location": "garage",  # not in source
            "last_delivery_date": None,
            "last_delivery_amount": None,
        }
        result = verify_extras_values(extras, source)
        self.assertEqual(result["tank_size"], "275 gallon")
        self.assertIsNone(result["tank_location"])  # dropped

    def test_none_extras(self):
        result = verify_extras_values(None, "some text")
        self.assertEqual(result, {
            "tank_size": None,
            "tank_location": None,
            "last_delivery_date": None,
            "last_delivery_amount": None,
        })

    def test_empty_dict(self):
        result = verify_extras_values({}, "some text")
        for key in ("tank_size", "tank_location", "last_delivery_date", "last_delivery_amount"):
            self.assertIsNone(result[key])


class DetectConflictsTests(SimpleTestCase):
    def test_no_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertFalse(result[0]["has_conflict"])

    def test_same_responsibility_no_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "listing_description",
             "source_note_id": None, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertFalse(result[0]["has_conflict"])
        self.assertFalse(result[1]["has_conflict"])

    def test_different_responsibility_is_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertTrue(result[0]["has_conflict"])
        self.assertTrue(result[1]["has_conflict"])
        self.assertIn("note 10", result[0]["conflict_note"])
        self.assertIn("note 20", result[0]["conflict_note"])

    def test_unknown_ignored_in_comparison(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "unknown", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        # Only one non-unknown value → no conflict
        self.assertFalse(result[0]["has_conflict"])
        self.assertFalse(result[1]["has_conflict"])

    def test_different_units_not_conflicting(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 2, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertFalse(result[0]["has_conflict"])
        self.assertFalse(result[1]["has_conflict"])

    def test_listing_source_description(self):
        proposals = [
            {"rentvine_unit_id": 5, "utility_type": "trash",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 5, "utility_type": "trash",
             "responsibility": "tenant", "source_type": "listing_description",
             "source_note_id": None, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertTrue(result[0]["has_conflict"])
        self.assertIn("note 10", result[0]["conflict_note"])
        self.assertIn("listing", result[1]["conflict_note"])


class BuildUserPromptTests(SimpleTestCase):
    def test_basic_structure(self):
        notes = [{
            "note_id": 100,
            "unit_id": 50,
            "level": "unit",
            "date_posted": "2024-01-15",
            "converted_text": "WATER: Tenant",
        }]
        listings = [{"unit_id": 50, "description": "Utilities not included"}]
        prompt = build_user_prompt(11, "123 Main St", notes, listings)
        self.assertIn("property 11", prompt)
        self.assertIn("123 Main St", prompt)
        self.assertIn("NOTE (id=100", prompt)
        self.assertIn("WATER: Tenant", prompt)
        self.assertIn("LISTING DESCRIPTION (unit_id=50)", prompt)
        self.assertIn("Utilities not included", prompt)

    def test_property_level_note(self):
        notes = [{
            "note_id": 200,
            "unit_id": None,
            "level": "property",
            "date_posted": "2024-06-01",
            "converted_text": "ALL UTILITIES TENANT",
        }]
        prompt = build_user_prompt(22, "456 Oak Ave", notes, [])
        self.assertIn("property-level", prompt)
        self.assertIn("ALL UTILITIES TENANT", prompt)


class BuildSourceLookupTests(SimpleTestCase):
    def test_notes_and_listings(self):
        notes = [{
            "note_id": 10,
            "unit_id": 50,
            "level": "unit",
            "date_posted": "2024-01-01",
            "converted_text": "WATER: Tenant",
            "source_text_hash": "abc",
        }]
        listings = [{"unit_id": 50, "description": "No utilities"}]
        info = build_source_lookup(notes, listings)
        self.assertIn(("note", 10), info["lookup"])
        self.assertIn(("listing", 50), info["lookup"])
        self.assertIn(10, info["valid_note_ids"])
        self.assertIn(50, info["valid_listing_unit_ids"])


class ValidateAndBuildProposalsTests(SimpleTestCase):
    """Test the proposal builder with mock model output."""

    def _make_source_info(self):
        """Build source info with one note and one listing."""
        notes = [{
            "note_id": 10,
            "unit_id": 50,
            "level": "unit",
            "date_posted": "2024-01-15",
            "converted_text": "WATER: City Water - Tenant pays",
            "source_text_hash": compute_source_text_hash("WATER: City Water - Tenant pays"),
        }]
        listings = [{
            "unit_id": 50,
            "description": "Electric is tenant responsibility",
        }]
        return build_source_lookup(notes, listings)

    def test_valid_note_row(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": "City Water",
            "evidence_quote": "WATER: City Water - Tenant pays",
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": "2024-01-15",
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["utility_type"], "water")
        self.assertEqual(proposals[0]["source_level"], "unit")
        self.assertEqual(counters["quote_failed"], 0)

    def test_invalid_note_id_dropped(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "WATER: Tenant",
            "source": "note",
            "note_id": 999,  # not in input
            "unit_id": 50,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_ref"], 1)

    def test_failed_quote_dropped(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "This quote is not in the source",
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["quote_failed"], 1)

    def test_invalid_utility_type_dropped(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "steam",  # not valid
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "WATER: City Water",
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_field"], 1)

    def test_property_level_note_fans_out(self):
        """Property-level note creates one proposal per active unit."""
        notes = [{
            "note_id": 20,
            "unit_id": None,
            "level": "property",
            "date_posted": "2024-01-01",
            "converted_text": "ALL UTILITIES TENANT RESPONSIBILITY",
            "source_text_hash": compute_source_text_hash(
                "ALL UTILITIES TENANT RESPONSIBILITY"
            ),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "ALL UTILITIES TENANT RESPONSIBILITY",
            "source": "note",
            "note_id": 20,
            "unit_id": None,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50, 51, 52], property_id=11,
        )
        self.assertEqual(len(proposals), 3)
        unit_ids = {p["rentvine_unit_id"] for p in proposals}
        self.assertEqual(unit_ids, {50, 51, 52})
        # All should have source_level="property"
        for p in proposals:
            self.assertEqual(p["source_level"], "property")

    def test_fan_out_fingerprints_unique(self):
        """Each fanned-out proposal gets a different fingerprint."""
        notes = [{
            "note_id": 20,
            "unit_id": None,
            "level": "property",
            "date_posted": "2024-01-01",
            "converted_text": "WATER: Tenant",
            "source_text_hash": compute_source_text_hash("WATER: Tenant"),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "WATER: Tenant",
            "source": "note",
            "note_id": 20,
            "unit_id": None,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, _ = validate_and_build_proposals(
            model_rows, source_info, [50, 51], property_id=11,
        )
        fps = [p["fingerprint"] for p in proposals]
        self.assertEqual(len(fps), len(set(fps)))

    def test_listing_row(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "electric",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "Electric is tenant responsibility",
            "source": "listing_description",
            "note_id": None,
            "unit_id": 50,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["source_type"], "listing_description")
        self.assertEqual(proposals[0]["source_level"], "unit")
        self.assertIsNone(proposals[0]["source_note_id"])

    def test_catch_all_two_lines_verified(self):
        notes = [{
            "note_id": 30,
            "unit_id": 60,
            "level": "unit",
            "date_posted": "2024-03-01",
            "converted_text": "ELECTRIC: Duke Energy\nTENANT PAYS ALL OTHER UTILITIES",
            "source_text_hash": compute_source_text_hash(
                "ELECTRIC: Duke Energy\nTENANT PAYS ALL OTHER UTILITIES"
            ),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "electric",
            "responsibility": "tenant",
            "basis": "catch_all",
            "provider": "Duke Energy",
            "evidence_quote": "ELECTRIC: Duke Energy\nTENANT PAYS ALL OTHER UTILITIES",
            "source": "note",
            "note_id": 30,
            "unit_id": 60,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [60], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["basis"], "catch_all")

    def test_catch_all_one_line_dropped(self):
        notes = [{
            "note_id": 30,
            "unit_id": 60,
            "level": "unit",
            "date_posted": "2024-03-01",
            "converted_text": "ELECTRIC: Duke Energy",
            "source_text_hash": compute_source_text_hash("ELECTRIC: Duke Energy"),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "electric",
            "responsibility": "tenant",
            "basis": "catch_all",
            "provider": "Duke Energy",
            "evidence_quote": "ELECTRIC: Duke Energy",  # only 1 line
            "source": "note",
            "note_id": 30,
            "unit_id": 60,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [60], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["quote_failed"], 1)

    def test_arrangement_cleared_when_not_in_source(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": "City Water",
            "evidence_quote": "WATER: City Water - Tenant pays",
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": None,
            "arrangement": "This arrangement is fabricated",
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["arrangement"], "")
        self.assertEqual(counters["arrangement_cleared"], 1)

    def test_invalid_listing_unit_id_dropped(self):
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "electric",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "Electric is tenant",
            "source": "listing_description",
            "note_id": None,
            "unit_id": 999,  # not a valid listing unit
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_ref"], 1)

    def test_non_dict_row_dropped(self):
        """A non-dict entry in the utilities list is dropped."""
        source_info = self._make_source_info()
        model_rows = [
            "this is not a dict",
            42,
            None,
        ]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_field"], 3)

    def test_non_int_note_id_dropped(self):
        """A note_id that can't be cast to int is dropped as invalid_ref."""
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "WATER: Tenant",
            "source": "note",
            "note_id": "not_a_number",
            "unit_id": 50,
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_ref"], 1)

    def test_non_int_listing_unit_id_dropped(self):
        """A listing unit_id that can't be cast to int is dropped."""
        source_info = self._make_source_info()
        model_rows = [{
            "utility_type": "electric",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": None,
            "evidence_quote": "Electric is tenant",
            "source": "listing_description",
            "note_id": None,
            "unit_id": "bad",
            "source_date": None,
            "arrangement": None,
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 0)
        self.assertEqual(counters["invalid_ref"], 1)


class ExtractJsonObjectTests(SimpleTestCase):
    def test_bare_json(self):
        text = '{"utilities": []}'
        self.assertEqual(extract_json_object(text), '{"utilities": []}')

    def test_json_in_fences(self):
        text = '```json\n{"utilities": [{"type": "water"}]}\n```'
        self.assertEqual(
            extract_json_object(text),
            '{"utilities": [{"type": "water"}]}',
        )

    def test_json_with_prose_before_and_after(self):
        text = (
            "Here is the extracted data:\n"
            '{"utilities": [{"type": "water"}]}\n'
            "Let me know if you need anything else."
        )
        result = extract_json_object(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("{"))
        self.assertTrue(result.endswith("}"))
        import json
        parsed = json.loads(result)
        self.assertIn("utilities", parsed)

    def test_no_braces(self):
        self.assertIsNone(extract_json_object("no json here"))

    def test_empty_string(self):
        self.assertIsNone(extract_json_object(""))

    def test_whitespace_only(self):
        self.assertIsNone(extract_json_object("   \n\t  "))

    def test_fences_without_language_tag(self):
        text = '```\n{"key": "value"}\n```'
        self.assertEqual(extract_json_object(text), '{"key": "value"}')


# ==========================================================================
# Grid pure-function tests
# ==========================================================================

from datetime import date

from utilities.grid import (
    GRID_COLUMNS,
    RESPONSIBILITY_DISPLAY,
    _natural_sort_key,
    apply_filter,
    build_csv_rows,
    build_grid,
    compute_cell_status,
    compute_tile_counts,
    csv_cell_text,
    has_gap,
    is_complete,
    is_flagged,
    missing_oil_propane,
    needs_review,
    occupancy_text,
    sanitize_csv_value,
)
from utilities.views import _grid_url


def _make_proposal(responsibility="tenant", has_conflict=False, basis="stated"):
    """Helper: build a minimal pending proposal dict."""
    return {
        "id": 1,
        "responsibility": responsibility,
        "has_conflict": has_conflict,
        "basis": basis,
        "provider": "",
        "arrangement": "",
    }


def _make_approved(responsibility="tenant"):
    """Helper: build a minimal approved row dict."""
    return {"responsibility": responsibility, "provider": "", "arrangement": ""}


class ComputeCellStatusTests(SimpleTestCase):

    # -- Approved direct --

    def test_ok_tenant(self):
        result = compute_cell_status("electric", _make_approved("tenant"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Tenant", "flag": False})

    def test_ok_owner(self):
        result = compute_cell_status("electric", _make_approved("owner"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Owner", "flag": False})

    def test_ok_included(self):
        result = compute_cell_status("water", _make_approved("included_in_rent"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Included", "flag": False})

    def test_na(self):
        result = compute_cell_status("gas", _make_approved("not_applicable"), None, [], [])
        self.assertEqual(result, {"status": "na", "label": "NA", "flag": False})

    # -- Approved alias --

    def test_water_well_approved(self):
        result = compute_cell_status("water", None, _make_approved("tenant"), [], [])
        self.assertEqual(result, {"status": "ok", "label": "Well", "flag": False})

    def test_sewer_septic_approved(self):
        result = compute_cell_status("sewer", None, _make_approved("owner"), [], [])
        self.assertEqual(result, {"status": "ok", "label": "Septic", "flag": False})

    def test_approved_alias_na(self):
        result = compute_cell_status("water", None, _make_approved("not_applicable"), [], [])
        self.assertEqual(result, {"status": "na", "label": "NA", "flag": False})

    # -- Approved beats pending --

    def test_approved_beats_pending(self):
        result = compute_cell_status(
            "electric",
            _make_approved("tenant"),
            None,
            [_make_proposal("owner")],
            [],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["label"], "Tenant")

    def test_approved_direct_beats_approved_alias(self):
        result = compute_cell_status(
            "water",
            _make_approved("owner"),
            _make_approved("tenant"),
            [],
            [],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["label"], "Owner")

    # -- Pending direct --

    def test_pend_basic(self):
        result = compute_cell_status("electric", None, None, [_make_proposal("tenant")], [])
        self.assertEqual(result, {"status": "pend", "label": "Tenant", "flag": False})

    def test_pend_na_proposal(self):
        result = compute_cell_status("gas", None, None, [_make_proposal("not_applicable")], [])
        self.assertEqual(result, {"status": "pend", "label": "N/A", "flag": False})

    def test_pend_flag_conflict(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", has_conflict=True)], [],
        )
        self.assertTrue(result["flag"])

    def test_pend_flag_catch_all(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", basis="catch_all")], [],
        )
        self.assertTrue(result["flag"])

    def test_pend_flag_disagreement(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant"), _make_proposal("owner")], [],
        )
        self.assertTrue(result["flag"])

    # -- Pending alias --

    def test_pending_well_alias(self):
        """Water column satisfied by a pending well proposal shows label 'Well'."""
        result = compute_cell_status(
            "water", None, None, [],
            [_make_proposal("tenant")],
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Well")

    def test_pending_septic_alias(self):
        """Sewer column satisfied by a pending septic proposal shows label 'Septic'."""
        result = compute_cell_status(
            "sewer", None, None, [],
            [_make_proposal("owner")],
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Septic")

    # -- Unknown and miss --

    def test_pend_unknown_ignored(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("unknown")], [],
        )
        self.assertEqual(result["status"], "miss")

    def test_miss_no_data(self):
        result = compute_cell_status("electric", None, None, [], [])
        self.assertEqual(result, {"status": "miss", "label": "", "flag": False})


class BuildGridTests(SimpleTestCase):

    def _unit(self, pk, rentvine_id, address, unit_number=""):
        return {
            "id": pk,
            "rentvine_id": rentvine_id,
            "display_address": address,
            "unit_number": unit_number,
        }

    def test_sorting(self):
        units = [
            self._unit(2, 200, "Zebra St", "B"),
            self._unit(1, 100, "Apple Ave", "A"),
            self._unit(3, 300, "Apple Ave", "C"),
        ]
        rows = build_grid(units, {}, {}, {}, {})
        addresses = [(r["address"], r["unit_number"]) for r in rows]
        self.assertEqual(addresses, [
            ("Apple Ave", "A"),
            ("Apple Ave", "C"),
            ("Zebra St", "B"),
        ])

    def test_rentvine_id_none_shows_miss(self):
        """A unit with rentvine_id=None cannot match proposals, shows miss."""
        units = [self._unit(1, None, "123 Main St")]
        proposals = {
            999: {"electric": [_make_proposal("tenant")]},
        }
        rows = build_grid(units, {}, proposals, {}, {})
        self.assertEqual(rows[0]["cells"]["electric"]["status"], "miss")

    def test_cell_list_populated(self):
        units = [self._unit(1, 100, "123 Main St")]
        rows = build_grid(units, {}, {}, {}, {})
        self.assertEqual(len(rows[0]["cell_list"]), len(GRID_COLUMNS))
        self.assertEqual(rows[0]["cell_list"][0]["column"], "electric")


class FilterPredicateTests(SimpleTestCase):

    def _row(self, **cell_overrides):
        """Build a row with all-ok cells, then override specified columns."""
        cells = {}
        for col in GRID_COLUMNS:
            cells[col] = {"status": "ok", "label": "Tenant", "flag": False}
        for col, status_info in cell_overrides.items():
            cells[col] = status_info
        return {"cells": cells, "address": "Test", "unit_number": ""}

    def test_is_complete_all_ok(self):
        self.assertTrue(is_complete(self._row()))

    def test_is_complete_with_na(self):
        row = self._row(gas={"status": "na", "label": "NA", "flag": False})
        self.assertTrue(is_complete(row))

    def test_is_complete_fails_with_pend(self):
        row = self._row(water={"status": "pend", "label": "Tenant", "flag": False})
        self.assertFalse(is_complete(row))

    def test_has_gap(self):
        row = self._row(electric={"status": "miss", "label": "", "flag": False})
        self.assertTrue(has_gap(row))

    def test_no_gap(self):
        self.assertFalse(has_gap(self._row()))

    def test_needs_review(self):
        row = self._row(water={"status": "pend", "label": "Tenant", "flag": False})
        self.assertTrue(needs_review(row))

    def test_no_review_needed(self):
        self.assertFalse(needs_review(self._row()))

    def test_is_flagged(self):
        row = self._row(electric={"status": "pend", "label": "Tenant", "flag": True})
        self.assertTrue(is_flagged(row))

    def test_not_flagged(self):
        self.assertFalse(is_flagged(self._row()))

    def test_missing_oil_propane_oil(self):
        row = self._row(oil={"status": "miss", "label": "", "flag": False})
        self.assertTrue(missing_oil_propane(row))

    def test_missing_oil_propane_propane(self):
        row = self._row(propane={"status": "miss", "label": "", "flag": False})
        self.assertTrue(missing_oil_propane(row))

    def test_missing_oil_propane_neither(self):
        self.assertFalse(missing_oil_propane(self._row()))


class TileCountTests(SimpleTestCase):

    def _row(self, **cell_overrides):
        cells = {}
        for col in GRID_COLUMNS:
            cells[col] = {"status": "ok", "label": "Tenant", "flag": False}
        for col, status_info in cell_overrides.items():
            cells[col] = status_info
        return {"cells": cells, "address": "Test", "unit_number": ""}

    def test_all_complete(self):
        rows = [self._row(), self._row()]
        tiles = compute_tile_counts(rows)
        self.assertEqual(tiles["complete"], 2)
        self.assertEqual(tiles["needs_review"], 0)
        self.assertEqual(tiles["has_gap"], 0)
        self.assertEqual(tiles["missing_oil_propane"], 0)

    def test_mixed(self):
        rows = [
            self._row(),  # complete
            self._row(water={"status": "pend", "label": "Tenant", "flag": False}),  # review
            self._row(electric={"status": "miss", "label": "", "flag": False}),  # gap
            self._row(oil={"status": "miss", "label": "", "flag": False}),  # oil missing + gap
        ]
        tiles = compute_tile_counts(rows)
        self.assertEqual(tiles["complete"], 1)
        self.assertEqual(tiles["needs_review"], 1)
        self.assertEqual(tiles["has_gap"], 2)  # rows 3 and 4
        self.assertEqual(tiles["missing_oil_propane"], 1)

    def test_na_counts_as_complete(self):
        """A unit with only ok and na cells counts as complete."""
        row = self._row(
            gas={"status": "na", "label": "NA", "flag": False},
            oil={"status": "na", "label": "NA", "flag": False},
            propane={"status": "na", "label": "NA", "flag": False},
        )
        tiles = compute_tile_counts([row])
        self.assertEqual(tiles["complete"], 1)


class ApplyFilterTests(SimpleTestCase):

    def _row(self, address="123 Main", **cell_overrides):
        cells = {}
        for col in GRID_COLUMNS:
            cells[col] = {"status": "ok", "label": "Tenant", "flag": False}
        for col, status_info in cell_overrides.items():
            cells[col] = status_info
        return {"cells": cells, "address": address, "unit_number": ""}

    def test_all_filter(self):
        rows = [self._row(), self._row()]
        self.assertEqual(len(apply_filter(rows, "all")), 2)

    def test_gap_filter(self):
        rows = [
            self._row(),
            self._row(electric={"status": "miss", "label": "", "flag": False}),
        ]
        result = apply_filter(rows, "gap")
        self.assertEqual(len(result), 1)

    def test_review_filter(self):
        rows = [
            self._row(),
            self._row(water={"status": "pend", "label": "Tenant", "flag": False}),
        ]
        result = apply_filter(rows, "review")
        self.assertEqual(len(result), 1)

    def test_oil_propane_filter(self):
        rows = [
            self._row(),
            self._row(propane={"status": "miss", "label": "", "flag": False}),
        ]
        result = apply_filter(rows, "oil_propane")
        self.assertEqual(len(result), 1)

    def test_flagged_filter(self):
        rows = [
            self._row(),
            self._row(electric={"status": "pend", "label": "Tenant", "flag": True}),
        ]
        result = apply_filter(rows, "flagged")
        self.assertEqual(len(result), 1)

    def test_query_filter(self):
        rows = [
            self._row(address="123 Main St"),
            self._row(address="456 Oak Ave"),
        ]
        result = apply_filter(rows, "all", query="oak")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["address"], "456 Oak Ave")

    def test_query_and_filter_combined(self):
        rows = [
            self._row(address="123 Main", electric={"status": "miss", "label": "", "flag": False}),
            self._row(address="456 Oak", electric={"status": "miss", "label": "", "flag": False}),
            self._row(address="789 Main"),
        ]
        result = apply_filter(rows, "gap", query="main")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["address"], "123 Main")


class CsvCellTextTests(SimpleTestCase):

    def test_ok(self):
        self.assertEqual(csv_cell_text({"status": "ok", "label": "Tenant", "flag": False}), "Tenant")

    def test_ok_well(self):
        self.assertEqual(csv_cell_text({"status": "ok", "label": "Well", "flag": False}), "Well")

    def test_na(self):
        self.assertEqual(csv_cell_text({"status": "na", "label": "NA", "flag": False}), "NA")

    def test_pend_with_label(self):
        self.assertEqual(
            csv_cell_text({"status": "pend", "label": "Tenant", "flag": False}),
            "Needs review: Tenant",
        )

    def test_pend_no_label(self):
        self.assertEqual(
            csv_cell_text({"status": "pend", "label": "", "flag": False}),
            "Needs review",
        )

    def test_miss(self):
        self.assertEqual(csv_cell_text({"status": "miss", "label": "", "flag": False}), "Missing")


class SanitizeCsvValueTests(SimpleTestCase):

    def test_normal_value(self):
        self.assertEqual(sanitize_csv_value("Tenant"), "Tenant")

    def test_equals_prefix(self):
        self.assertEqual(sanitize_csv_value("=SUM(A1:A10)"), "'=SUM(A1:A10)")

    def test_plus_prefix(self):
        self.assertEqual(sanitize_csv_value("+cmd"), "'+cmd")

    def test_minus_prefix(self):
        self.assertEqual(sanitize_csv_value("-cmd"), "'-cmd")

    def test_at_prefix(self):
        self.assertEqual(sanitize_csv_value("@SUM"), "'@SUM")

    def test_empty_string(self):
        self.assertEqual(sanitize_csv_value(""), "")

    def test_none(self):
        self.assertIsNone(sanitize_csv_value(None))


class CsvRowBuilderTests(SimpleTestCase):
    """Test build_csv_rows on real build_grid output."""

    def test_full_row_from_build_grid(self):
        units = [{"id": 1, "rentvine_id": 100, "display_address": "123 Main St", "unit_number": "A"}]
        approved = {1: {"electric": {"responsibility": "tenant", "provider": "Duke", "arrangement": ""}}}
        proposals = {100: {"water": [_make_proposal("owner")]}}
        notes = {1: {"latest": "Check meter", "count": 1}}
        occupancy = {1: {"occupied": True, "move_in_date": date(2024, 1, 15)}}

        rows = build_grid(units, approved, proposals, notes, occupancy)
        csv_rows = build_csv_rows(rows)

        self.assertEqual(len(csv_rows), 1)
        line = csv_rows[0]
        self.assertEqual(line[0], "123 Main St")
        self.assertEqual(line[1], "A")
        self.assertIn("Jan 15, 2024", line[2])
        # Electric (index 3) — approved tenant
        self.assertEqual(line[3], "Tenant")
        # Water (index 4) — pending owner
        self.assertEqual(line[4], "Needs review: Owner")
        # Last column — latest note
        self.assertEqual(line[-1], "Check meter")

    def test_vacant_occupancy_in_csv(self):
        units = [{"id": 1, "rentvine_id": 100, "display_address": "456 Oak", "unit_number": ""}]
        rows = build_grid(units, {}, {}, {}, {})
        csv_rows = build_csv_rows(rows)
        self.assertEqual(csv_rows[0][2], "Vacant")

    def test_no_none_in_csv(self):
        """CSV must never contain the string 'None'."""
        units = [{"id": 1, "rentvine_id": 100, "display_address": "789 Elm", "unit_number": ""}]
        occupancy = {1: {"occupied": True, "move_in_date": None}}
        rows = build_grid(units, {}, {}, {}, occupancy)
        csv_rows = build_csv_rows(rows)
        for cell in csv_rows[0]:
            if cell is not None:
                self.assertNotIn("None", str(cell))

    def test_formula_injection_sanitized(self):
        units = [{"id": 1, "rentvine_id": 100, "display_address": "=Evil", "unit_number": ""}]
        rows = build_grid(units, {}, {}, {}, {})
        csv_rows = build_csv_rows(rows)
        self.assertTrue(csv_rows[0][0].startswith("'="))

    def test_latest_note_sanitized(self):
        units = [{"id": 1, "rentvine_id": 100, "display_address": "X", "unit_number": ""}]
        notes = {1: {"latest": "=dangerous formula", "count": 1}}
        rows = build_grid(units, {}, {}, notes, {})
        csv_rows = build_csv_rows(rows)
        self.assertEqual(csv_rows[0][-1], "'=dangerous formula")


class OccupancyTextTests(SimpleTestCase):

    def test_vacant(self):
        self.assertEqual(occupancy_text({"occupied": False, "move_in_date": None}), "Vacant")

    def test_occupied_no_date(self):
        self.assertEqual(occupancy_text({"occupied": True, "move_in_date": None}), "Occupied")

    def test_occupied_with_date(self):
        result = occupancy_text({"occupied": True, "move_in_date": date(2024, 3, 15)})
        self.assertEqual(result, "Occupied \u00b7 since Mar 15, 2024")

    def test_empty_dict(self):
        self.assertEqual(occupancy_text({}), "Vacant")

    def test_build_grid_includes_occupancy_text(self):
        units = [{"id": 1, "rentvine_id": 100, "display_address": "X", "unit_number": ""}]
        occ = {1: {"occupied": True, "move_in_date": date(2024, 6, 1)}}
        rows = build_grid(units, {}, {}, {}, occ)
        self.assertEqual(rows[0]["occupancy_text"], "Occupied \u00b7 since Jun 1, 2024")


class NaturalSortTests(SimpleTestCase):

    def test_numeric_ordering(self):
        items = ["10", "2", "1", "20", "3"]
        result = sorted(items, key=_natural_sort_key)
        self.assertEqual(result, ["1", "2", "3", "10", "20"])

    def test_alphanumeric(self):
        items = ["Unit 10", "Unit 2", "Unit 1"]
        result = sorted(items, key=_natural_sort_key)
        self.assertEqual(result, ["Unit 1", "Unit 2", "Unit 10"])

    def test_empty_string(self):
        self.assertEqual(_natural_sort_key(""), [""])

    def test_build_grid_natural_sort(self):
        """build_grid sorts unit_number naturally."""
        units = [
            {"id": 1, "rentvine_id": 100, "display_address": "100 Main St", "unit_number": "10"},
            {"id": 2, "rentvine_id": 200, "display_address": "100 Main St", "unit_number": "2"},
            {"id": 3, "rentvine_id": 300, "display_address": "100 Main St", "unit_number": "1"},
        ]
        rows = build_grid(units, {}, {}, {}, {})
        numbers = [r["unit_number"] for r in rows]
        self.assertEqual(numbers, ["1", "2", "10"])


class ResponsibilityDisplayTests(SimpleTestCase):

    def test_all_keys_present(self):
        for key in ("tenant", "owner", "included_in_rent", "not_applicable", "unknown"):
            self.assertIn(key, RESPONSIBILITY_DISPLAY)

    def test_human_readable(self):
        self.assertEqual(RESPONSIBILITY_DISPLAY["included_in_rent"], "Included in Rent")
        self.assertEqual(RESPONSIBILITY_DISPLAY["not_applicable"], "Not Applicable")


class GridUrlTests(SimpleTestCase):

    def test_ampersand_in_query(self):
        url = _grid_url({"q": "a&b"})
        self.assertIn("q=a%26b", url)

    def test_hash_in_query(self):
        url = _grid_url({"q": "a#b"})
        self.assertIn("q=a%23b", url)

    def test_space_in_query(self):
        url = _grid_url({"q": "a b"})
        self.assertIn("q=a+b", url)

    def test_empty_params(self):
        self.assertEqual(_grid_url({}), "/dashboard/utilities/")

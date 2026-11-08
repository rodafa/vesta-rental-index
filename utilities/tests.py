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

"""
Pure-function tests for utilities.services.

SimpleTestCase only — no database access.
"""

from django.test import SimpleTestCase

from utilities.services import (
    REDACTION_MARKER,
    build_source_lookup,
    build_user_prompt,
    check_payer_phrase,
    check_quote,
    collapse_ws,
    compute_fingerprint,
    compute_source_text_hash,
    convert_note_html,
    detect_conflicts,
    extract_json_object,
    filter_and_convert_notes,
    has_utility_words,
    redact_security_lines,
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


class RedactSecurityLinesTests(SimpleTestCase):
    """Tests for line-level security redaction."""

    def test_code_line_removed_utility_kept(self):
        """A note with a code line and a utility line keeps only the utility."""
        text = "WATER: Tenant pays\nlockbox code is 1234"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 1)
        self.assertIn("WATER: Tenant pays", redacted)
        self.assertNotIn("1234", redacted)
        self.assertIn(REDACTION_MARKER, redacted)

    def test_door_code_followed_by_bare_code(self):
        """A 'door code' line + bare-code line → both removed, one marker."""
        text = "WATER: Tenant\nDoor code: see below\n5678\nELECTRIC: Owner"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 2)
        self.assertNotIn("5678", redacted)
        self.assertNotIn("Door code", redacted)
        self.assertIn("WATER: Tenant", redacted)
        self.assertIn("ELECTRIC: Owner", redacted)
        # Only one marker for both removed lines
        self.assertEqual(redacted.count(REDACTION_MARKER), 1)

    def test_no_sensitive_text_byte_identical(self):
        """A clean note round-trips identically (hash unchanged)."""
        text = "WATER: Tenant pays\nELECTRIC: Duke Energy"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 0)
        self.assertEqual(redacted, text)
        self.assertEqual(
            compute_source_text_hash(redacted),
            compute_source_text_hash(text),
        )

    def test_only_code_lines_dropped(self):
        """A note with only security lines → dropped by filter pipeline."""
        raw_notes = [{
            "noteID": 1,
            "note": "<p>lockbox code is 1234</p><p>alarm code is 9999</p>",
            "datePosted": "2024-01-01",
        }]
        converted, dropped, redacted_count = filter_and_convert_notes(
            raw_notes, "unit", 50,
        )
        self.assertEqual(len(converted), 0)
        self.assertEqual(dropped, 1)
        self.assertGreater(redacted_count, 0)

    def test_no_utility_words_after_redaction_dropped(self):
        """Redaction removes the only utility word → note dropped."""
        raw_notes = [{
            "noteID": 2,
            "note": "<p>door code: 5678</p><p>water meter in basement</p>",
            "datePosted": "2024-01-01",
        }]
        # The utility word "water" + "meter" is in the non-security line,
        # so this note survives. Build a case where it doesn't:
        raw_notes_no_util = [{
            "noteID": 3,
            "note": "<p>lockbox is on the gate</p><p>Lawn care on Tuesdays</p>",
            "datePosted": "2024-01-01",
        }]
        converted, dropped, _ = filter_and_convert_notes(
            raw_notes_no_util, "unit", 50,
        )
        self.assertEqual(len(converted), 0)
        self.assertEqual(dropped, 1)

    def test_quote_containing_marker_rejected(self):
        """check_quote rejects a quote with the redaction marker."""
        source = "WATER: Tenant\n[line removed]\nELECTRIC: Owner"
        self.assertFalse(check_quote("[line removed]\nELECTRIC: Owner", source))
        self.assertFalse(check_quote("[line removed]", source))
        # Normal quote still works
        self.assertTrue(check_quote("WATER: Tenant", source))

    def test_consecutive_markers_collapse(self):
        """Two adjacent security lines produce one marker."""
        text = "WATER: Tenant\nlockbox is 1234\nalarm code 5678\nELECTRIC: Owner"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 2)
        self.assertEqual(redacted.count(REDACTION_MARKER), 1)
        lines = redacted.split("\n")
        self.assertEqual(lines, [
            "WATER: Tenant",
            REDACTION_MARKER,
            "ELECTRIC: Owner",
        ])

    def test_table_row_security_word_removed(self):
        """A pipe-delimited table row with a security word is removed."""
        text = "Utility | Landlord | Resident\nWater |  | X\nDoor | Code | 1234"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 1)
        self.assertIn("Water |  | X", redacted)
        self.assertNotIn("1234", redacted)
        self.assertNotIn("Door | Code", redacted)

    def test_table_row_no_sensitive_text_kept(self):
        """A table row without sensitive text is kept byte-identical."""
        text = "Utility | Landlord | Resident\nWater |  | X\nElectric | X |"
        redacted, removed = redact_security_lines(text)
        self.assertEqual(removed, 0)
        self.assertEqual(redacted, text)

    def test_filter_pipeline_preserves_clean_note(self):
        """A clean note through the full pipeline has unchanged text and hash."""
        # </p><p> produces a blank line in the template converter
        expected_text = "WATER: Tenant pays\n\nELECTRIC: Duke Energy"
        raw_notes = [{
            "noteID": 10,
            "note": "<p>WATER: Tenant pays</p><p>ELECTRIC: Duke Energy</p>",
            "datePosted": "2024-01-01",
        }]
        converted, dropped, redacted_count = filter_and_convert_notes(
            raw_notes, "unit", 50,
        )
        self.assertEqual(len(converted), 1)
        self.assertEqual(redacted_count, 0)
        self.assertEqual(converted[0]["converted_text"], expected_text)


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
        self.assertTrue(has_utility_words("Check the gas meter"))
        self.assertTrue(has_utility_words("The heat is electric"))

    def test_utilities_plural(self):
        self.assertTrue(has_utility_words("TENANT PAYS FOR ALL UTILITIES"))

    def test_utility_singular(self):
        self.assertTrue(has_utility_words("Utility info"))

    def test_no_utility_words_lawn_care(self):
        self.assertFalse(has_utility_words("OWNER PAYS FOR LAWN CARE"))


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

    def test_preserves_existing_payer_note(self):
        """detect_conflicts appends cross-source note; does not overwrite payer note."""
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10,
             "has_conflict": True,
             "conflict_note": "Quote says tenant/resident pays but row says owner"},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 20,
             "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        # First proposal keeps payer note AND gets cross-source note
        self.assertIn(
            "Quote says tenant/resident pays but row says owner",
            result[0]["conflict_note"],
        )
        self.assertIn("note 10", result[0]["conflict_note"])
        self.assertIn("note 20", result[0]["conflict_note"])
        # Second proposal gets the cross-source note
        self.assertTrue(result[1]["has_conflict"])
        self.assertIn("note 10", result[1]["conflict_note"])

    def test_no_duplicate_note_text(self):
        """detect_conflicts does not append the same note text twice."""
        note_text = "water @ unit 1: note 10: owner vs note 20: tenant"
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10,
             "has_conflict": True,
             "conflict_note": note_text},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 20,
             "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        # The cross-source note is a superset that contains "note 10" and "note 20"
        # but it should not duplicate existing text if already present
        count = result[0]["conflict_note"].count(note_text)
        self.assertEqual(count, 1)


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


class CheckPayerPhraseTests(SimpleTestCase):
    """Tests for the payer-phrase guard."""

    # -- Core tests --

    def test_hendersonville_owner_flagged(self):
        """Exact prod quote: TENANT PAYS but responsibility=owner → flagged."""
        quote = (
            "WATER - TENANT PAYS - HENDERSONVILLE WATER/SEWER "
            "(There is only one master meter for the property, water is "
            "under the owner's name, and we send the monthly bill to "
            "the tenant)"
        )
        conflict, note = check_payer_phrase(quote, "owner")
        self.assertTrue(conflict)
        self.assertIn("tenant", note.lower())
        self.assertIn("owner", note.lower())

    def test_correct_tenant_not_flagged(self):
        """Same Hendersonville quote with correct responsibility=tenant."""
        quote = (
            "WATER - TENANT PAYS - HENDERSONVILLE WATER/SEWER "
            "(There is only one master meter for the property, water is "
            "under the owner's name, and we send the monthly bill to "
            "the tenant)"
        )
        conflict, note = check_payer_phrase(quote, "tenant")
        self.assertFalse(conflict)

    def test_owner_pays_with_tenant_responsibility_flagged(self):
        quote = "GAS - OWNER PAYS - Piedmont Natural Gas"
        conflict, note = check_payer_phrase(quote, "tenant")
        self.assertTrue(conflict)
        self.assertIn("owner", note.lower())

    def test_no_payer_phrases_not_flagged(self):
        """No explicit payer phrase → inconclusive → no flag."""
        quote = "ELECTRIC: Duke Energy"
        conflict, _ = check_payer_phrase(quote, "owner")
        self.assertFalse(conflict)

    def test_both_parties_mentioned_not_flagged(self):
        """Both sides mentioned → ambiguous → no flag."""
        quote = "WATER - TENANT PAYS water bill, OWNER PAYS sewer portion"
        conflict, _ = check_payer_phrase(quote, "tenant")
        self.assertFalse(conflict)

    # -- Broadened tenant-side phrase families --

    def test_resident_pays(self):
        conflict, _ = check_payer_phrase("WATER - RESIDENT PAYS", "owner")
        self.assertTrue(conflict)

    def test_paid_by_tenant(self):
        conflict, _ = check_payer_phrase("Water is paid by the tenant", "owner")
        self.assertTrue(conflict)

    def test_tenants_responsibility(self):
        conflict, _ = check_payer_phrase(
            "Water is tenant's responsibility", "owner",
        )
        self.assertTrue(conflict)

    def test_tenant_to_pay(self):
        conflict, _ = check_payer_phrase("WATER - tenant to pay", "owner")
        self.assertTrue(conflict)

    # -- Broadened owner-side phrase families --

    def test_paid_by_owner(self):
        conflict, _ = check_payer_phrase("Trash is paid by the owner", "tenant")
        self.assertTrue(conflict)

    def test_owners_responsibility(self):
        conflict, _ = check_payer_phrase(
            "Sewer is owner's responsibility", "tenant",
        )
        self.assertTrue(conflict)

    def test_owner_to_pay(self):
        conflict, _ = check_payer_phrase("WATER - owner to pay", "tenant")
        self.assertTrue(conflict)

    def test_landlord_provides(self):
        conflict, _ = check_payer_phrase(
            "Internet - landlord provides", "tenant",
        )
        self.assertTrue(conflict)

    # -- Progressive / future forms --

    def test_asheville_owner_paying_not_flagged(self):
        """Exact prod quote: both sides match → inconclusive → no flag."""
        quote = (
            "City water - CITY OF ASHEVILLE - lease should say tenant "
            "pays, but for right now, owner is paying until submeters "
            "installed."
        )
        conflict, _ = check_payer_phrase(quote, "owner")
        self.assertFalse(conflict)

    def test_asheville_tenant_not_flagged(self):
        """Same prod quote with responsibility=tenant → both match → no flag."""
        quote = (
            "City water - CITY OF ASHEVILLE - lease should say tenant "
            "pays, but for right now, owner is paying until submeters "
            "installed."
        )
        conflict, _ = check_payer_phrase(quote, "tenant")
        self.assertFalse(conflict)

    def test_tenant_is_paying_flags_owner(self):
        conflict, _ = check_payer_phrase("tenant is paying", "owner")
        self.assertTrue(conflict)

    def test_owner_will_pay_flags_tenant(self):
        conflict, _ = check_payer_phrase("owner will pay", "tenant")
        self.assertTrue(conflict)


class PayerMismatchIntegrationTests(SimpleTestCase):
    """Integration: payer guard wired through validate_and_build_proposals."""

    def test_payer_mismatch_flagged_through_validation(self):
        """Hendersonville quote with responsibility=owner → flagged proposal."""
        quote = (
            "WATER - TENANT PAYS - HENDERSONVILLE WATER/SEWER "
            "(There is only one master meter for the property, water is "
            "under the owner's name, and we send the monthly bill to "
            "the tenant)"
        )
        notes = [{
            "note_id": 10,
            "unit_id": 50,
            "level": "unit",
            "date_posted": "2024-01-15",
            "converted_text": quote,
            "source_text_hash": compute_source_text_hash(quote),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "water",
            "responsibility": "owner",
            "basis": "stated",
            "provider": "HENDERSONVILLE WATER/SEWER",
            "evidence_quote": quote,
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": "2024-01-15",
            "arrangement": (
                "water is under the owner's name, and we send the "
                "monthly bill to the tenant"
            ),
            "extras": None,
        }]
        proposals, counters = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertTrue(proposals[0]["has_conflict"])
        self.assertNotEqual(proposals[0]["conflict_note"], "")
        self.assertEqual(len(proposals[0]["fingerprint"]), 64)

    def test_correct_row_not_flagged_through_validation(self):
        """Correct responsibility=tenant with TENANT PAYS → no flag."""
        quote = "WATER - TENANT PAYS - City Water"
        notes = [{
            "note_id": 10,
            "unit_id": 50,
            "level": "unit",
            "date_posted": "2024-01-15",
            "converted_text": quote,
            "source_text_hash": compute_source_text_hash(quote),
        }]
        source_info = build_source_lookup(notes, [])
        model_rows = [{
            "utility_type": "water",
            "responsibility": "tenant",
            "basis": "stated",
            "provider": "City Water",
            "evidence_quote": quote,
            "source": "note",
            "note_id": 10,
            "unit_id": 50,
            "source_date": "2024-01-15",
            "arrangement": None,
            "extras": None,
        }]
        proposals, _ = validate_and_build_proposals(
            model_rows, source_info, [50], property_id=11,
        )
        self.assertEqual(len(proposals), 1)
        self.assertFalse(proposals[0]["has_conflict"])


# ==========================================================================
# Grid pure-function tests
# ==========================================================================

from datetime import date

from utilities.grid import (
    ALLOWED_TYPES_FOR_COLUMN,
    BLANKET_EXCLUDED_COLUMNS,
    GRID_COLUMNS,
    MANUAL_RESPONSIBILITIES,
    RESPONSIBILITY_DISPLAY,
    _natural_sort_key,
    apply_filter,
    approved_rows_to_replace,
    blanket_columns_to_fill,
    build_csv_rows,
    build_grid,
    build_provider_display_map,
    check_approve_proposal,
    column_group,
    compute_cell_status,
    compute_tile_counts,
    conflict_class,
    csv_cell_text,
    has_gap,
    is_blanket_proposal,
    is_complete,
    is_flagged,
    missing_oil_propane,
    needs_review,
    occupancy_text,
    prepare_panel_proposals,
    sanitize_csv_value,
    siblings_to_supersede,
    validate_manual_answer,
    validate_reject,
)
from utilities.services import recompute_conflict_for_group
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


def _make_blanket(quote="TENANT PAYS FOR ALL UTILITIES", responsibility="tenant"):
    """Helper: build a minimal blanket-style proposal dict."""
    return {
        "utility_type": "other",
        "responsibility": responsibility,
        "evidence_quote": quote,
        "has_conflict": False,
        "basis": "stated",
    }


def _make_pending_blanket(quote="TENANT PAYS FOR ALL UTILITIES", responsibility="tenant"):
    """Helper: blanket proposal shaped like fetch_pending_proposals output.

    Must mirror the dict keys in fetch_pending_proposals — no utility_type key;
    the type is only the outer map key in the proposals_map.
    """
    return {
        "id": 1,
        "responsibility": responsibility,
        "has_conflict": False,
        "basis": "stated",
        "provider": "",
        "arrangement": "",
        "evidence_quote": quote,
        "source_type": "note",
        "source_level": "unit",
        "source_date": None,
        "quote_verified": False,
        "conflict_note": "",
    }


class IsBlanketProposalTests(SimpleTestCase):

    def test_tenant_pays_all_utilities(self):
        self.assertTrue(is_blanket_proposal(_make_blanket(
            "TENANT PAYS FOR ALL UTILITIES",
        )))

    def test_tenant_responsible_for_utilities(self):
        self.assertTrue(is_blanket_proposal(_make_blanket(
            "Tenant responsible for utilities",
        )))

    def test_except_exclusion(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "Tenant pays all utilities except water",
        )))

    def test_specific_utility_word(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "Tenant pays water and electric utilities",
        )))

    def test_no_utility_word(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "Tenant pays for lawn care",
        )))

    def test_unknown_responsibility(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "TENANT PAYS FOR ALL UTILITIES", responsibility="unknown",
        )))

    def test_not_applicable_responsibility(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "TENANT PAYS FOR ALL UTILITIES", responsibility="not_applicable",
        )))

    def test_non_other_type(self):
        p = _make_blanket("TENANT PAYS FOR ALL UTILITIES")
        p["utility_type"] = "water"
        self.assertFalse(is_blanket_proposal(p))

    def test_contribution_does_not_trigger_but(self):
        self.assertTrue(is_blanket_proposal(_make_blanket(
            "Tenant responsible for utilities, contribution to be arranged",
        )))

    def test_toil_does_not_trigger_oil(self):
        self.assertTrue(is_blanket_proposal(_make_blanket(
            "Tenant pays all utilities, no toil",
        )))

    def test_but_exclusion(self):
        self.assertFalse(is_blanket_proposal(_make_blanket(
            "Tenant pays all utilities but water",
        )))


class ComputeCellStatusTests(SimpleTestCase):

    # -- Approved direct --

    def test_ok_tenant(self):
        result = compute_cell_status("electric", _make_approved("tenant"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Tenant", "flag": False, "blanket": False, "inferred": False})

    def test_ok_owner(self):
        result = compute_cell_status("electric", _make_approved("owner"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Owner", "flag": False, "blanket": False, "inferred": False})

    def test_ok_included(self):
        result = compute_cell_status("water", _make_approved("included_in_rent"), None, [], [])
        self.assertEqual(result, {"status": "ok", "label": "Included", "flag": False, "blanket": False, "inferred": False})

    def test_na(self):
        result = compute_cell_status("gas", _make_approved("not_applicable"), None, [], [])
        self.assertEqual(result, {"status": "na", "label": "NA", "flag": False, "blanket": False, "inferred": False})

    # -- Approved alias --

    def test_water_well_approved(self):
        result = compute_cell_status("water", None, _make_approved("tenant"), [], [])
        self.assertEqual(result, {"status": "ok", "label": "Well", "flag": False, "blanket": False, "inferred": False})

    def test_sewer_septic_approved(self):
        result = compute_cell_status("sewer", None, _make_approved("owner"), [], [])
        self.assertEqual(result, {"status": "ok", "label": "Septic", "flag": False, "blanket": False, "inferred": False})

    def test_approved_alias_na(self):
        result = compute_cell_status("water", None, _make_approved("not_applicable"), [], [])
        self.assertEqual(result, {"status": "na", "label": "NA", "flag": False, "blanket": False, "inferred": False})

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
        self.assertEqual(result, {"status": "pend", "label": "Tenant", "flag": False, "blanket": False, "inferred": False})

    def test_pend_na_proposal(self):
        result = compute_cell_status("gas", None, None, [_make_proposal("not_applicable")], [])
        self.assertEqual(result, {"status": "pend", "label": "N/A", "flag": False, "blanket": False, "inferred": False})

    def test_pend_flag_conflict(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", has_conflict=True)], [],
        )
        self.assertTrue(result["flag"])

    def test_pend_catch_all_inferred_not_flagged(self):
        """A sole catch_all proposal is inferred, not flagged (flag split)."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", basis="catch_all")], [],
        )
        self.assertFalse(result["flag"])
        self.assertTrue(result["inferred"])

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

    def test_pend_unknown_gives_info(self):
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("unknown")], [],
        )
        self.assertEqual(result["status"], "info")
        self.assertEqual(result["label"], "Info only")

    def test_miss_no_data(self):
        result = compute_cell_status("electric", None, None, [], [])
        self.assertEqual(result, {"status": "miss", "label": "", "flag": False, "blanket": False, "inferred": False})

    # -- Blanket priority --

    def test_pending_direct_beats_blanket(self):
        blanket = [_make_blanket()]
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("owner")], [],
            blanket_pending=blanket,
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Owner")
        self.assertFalse(result["blanket"])

    def test_blanket_beats_miss(self):
        blanket = [_make_blanket()]
        result = compute_cell_status(
            "electric", None, None, [], [],
            blanket_pending=blanket,
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Tenant")
        self.assertTrue(result["blanket"])

    def test_approved_direct_beats_blanket(self):
        blanket = [_make_blanket()]
        result = compute_cell_status(
            "electric", _make_approved("owner"), None, [], [],
            blanket_pending=blanket,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["label"], "Owner")
        self.assertFalse(result["blanket"])

    def test_approved_alias_beats_blanket(self):
        blanket = [_make_blanket()]
        result = compute_cell_status(
            "water", None, _make_approved("tenant"), [], [],
            blanket_pending=blanket,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["label"], "Well")
        self.assertFalse(result["blanket"])


class BlanketCellTests(SimpleTestCase):

    def test_blanket_cell_properties(self):
        blanket = [_make_blanket()]
        result = compute_cell_status(
            "electric", None, None, [], [],
            blanket_pending=blanket,
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Tenant")
        self.assertFalse(result["flag"])
        self.assertTrue(result["blanket"])
        self.assertTrue(result["inferred"])

    def test_non_blanket_has_blanket_false(self):
        ok = compute_cell_status("electric", _make_approved("tenant"), None, [], [])
        self.assertFalse(ok["blanket"])

        pend = compute_cell_status("electric", None, None, [_make_proposal("tenant")], [])
        self.assertFalse(pend["blanket"])

        miss = compute_cell_status("electric", None, None, [], [])
        self.assertFalse(miss["blanket"])

    def test_disagreeing_blankets_inferred_not_flagged(self):
        """Disagreeing blankets are inferred, not flagged (flag split)."""
        blanket = [
            _make_blanket(responsibility="tenant"),
            _make_blanket("Owner pays all utilities", responsibility="owner"),
        ]
        result = compute_cell_status(
            "electric", None, None, [], [],
            blanket_pending=blanket,
        )
        self.assertFalse(result["flag"])
        self.assertTrue(result["inferred"])
        self.assertTrue(result["blanket"])


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

    def test_blanket_only_unit(self):
        """Unit with only a blanket row: non-excluded cells pend, oil/propane miss."""
        units = [self._unit(1, 100, "123 Main St")]
        proposals = {100: {"other": [_make_pending_blanket()]}}
        rows = build_grid(units, {}, proposals, {}, {})
        for col in GRID_COLUMNS:
            cell = rows[0]["cells"][col]
            if col in BLANKET_EXCLUDED_COLUMNS:
                self.assertEqual(cell["status"], "miss", f"{col} status")
            else:
                self.assertEqual(cell["status"], "pend", f"{col} status")
                self.assertEqual(cell["label"], "Tenant", f"{col} label")
                self.assertFalse(cell["flag"], f"{col} flag (blanket → not flagged)")
                self.assertTrue(cell["blanket"], f"{col} blanket")
                self.assertTrue(cell["inferred"], f"{col} inferred")

    def test_blanket_plus_direct(self):
        """Blanket + direct Electric=owner: Electric direct, non-excluded others blanket, oil/propane miss."""
        units = [self._unit(1, 100, "123 Main St")]
        proposals = {100: {
            "other": [_make_pending_blanket()],
            "electric": [_make_proposal("owner")],
        }}
        rows = build_grid(units, {}, proposals, {}, {})
        electric = rows[0]["cells"]["electric"]
        self.assertEqual(electric["label"], "Owner")
        self.assertFalse(electric["blanket"])
        for col in GRID_COLUMNS:
            if col == "electric":
                continue
            cell = rows[0]["cells"][col]
            if col in BLANKET_EXCLUDED_COLUMNS:
                self.assertEqual(cell["status"], "miss", f"{col} status")
            else:
                self.assertEqual(cell["label"], "Tenant", f"{col} label")
                self.assertTrue(cell["blanket"], f"{col} blanket")


class PreparePanelProposalsTests(SimpleTestCase):

    def test_column_proposal_passes_through(self):
        p = {
            "utility_type": "electric", "responsibility": "tenant",
            "evidence_quote": "ELECTRIC: Tenant", "has_conflict": False,
            "basis": "stated",
        }
        result = prepare_panel_proposals([p], "electric")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["is_blanket"])

    def test_blanket_other_row_kept(self):
        p = _make_blanket()
        result = prepare_panel_proposals([p], "electric")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["is_blanket"])

    def test_non_blanket_other_dropped(self):
        p = {
            "utility_type": "other", "responsibility": "tenant",
            "evidence_quote": "Tenant pays for lawn care",
            "has_conflict": False, "basis": "stated",
        }
        result = prepare_panel_proposals([p], "electric")
        self.assertEqual(len(result), 0)

    def test_alias_proposal_passes_through(self):
        p = {
            "utility_type": "well", "responsibility": "tenant",
            "evidence_quote": "Well water", "has_conflict": False,
            "basis": "stated",
        }
        result = prepare_panel_proposals([p], "water")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["is_blanket"])


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

    def test_blanket_cell_suffix(self):
        self.assertEqual(
            csv_cell_text({"status": "pend", "label": "Tenant", "flag": False, "blanket": True, "inferred": True}),
            "Needs review: Tenant (blanket)",
        )


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


# ==========================================================================
# conflict_class tests
# ==========================================================================

class ConflictClassTests(SimpleTestCase):

    def test_owner_returns_owner_side(self):
        self.assertEqual(conflict_class("owner"), "owner_side")

    def test_included_in_rent_returns_owner_side(self):
        self.assertEqual(conflict_class("included_in_rent"), "owner_side")

    def test_owner_equals_included(self):
        self.assertEqual(conflict_class("owner"), conflict_class("included_in_rent"))

    def test_tenant_returns_tenant(self):
        self.assertEqual(conflict_class("tenant"), "tenant")

    def test_tenant_differs_from_owner(self):
        self.assertNotEqual(conflict_class("tenant"), conflict_class("owner"))

    def test_not_applicable(self):
        self.assertEqual(conflict_class("not_applicable"), "not_applicable")

    def test_unknown(self):
        self.assertEqual(conflict_class("unknown"), "unknown")


# ==========================================================================
# detect_conflicts with conflict_class (owner vs included_in_rent)
# ==========================================================================

class DetectConflictsOwnerIncludedTests(SimpleTestCase):

    def test_owner_vs_included_not_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "included_in_rent", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertFalse(result[0]["has_conflict"])
        self.assertFalse(result[1]["has_conflict"])

    def test_owner_vs_tenant_is_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "water",
             "responsibility": "tenant", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        self.assertTrue(result[0]["has_conflict"])
        self.assertTrue(result[1]["has_conflict"])

    def test_three_sources_owner_owner_included_not_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "included_in_rent", "source_type": "listing_description",
             "source_note_id": None, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        for p in result:
            self.assertFalse(p["has_conflict"])

    def test_owner_owner_tenant_is_conflict(self):
        proposals = [
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 10, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "owner", "source_type": "note",
             "source_note_id": 20, "has_conflict": False, "conflict_note": ""},
            {"rentvine_unit_id": 1, "utility_type": "electric",
             "responsibility": "tenant", "source_type": "listing_description",
             "source_note_id": None, "has_conflict": False, "conflict_note": ""},
        ]
        result = detect_conflicts(proposals)
        for p in result:
            self.assertTrue(p["has_conflict"])


# ==========================================================================
# _compute_flag with conflict_class
# ==========================================================================

class ComputeFlagConflictClassTests(SimpleTestCase):

    def test_owner_vs_included_no_flag(self):
        """Two pending proposals: owner and included_in_rent should NOT flag."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("owner"), _make_proposal("included_in_rent")],
            [],
        )
        self.assertEqual(result["status"], "pend")
        self.assertFalse(result["flag"])

    def test_owner_vs_tenant_flags(self):
        """Two pending proposals: owner and tenant SHOULD flag."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("owner"), _make_proposal("tenant")],
            [],
        )
        self.assertEqual(result["status"], "pend")
        self.assertTrue(result["flag"])


# ==========================================================================
# recompute_conflict_for_group tests
# ==========================================================================

class RecomputeConflictForGroupTests(SimpleTestCase):

    def test_group_only_conflict_cleared(self):
        """owner vs included_in_rent group note cleared."""
        proposals = [
            {"id": 1, "responsibility": "owner", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: included_in_rent"},
            {"id": 2, "responsibility": "included_in_rent", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: included_in_rent"},
        ]
        results = recompute_conflict_for_group(proposals)
        for pk, flag, note in results:
            self.assertFalse(flag)
            self.assertEqual(note, "")

    def test_payer_phrase_kept_flag_stays(self):
        """Payer-phrase note is kept and has_conflict stays True."""
        proposals = [
            {"id": 1, "responsibility": "owner", "has_conflict": True,
             "conflict_note": "Quote says tenant/resident pays but row says owner"},
        ]
        results = recompute_conflict_for_group(proposals)
        pk, flag, note = results[0]
        self.assertTrue(flag)
        self.assertEqual(note, "Quote says tenant/resident pays but row says owner")

    def test_mixed_note_keeps_only_payer_piece(self):
        """Mixed note (payer + group) keeps only payer when group clears."""
        proposals = [
            {"id": 1, "responsibility": "owner", "has_conflict": True,
             "conflict_note": "Quote says tenant/resident pays but row says owner; water @ unit 10: note 1: owner vs note 2: included_in_rent"},
            {"id": 2, "responsibility": "included_in_rent", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: included_in_rent"},
        ]
        results = recompute_conflict_for_group(proposals)
        # Proposal 1: payer piece kept, group piece dropped
        pk1, flag1, note1 = results[0]
        self.assertTrue(flag1)
        self.assertEqual(note1, "Quote says tenant/resident pays but row says owner")
        # Proposal 2: group piece dropped, no payer piece
        pk2, flag2, note2 = results[1]
        self.assertFalse(flag2)
        self.assertEqual(note2, "")

    def test_real_conflict_stays(self):
        """owner vs tenant is still a real conflict — kept."""
        proposals = [
            {"id": 1, "responsibility": "owner", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: tenant"},
            {"id": 2, "responsibility": "tenant", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: tenant"},
        ]
        results = recompute_conflict_for_group(proposals)
        for pk, flag, note in results:
            self.assertTrue(flag)
            self.assertIn("owner vs note 2: tenant", note)

    def test_idempotent(self):
        """Running twice gives the same result."""
        proposals = [
            {"id": 1, "responsibility": "owner", "has_conflict": True,
             "conflict_note": "Quote says tenant/resident pays but row says owner; water @ unit 10: note 1: owner vs note 2: included_in_rent"},
            {"id": 2, "responsibility": "included_in_rent", "has_conflict": True,
             "conflict_note": "water @ unit 10: note 1: owner vs note 2: included_in_rent"},
        ]
        first = recompute_conflict_for_group(proposals)
        # Apply first pass results back
        updated = []
        for orig, (pk, flag, note) in zip(proposals, first):
            updated.append({**orig, "has_conflict": flag, "conflict_note": note})
        second = recompute_conflict_for_group(updated)
        self.assertEqual(first, second)


# ==========================================================================
# Blanket excluded columns tests
# ==========================================================================

class BlanketExcludedColumnsTests(SimpleTestCase):

    def test_oil_propane_stay_miss_with_blanket(self):
        """Oil and propane cells stay miss when only blanket rows exist."""
        units = [{"id": 1, "rentvine_id": 100, "display_address": "10 Main St", "unit_number": ""}]
        proposals_map = {100: {"other": [_make_pending_blanket()]}}
        rows = build_grid(units, {}, proposals_map, {}, {})
        row = rows[0]
        self.assertEqual(row["cells"]["oil"]["status"], "miss")
        self.assertEqual(row["cells"]["propane"]["status"], "miss")

    def test_other_columns_still_fill_from_blanket(self):
        """The other 6 columns still get pend from blanket."""
        units = [{"id": 1, "rentvine_id": 100, "display_address": "10 Main St", "unit_number": ""}]
        proposals_map = {100: {"other": [_make_pending_blanket()]}}
        rows = build_grid(units, {}, proposals_map, {}, {})
        row = rows[0]
        for col in GRID_COLUMNS:
            if col in BLANKET_EXCLUDED_COLUMNS:
                self.assertEqual(row["cells"][col]["status"], "miss", f"{col} should be miss")
            else:
                self.assertEqual(row["cells"][col]["status"], "pend", f"{col} should be pend")

    def test_panel_oil_drops_blanket(self):
        """prepare_panel_proposals for oil drops blanket rows."""
        blanket = _make_blanket()
        direct = {"utility_type": "oil", "responsibility": "tenant",
                   "evidence_quote": "Oil: tenant", "has_conflict": False, "basis": "stated"}
        proposals = [blanket, direct]
        result = prepare_panel_proposals(proposals, "oil")
        types = [p["utility_type"] for p in result]
        self.assertIn("oil", types)
        self.assertNotIn("other", types)

    def test_panel_electric_keeps_blanket(self):
        """prepare_panel_proposals for electric keeps blanket rows."""
        blanket = _make_blanket()
        proposals = [blanket]
        result = prepare_panel_proposals(proposals, "electric")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["is_blanket"])


# ==========================================================================
# Info status tests
# ==========================================================================

class InfoStatusTests(SimpleTestCase):

    def test_all_unknown_pending_gives_info(self):
        """All-unknown pending proposals produce info status."""
        result = compute_cell_status(
            "propane", None, None,
            [_make_proposal("unknown")], [],
        )
        self.assertEqual(result["status"], "info")
        self.assertEqual(result["label"], "Info only")
        self.assertFalse(result["flag"])
        self.assertFalse(result["blanket"])

    def test_real_row_beats_info(self):
        """A non-unknown pending proposal beats info."""
        result = compute_cell_status(
            "propane", None, None,
            [_make_proposal("tenant"), _make_proposal("unknown")], [],
        )
        self.assertEqual(result["status"], "pend")
        self.assertEqual(result["label"], "Tenant")

    def test_blanket_beats_info(self):
        """Blanket pending beats info (blanket comes before info in priority)."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("unknown")], [],
            blanket_pending=[_make_pending_blanket()],
        )
        self.assertEqual(result["status"], "pend")
        self.assertTrue(result["blanket"])

    def test_approved_beats_info(self):
        """Approved row beats info."""
        result = compute_cell_status(
            "propane", _make_approved("tenant"), None,
            [_make_proposal("unknown")], [],
        )
        self.assertEqual(result["status"], "ok")

    def test_info_not_has_gap(self):
        """Info cell is not a gap."""
        cells = {col: {"status": "ok", "label": "Tenant", "flag": False, "blanket": False, "inferred": False}
                 for col in GRID_COLUMNS}
        cells["propane"] = {"status": "info", "label": "Info only", "flag": False, "blanket": False}
        row = {"cells": cells}
        self.assertFalse(has_gap(row))

    def test_info_not_missing_oil_propane(self):
        """Info cell on propane is not missing_oil_propane."""
        cells = {col: {"status": "ok", "label": "Tenant", "flag": False, "blanket": False, "inferred": False}
                 for col in GRID_COLUMNS}
        cells["propane"] = {"status": "info", "label": "Info only", "flag": False, "blanket": False}
        row = {"cells": cells}
        self.assertFalse(missing_oil_propane(row))

    def test_info_counts_as_needs_review(self):
        """Info cell counts as needs_review."""
        cells = {col: {"status": "ok", "label": "Tenant", "flag": False, "blanket": False, "inferred": False}
                 for col in GRID_COLUMNS}
        cells["propane"] = {"status": "info", "label": "Info only", "flag": False, "blanket": False}
        row = {"cells": cells}
        self.assertTrue(needs_review(row))

    def test_info_not_complete(self):
        """Info cell means unit is not complete."""
        cells = {col: {"status": "ok", "label": "Tenant", "flag": False, "blanket": False, "inferred": False}
                 for col in GRID_COLUMNS}
        cells["propane"] = {"status": "info", "label": "Info only", "flag": False, "blanket": False}
        row = {"cells": cells}
        self.assertFalse(is_complete(row))

    def test_csv_text_info(self):
        """CSV text for info cell is 'Info only'."""
        cell = {"status": "info", "label": "Info only", "flag": False, "blanket": False}
        self.assertEqual(csv_cell_text(cell), "Info only")

    def test_info_from_alias_unknown(self):
        """Unknown proposals via alias still produce info."""
        result = compute_cell_status(
            "water", None, None,
            [], [_make_proposal("unknown")],
        )
        self.assertEqual(result["status"], "info")


# ==========================================================================
# Provider display map tests
# ==========================================================================

class ProviderDisplayMapTests(SimpleTestCase):

    def test_three_casings_collapse(self):
        """Three casings of City of Asheville collapse to mixed-case variant."""
        providers = (
            ["City of Asheville"] * 72
            + ["CITY OF ASHEVILLE"] * 45
            + ["city of asheville"] * 2
        )
        m = build_provider_display_map(providers)
        self.assertEqual(m["CITY OF ASHEVILLE"], "City of Asheville")
        self.assertEqual(m["city of asheville"], "City of Asheville")
        self.assertEqual(m["City of Asheville"], "City of Asheville")

    def test_all_caps_only_stays(self):
        """All-caps-only group stays as-is (never title-cased)."""
        providers = ["FCC ENVIRONMENTAL"] * 10
        m = build_provider_display_map(providers)
        self.assertEqual(m["FCC ENVIRONMENTAL"], "FCC ENVIRONMENTAL")

    def test_duke_alias(self):
        """'Duke' maps to the 'Duke Energy' group via alias."""
        providers = (
            ["Duke Energy"] * 57
            + ["DUKE ENERGY"] * 42
            + ["Duke"] * 10
        )
        m = build_provider_display_map(providers)
        self.assertEqual(m["Duke Energy"], "Duke Energy")
        self.assertEqual(m["DUKE ENERGY"], "Duke Energy")
        self.assertEqual(m["Duke"], "Duke Energy")

    def test_whitespace_collapse(self):
        """Internal whitespace is collapsed before grouping."""
        providers = ["  City   of   Asheville  "] * 5 + ["City of Asheville"] * 10
        m = build_provider_display_map(providers)
        self.assertEqual(m["City of Asheville"], "City of Asheville")
        self.assertEqual(m["City of Asheville"], "City of Asheville")

    def test_hoa_stays_uppercase(self):
        """All-caps 'HOA' stays 'HOA'."""
        providers = ["HOA"] * 5
        m = build_provider_display_map(providers)
        self.assertEqual(m["HOA"], "HOA")

    def test_empty_strings_excluded(self):
        """Empty strings produce no entries."""
        m = build_provider_display_map(["", "  ", "Duke Energy"])
        self.assertNotIn("", m)
        self.assertEqual(m["Duke Energy"], "Duke Energy")

    def test_realistic_full_set(self):
        """Full realistic frequency set confirms correct display names."""
        providers = (
            ["CITY OF ASHEVILLE"] * 45
            + ["City of Asheville"] * 72
            + ["city of asheville"] * 2
            + ["Duke Energy"] * 57
            + ["DUKE ENERGY"] * 42
            + ["Duke"] * 10
        )
        m = build_provider_display_map(providers)
        self.assertEqual(m["CITY OF ASHEVILLE"], "City of Asheville")
        self.assertEqual(m["DUKE ENERGY"], "Duke Energy")
        self.assertEqual(m["Duke"], "Duke Energy")


# ==========================================================================
# Column group tests
# ==========================================================================

class ColumnGroupTests(SimpleTestCase):

    def test_water_and_well_same_group(self):
        self.assertEqual(column_group("water"), column_group("well"))

    def test_sewer_and_septic_same_group(self):
        self.assertEqual(column_group("sewer"), column_group("septic"))

    def test_electric_is_own_group(self):
        self.assertEqual(column_group("electric"), "electric")

    def test_unknown_type_returns_itself(self):
        self.assertEqual(column_group("mystery"), "mystery")

    def test_all_grid_columns_have_groups(self):
        for col in GRID_COLUMNS:
            self.assertIsNotNone(column_group(col))


# ==========================================================================
# Siblings to supersede tests
# ==========================================================================

class SiblingsToSupersedeTests(SimpleTestCase):

    def test_water_supersedes_well(self):
        chosen = {"id": 1, "utility_type": "water"}
        pending = [
            {"id": 1, "utility_type": "water"},
            {"id": 2, "utility_type": "well"},
            {"id": 3, "utility_type": "electric"},
        ]
        self.assertEqual(siblings_to_supersede(chosen, pending), [2])

    def test_well_supersedes_water(self):
        chosen = {"id": 2, "utility_type": "well"}
        pending = [
            {"id": 1, "utility_type": "water"},
            {"id": 2, "utility_type": "well"},
        ]
        self.assertEqual(siblings_to_supersede(chosen, pending), [1])

    def test_sewer_supersedes_septic(self):
        chosen = {"id": 1, "utility_type": "sewer"}
        pending = [
            {"id": 1, "utility_type": "sewer"},
            {"id": 2, "utility_type": "septic"},
        ]
        self.assertEqual(siblings_to_supersede(chosen, pending), [2])

    def test_no_siblings_different_group(self):
        chosen = {"id": 1, "utility_type": "electric"}
        pending = [
            {"id": 1, "utility_type": "electric"},
            {"id": 2, "utility_type": "gas"},
        ]
        self.assertEqual(siblings_to_supersede(chosen, pending), [])

    def test_never_includes_self(self):
        chosen = {"id": 1, "utility_type": "water"}
        pending = [{"id": 1, "utility_type": "water"}]
        self.assertEqual(siblings_to_supersede(chosen, pending), [])

    def test_empty_pending(self):
        chosen = {"id": 1, "utility_type": "water"}
        self.assertEqual(siblings_to_supersede(chosen, []), [])


# ==========================================================================
# Approved rows to replace tests
# ==========================================================================

class ApprovedRowsToReplaceTests(SimpleTestCase):

    def test_saving_water_replaces_well(self):
        self.assertEqual(approved_rows_to_replace(["well", "electric"], "water"), ["well"])

    def test_saving_well_replaces_water(self):
        self.assertEqual(approved_rows_to_replace(["water", "gas"], "well"), ["water"])

    def test_saving_sewer_replaces_septic(self):
        self.assertEqual(approved_rows_to_replace(["septic"], "sewer"), ["septic"])

    def test_saving_electric_replaces_nothing(self):
        self.assertEqual(approved_rows_to_replace(["water", "gas"], "electric"), [])

    def test_same_type_not_replaced(self):
        """Saving water when water exists does not list water (update, not replace)."""
        self.assertEqual(approved_rows_to_replace(["water"], "water"), [])

    def test_empty_approved(self):
        self.assertEqual(approved_rows_to_replace([], "water"), [])


# ==========================================================================
# Check approve proposal tests
# ==========================================================================

class CheckApproveProposalTests(SimpleTestCase):

    def test_pending_tenant_ok(self):
        ok, err = check_approve_proposal({"status": "pending", "responsibility": "tenant"})
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_pending_owner_ok(self):
        ok, err = check_approve_proposal({"status": "pending", "responsibility": "owner"})
        self.assertTrue(ok)

    def test_pending_unknown_rejected(self):
        ok, err = check_approve_proposal({"status": "pending", "responsibility": "unknown"})
        self.assertFalse(ok)
        self.assertIn("unknown", err.lower())

    def test_approved_status_rejected(self):
        ok, err = check_approve_proposal({"status": "approved", "responsibility": "tenant"})
        self.assertFalse(ok)
        self.assertIn("no longer pending", err.lower())

    def test_rejected_status_rejected(self):
        ok, err = check_approve_proposal({"status": "rejected", "responsibility": "tenant"})
        self.assertFalse(ok)

    def test_included_in_rent_ok(self):
        ok, err = check_approve_proposal({"status": "pending", "responsibility": "included_in_rent"})
        self.assertTrue(ok)

    def test_not_applicable_ok(self):
        ok, err = check_approve_proposal({"status": "pending", "responsibility": "not_applicable"})
        self.assertTrue(ok)


# ==========================================================================
# Validate reject tests
# ==========================================================================

class ValidateRejectTests(SimpleTestCase):

    def test_empty_note_ok(self):
        ok, err = validate_reject("")
        self.assertTrue(ok)

    def test_none_note_ok(self):
        ok, err = validate_reject(None)
        self.assertTrue(ok)

    def test_short_note_ok(self):
        ok, err = validate_reject("Looks wrong, re-extract.")
        self.assertTrue(ok)

    def test_500_chars_ok(self):
        ok, err = validate_reject("x" * 500)
        self.assertTrue(ok)

    def test_501_chars_rejected(self):
        ok, err = validate_reject("x" * 501)
        self.assertFalse(ok)
        self.assertIn("500", err)

    def test_whitespace_trimmed(self):
        ok, err = validate_reject("   note   ")
        self.assertTrue(ok)


# ==========================================================================
# Validate manual answer tests
# ==========================================================================

class ValidateManualAnswerTests(SimpleTestCase):

    def test_valid_electric(self):
        ok, err, cleaned = validate_manual_answer(
            "electric", "electric", "tenant", "Duke Energy", {},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["utility_type"], "electric")
        self.assertEqual(cleaned["responsibility"], "tenant")
        self.assertEqual(cleaned["provider"], "Duke Energy")
        self.assertEqual(cleaned["extras"], {})

    def test_water_allows_well(self):
        ok, err, cleaned = validate_manual_answer(
            "water", "well", "owner", "", {},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["utility_type"], "well")

    def test_sewer_allows_septic(self):
        ok, err, cleaned = validate_manual_answer(
            "sewer", "septic", "tenant", "", {},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["utility_type"], "septic")

    def test_invalid_column(self):
        ok, err, cleaned = validate_manual_answer(
            "mystery", "mystery", "tenant", "", {},
        )
        self.assertFalse(ok)
        self.assertIn("Invalid column", err)

    def test_invalid_type_for_column(self):
        ok, err, cleaned = validate_manual_answer(
            "electric", "water", "tenant", "", {},
        )
        self.assertFalse(ok)
        self.assertIn("Invalid type", err)

    def test_invalid_responsibility(self):
        ok, err, cleaned = validate_manual_answer(
            "electric", "electric", "unknown", "", {},
        )
        self.assertFalse(ok)
        self.assertIn("Invalid responsibility", err)

    def test_provider_too_long(self):
        ok, err, cleaned = validate_manual_answer(
            "electric", "electric", "tenant", "x" * 201, {},
        )
        self.assertFalse(ok)
        self.assertIn("200", err)

    def test_oil_extras_valid(self):
        ok, err, cleaned = validate_manual_answer(
            "oil", "oil", "owner", "Blossman",
            {"tank_size": "275 gal", "tank_location": "basement"},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["extras"]["tank_size"], "275 gal")
        self.assertEqual(cleaned["extras"]["tank_location"], "basement")

    def test_oil_extras_too_long(self):
        ok, err, cleaned = validate_manual_answer(
            "oil", "oil", "owner", "",
            {"tank_size": "x" * 101, "tank_location": ""},
        )
        self.assertFalse(ok)
        self.assertIn("100", err)

    def test_non_oil_ignores_extras(self):
        ok, err, cleaned = validate_manual_answer(
            "electric", "electric", "tenant", "",
            {"tank_size": "275 gal"},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["extras"], {})

    def test_not_applicable_responsibility(self):
        ok, err, cleaned = validate_manual_answer(
            "gas", "gas", "not_applicable", "", {},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["responsibility"], "not_applicable")

    def test_empty_provider_ok(self):
        ok, err, cleaned = validate_manual_answer(
            "trash", "trash", "tenant", "", {},
        )
        self.assertTrue(ok)
        self.assertEqual(cleaned["provider"], "")


# ==========================================================================
# Blanket columns to fill tests
# ==========================================================================

class BlanketColumnsToFillTests(SimpleTestCase):

    def test_empty_unit_fills_six(self):
        """Empty unit fills all 6 non-excluded columns."""
        cols = blanket_columns_to_fill({}, {}, "tenant")
        self.assertEqual(len(cols), 6)
        self.assertNotIn("oil", cols)
        self.assertNotIn("propane", cols)

    def test_approved_column_skipped(self):
        """Column with approved row is skipped."""
        approved = {"electric": {}}
        cols = blanket_columns_to_fill(approved, {}, "tenant")
        self.assertNotIn("electric", cols)

    def test_approved_alias_skips_group(self):
        """Approved well skips the water column."""
        approved = {"well": {}}
        cols = blanket_columns_to_fill(approved, {}, "tenant")
        self.assertNotIn("water", cols)

    def test_pending_non_unknown_skips(self):
        """Column with pending non-unknown proposal is skipped."""
        pending = {"electric": [{"responsibility": "tenant"}]}
        cols = blanket_columns_to_fill({}, pending, "tenant")
        self.assertNotIn("electric", cols)

    def test_pending_unknown_does_not_skip(self):
        """Column with only unknown pending is NOT skipped."""
        pending = {"electric": [{"responsibility": "unknown"}]}
        cols = blanket_columns_to_fill({}, pending, "tenant")
        self.assertIn("electric", cols)

    def test_fully_approved_unit(self):
        """Fully approved unit fills nothing."""
        approved = {col: {} for col in GRID_COLUMNS}
        cols = blanket_columns_to_fill(approved, {}, "tenant")
        self.assertEqual(cols, [])


# ==========================================================================
# Flag split tests
# ==========================================================================

class FlagSplitTests(SimpleTestCase):

    def test_real_conflict_flagged(self):
        """A proposal with has_conflict=True produces flag=True."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", has_conflict=True)], [],
        )
        self.assertTrue(result["flag"])
        self.assertFalse(result["inferred"])

    def test_disagreement_flagged(self):
        """Two proposals with different responsibilities produce flag=True."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant"), _make_proposal("owner")], [],
        )
        self.assertTrue(result["flag"])

    def test_catch_all_inferred_not_flagged(self):
        """A single catch_all proposal: flag=False, inferred=True."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", basis="catch_all")], [],
        )
        self.assertFalse(result["flag"])
        self.assertTrue(result["inferred"])

    def test_blanket_inferred_not_flagged(self):
        """A blanket cell: flag=False, inferred=True."""
        result = compute_cell_status(
            "electric", None, None, [], [],
            blanket_pending=[_make_blanket()],
        )
        self.assertFalse(result["flag"])
        self.assertTrue(result["inferred"])
        self.assertTrue(result["blanket"])

    def test_plain_stated_neither(self):
        """A single stated proposal: flag=False, inferred=False."""
        result = compute_cell_status(
            "electric", None, None,
            [_make_proposal("tenant", basis="stated")], [],
        )
        self.assertFalse(result["flag"])
        self.assertFalse(result["inferred"])

    def test_approved_no_inferred(self):
        """Approved cells never have inferred=True."""
        result = compute_cell_status("electric", _make_approved("tenant"), None, [], [])
        self.assertFalse(result["inferred"])

    def test_miss_no_inferred(self):
        """Miss cells have inferred=False."""
        result = compute_cell_status("electric", None, None, [], [])
        self.assertFalse(result["inferred"])

    def test_csv_inferred_suffix(self):
        """CSV text for an inferred (catch_all) cell gets (inferred) suffix."""
        cell = {"status": "pend", "label": "Tenant", "flag": False,
                "blanket": False, "inferred": True}
        self.assertEqual(csv_cell_text(cell), "Needs review: Tenant (inferred)")

    def test_csv_blanket_beats_inferred(self):
        """CSV text for blanket+inferred uses (blanket), not (inferred)."""
        cell = {"status": "pend", "label": "Tenant", "flag": False,
                "blanket": True, "inferred": True}
        self.assertEqual(csv_cell_text(cell), "Needs review: Tenant (blanket)")


# ==========================================================================
# Operations role tests
# ==========================================================================

from accounts.models import User


class OperationsRoleTests(SimpleTestCase):

    def test_operations_can_access_utilities(self):
        self.assertIn("utilities", User.ROLE_PRODUCT_ACCESS[User.Role.OPERATIONS])

    def test_operations_cannot_access_leasing(self):
        self.assertNotIn("leasing", User.ROLE_PRODUCT_ACCESS[User.Role.OPERATIONS])

    def test_operations_cannot_access_maintenance(self):
        self.assertNotIn("maintenance", User.ROLE_PRODUCT_ACCESS[User.Role.OPERATIONS])

    def test_operations_only_has_utilities(self):
        self.assertEqual(User.ROLE_PRODUCT_ACCESS[User.Role.OPERATIONS], {"utilities"})

    def test_leasing_cannot_access_utilities(self):
        self.assertNotIn("utilities", User.ROLE_PRODUCT_ACCESS[User.Role.LEASING])

    def test_maintenance_cannot_access_utilities(self):
        self.assertNotIn("utilities", User.ROLE_PRODUCT_ACCESS[User.Role.MAINTENANCE])

    def test_accounting_cannot_access_utilities(self):
        self.assertNotIn("utilities", User.ROLE_PRODUCT_ACCESS[User.Role.ACCOUNTING])

    def test_admin_can_access_utilities(self):
        self.assertIn("utilities", User.ROLE_PRODUCT_ACCESS[User.Role.ADMIN])

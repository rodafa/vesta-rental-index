"""
Integration tests for keys.loader — pytest-django, real test DB.

Fixtures use the 28 Bass Ln shape: 1 property, 3 units (1, 2, 3).
"""

import pytest

from core.models import Property, Unit
from keys.loader import load_access_notes, load_tags
from keys.models import AccessNote, KeyChange, Tag
from keys.services import build_unit_address_index


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bass_property(db):
    """Property: 28 Bass Ln, rentvine_id=100."""
    return Property.objects.create(
        rentvine_id=100,
        address_line_1="28 Bass Ln",
        name="28 Bass Ln",
    )


@pytest.fixture
def bass_units(bass_property):
    """Three units at 28 Bass Ln: unit 1, 2, 3."""
    units = []
    for i in range(1, 4):
        units.append(
            Unit.objects.create(
                rentvine_id=200 + i,
                rentengine_id=5000 + i,
                property=bass_property,
                unit_number=str(i),
                address_line_1="28 Bass Ln",
            )
        )
    return units


@pytest.fixture
def units_by_addr(bass_units):
    """Address index built from bass_units."""
    return build_unit_address_index(bass_units)


def _make_tag_row(box, number, address, notes="", key_with="",
                  row_type="property"):
    """Build a tag_row dict matching parse_key_box_csv output."""
    return {
        "key_id": f"{box}-{number:03d}",
        "tag_number": number,
        "address": address,
        "notes": notes,
        "key_with": key_with,
        "row_type": row_type,
        "box_letter": box,
    }


# ---------------------------------------------------------------------------
# TestLoadTags
# ---------------------------------------------------------------------------


class TestLoadTags:
    def test_create_unit_match(self, bass_units, units_by_addr):
        """Tag matched at unit level sets unit FK, not property FK."""
        rows = [_make_tag_row("A", 1, "28 Bass Ln Unit 1")]
        stats = load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-001")
        assert tag.unit == bass_units[0]
        assert tag.property is None
        assert stats["created"] == 1
        assert stats["orphan"] == 0

    def test_create_property_match(self, bass_units, units_by_addr):
        """Tag matched at property level sets property FK, not unit FK."""
        # No unit token — multi-unit address → property-level match
        rows = [_make_tag_row("A", 2, "28 Bass Ln")]
        stats = load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-002")
        assert tag.unit is None
        assert tag.property == bass_units[0].property
        assert stats["created"] == 1

    def test_create_orphan(self, bass_units, units_by_addr):
        """Unmatched tag gets unit=None, property=None."""
        rows = [_make_tag_row("A", 3, "999 Nowhere St")]
        stats = load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-003")
        assert tag.unit is None
        assert tag.property is None
        assert stats["created"] == 1
        assert stats["orphan"] == 1

    def test_non_property_row_is_orphan(self, bass_units, units_by_addr):
        """non_property row (e.g. SMART BLANKS) becomes orphan."""
        rows = [_make_tag_row("B", 1, "SMART BLANKS", row_type="non_property")]
        stats = load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="B-001")
        assert tag.unit is None
        assert tag.property is None
        assert stats["orphan"] == 1

    def test_ring_notes_joined(self, bass_units, units_by_addr):
        """ring_notes = 'notes | key_with' with empties skipped."""
        rows = [_make_tag_row("A", 4, "28 Bass Ln Unit 2",
                              notes="Schlage front", key_with="Kwikset back")]
        load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-004")
        assert tag.ring_notes == "Schlage front | Kwikset back"

    def test_ring_notes_only_notes(self, bass_units, units_by_addr):
        """key_with empty → ring_notes has no trailing separator."""
        rows = [_make_tag_row("A", 5, "28 Bass Ln Unit 2", notes="Schlage")]
        load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-005")
        assert tag.ring_notes == "Schlage"

    def test_idempotent_second_run(self, bass_units, units_by_addr):
        """Second identical run: all unchanged, no new KeyChange rows."""
        rows = [_make_tag_row("A", 1, "28 Bass Ln Unit 1", notes="front key")]
        load_tags(rows, units_by_addr)
        changes_after_first = KeyChange.objects.count()

        stats = load_tags(rows, units_by_addr)
        assert stats["unchanged"] == 1
        assert stats["created"] == 0
        assert stats["updated"] == 0
        assert KeyChange.objects.count() == changes_after_first

    def test_skipped_edited_guard(self, bass_units, units_by_addr):
        """Tag with a non-import KeyChange is skipped entirely."""
        rows = [_make_tag_row("A", 6, "28 Bass Ln Unit 3")]
        load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-006")
        # Simulate a human edit
        KeyChange.objects.create(
            tag=tag, field="status", old_value="on_hook",
            new_value="checked_out", changed_by="U12345",
        )

        # Change the sheet data — should still be skipped
        rows2 = [_make_tag_row("A", 6, "28 Bass Ln Unit 3", notes="new note")]
        stats = load_tags(rows2, units_by_addr)
        assert stats["skipped_edited"] == 1
        assert stats["updated"] == 0

        tag.refresh_from_db()
        assert tag.ring_notes == ""  # unchanged

    def test_skipped_edited_not_counted_as_orphan(self, bass_units, units_by_addr):
        """Skipped-edited tags are not double-counted as orphan."""
        rows = [_make_tag_row("A", 7, "999 Nowhere St")]
        load_tags(rows, units_by_addr)

        tag = Tag.objects.get(key_id="A-007")
        KeyChange.objects.create(
            tag=tag, field="ring_notes", old_value="",
            new_value="edited", changed_by="U99",
        )

        stats = load_tags(rows, units_by_addr)
        assert stats["skipped_edited"] == 1
        assert stats["orphan"] == 0

    def test_update_changed_fields(self, bass_units, units_by_addr):
        """Changed fields produce one KeyChange each with old/new values."""
        rows = [_make_tag_row("A", 8, "28 Bass Ln Unit 1", notes="old note")]
        load_tags(rows, units_by_addr)

        rows2 = [_make_tag_row("A", 8, "28 Bass Ln Unit 2", notes="new note")]
        stats = load_tags(rows2, units_by_addr)
        assert stats["updated"] == 1

        changes = list(
            KeyChange.objects.filter(tag__key_id="A-008")
            .exclude(field="created")
            .order_by("field")
        )
        fields = {c.field for c in changes}
        assert "unit" in fields
        assert "ring_notes" in fields

    def test_update_keychange_human_readable(self, bass_units, units_by_addr):
        """KeyChange for unit/property fields stores display strings, not PKs."""
        rows = [_make_tag_row("A", 9, "28 Bass Ln Unit 1")]
        load_tags(rows, units_by_addr)

        rows2 = [_make_tag_row("A", 9, "28 Bass Ln Unit 2")]
        load_tags(rows2, units_by_addr)

        change = KeyChange.objects.get(tag__key_id="A-009", field="unit")
        assert change.old_value == "28 Bass Ln 1"
        assert change.new_value == "28 Bass Ln 2"

    def test_filtered_run_out_of_scope(self, bass_units, units_by_addr):
        """Filtered run: tag for out-of-scope address is NOT created."""
        rows = [
            _make_tag_row("A", 10, "28 Bass Ln Unit 1"),
            _make_tag_row("A", 11, "52 Buffalo Ave"),
        ]
        stats = load_tags(rows, units_by_addr, property_filter="28 Bass")

        assert Tag.objects.filter(key_id="A-010").exists()
        assert not Tag.objects.filter(key_id="A-011").exists()
        assert stats["created"] == 1
        assert stats["out_of_scope"] == 1


# ---------------------------------------------------------------------------
# TestLoadAccessNotes
# ---------------------------------------------------------------------------


class TestLoadAccessNotes:
    def test_rentvine_unit_note(self, bass_units):
        """Unit-level Rentvine note creates AccessNote with unit FK."""
        unit = bass_units[0]
        unit_notes = {
            unit.rentvine_id: [
                {"note_id": 9001, "date": "2026-10-15", "text": "Lockbox code 1234"},
            ],
        }
        stats = load_access_notes(bass_units, unit_notes, {}, {})

        note = AccessNote.objects.get(source="rentvine_unit_note")
        assert note.unit == unit
        assert note.property is None
        assert note.source_ref == "9001"
        assert note.text == "Lockbox code 1234"
        assert str(note.source_date) == "2026-10-15"
        assert stats["created"] == 1

    def test_rentvine_property_note_stored_once(self, bass_units):
        """Property-level note stored once with unit=None, property FK."""
        prop = bass_units[0].property
        property_notes = {
            prop.rentvine_id: [
                {"note_id": 8001, "date": "2026-09-01", "text": "Master key in office"},
            ],
        }
        stats = load_access_notes(bass_units, {}, property_notes, {})

        notes = AccessNote.objects.filter(source="rentvine_property_note")
        assert notes.count() == 1

        note = notes.first()
        assert note.unit is None
        assert note.property == prop
        assert note.source_ref == "8001"
        assert stats["created"] == 1

    def test_rentengine_internal_notes(self, bass_units):
        """Non-empty RE internal_notes creates AccessNote."""
        unit = bass_units[0]
        re_index = {
            str(unit.rentengine_id): {
                "id": unit.rentengine_id,
                "key_access": "",
                "structured_access_instructions": {
                    "internal_notes": "Code is 1776",
                },
            },
        }
        stats = load_access_notes(bass_units, {}, {}, re_index)

        note = AccessNote.objects.get(source="rentengine_internal_notes")
        assert note.unit == unit
        assert note.property is None
        assert note.text == "Code is 1776"
        assert note.source_ref == str(unit.rentengine_id)
        assert note.source_date is None
        assert stats["created"] == 1

    def test_rentengine_key_access(self, bass_units):
        """Non-empty RE key_access creates AccessNote."""
        unit = bass_units[1]
        re_index = {
            str(unit.rentengine_id): {
                "id": unit.rentengine_id,
                "key_access": "Lockbox",
                "structured_access_instructions": {},
            },
        }
        stats = load_access_notes(bass_units, {}, {}, re_index)

        note = AccessNote.objects.get(source="rentengine_key_access")
        assert note.unit == unit
        assert note.property is None
        assert note.text == "Lockbox"
        assert note.source_date is None
        assert stats["created"] == 1

    def test_rentengine_empty_skipped(self, bass_units):
        """Empty/whitespace RE fields create no AccessNote."""
        unit = bass_units[0]
        re_index = {
            str(unit.rentengine_id): {
                "id": unit.rentengine_id,
                "key_access": "  ",
                "structured_access_instructions": {
                    "internal_notes": "",
                },
            },
        }
        stats = load_access_notes(bass_units, {}, {}, re_index)
        assert AccessNote.objects.count() == 0
        assert stats["created"] == 0

    def test_idempotent_second_run(self, bass_units):
        """Second run: all unchanged, no new AccessNote rows."""
        unit = bass_units[0]
        unit_notes = {
            unit.rentvine_id: [
                {"note_id": 9002, "date": "2026-10-15", "text": "Lockbox rear"},
            ],
        }
        re_index = {
            str(unit.rentengine_id): {
                "id": unit.rentengine_id,
                "key_access": "Lockbox",
                "structured_access_instructions": {
                    "internal_notes": "Code 5555",
                },
            },
        }
        load_access_notes(bass_units, unit_notes, {}, re_index)
        count_after_first = AccessNote.objects.count()

        stats = load_access_notes(bass_units, unit_notes, {}, re_index)
        assert AccessNote.objects.count() == count_after_first
        assert stats["unchanged"] == 3  # unit note + internal + key_access
        assert stats["created"] == 0
        assert stats["updated"] == 0

    def test_update_changed_text(self, bass_units):
        """Changed text on re-import counts as updated."""
        unit = bass_units[0]
        unit_notes_v1 = {
            unit.rentvine_id: [
                {"note_id": 9003, "date": "2026-10-15", "text": "Old text"},
            ],
        }
        load_access_notes(bass_units, unit_notes_v1, {}, {})

        unit_notes_v2 = {
            unit.rentvine_id: [
                {"note_id": 9003, "date": "2026-10-15", "text": "New text"},
            ],
        }
        stats = load_access_notes(bass_units, unit_notes_v2, {}, {})
        assert stats["updated"] == 1

        note = AccessNote.objects.get(source_ref="9003")
        assert note.text == "New text"

    def test_not_both_constraint(self, bass_units):
        """Unit-level notes have property=None; property-level have unit=None."""
        unit = bass_units[0]
        prop = unit.property

        unit_notes = {
            unit.rentvine_id: [
                {"note_id": 9010, "date": "2026-11-01", "text": "unit note"},
            ],
        }
        property_notes = {
            prop.rentvine_id: [
                {"note_id": 8010, "date": "2026-11-01", "text": "prop note"},
            ],
        }
        load_access_notes(bass_units, unit_notes, property_notes, {})

        u_note = AccessNote.objects.get(source="rentvine_unit_note")
        assert u_note.unit is not None
        assert u_note.property is None

        p_note = AccessNote.objects.get(source="rentvine_property_note")
        assert p_note.unit is None
        assert p_note.property is not None


# ---------------------------------------------------------------------------
# TestDryRun
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_leaves_no_rows(self, bass_units, units_by_addr):
        """Dry-run (transaction rollback) leaves Tag/AccessNote/KeyChange at 0."""
        from django.db import transaction

        tag_rows = [_make_tag_row("A", 1, "28 Bass Ln Unit 1")]
        unit_notes = {
            bass_units[0].rentvine_id: [
                {"note_id": 7001, "date": "2026-10-01", "text": "Lockbox"},
            ],
        }

        with transaction.atomic():
            tag_stats = load_tags(tag_rows, units_by_addr)
            note_stats = load_access_notes(
                bass_units, unit_notes, {}, {},
            )
            transaction.set_rollback(True)

        assert tag_stats["created"] == 1
        assert note_stats["created"] == 1

        # Nothing persisted
        assert Tag.objects.count() == 0
        assert AccessNote.objects.count() == 0
        assert KeyChange.objects.count() == 0

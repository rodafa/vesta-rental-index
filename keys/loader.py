"""
Write functions for the keys_load command.

Only place that writes to Tag, AccessNote, and KeyChange.
"""

import logging
from datetime import date

from keys.models import AccessNote, KeyChange, Tag
from keys.services import match_tag_to_unit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join_ring_notes(tag_row):
    """Join sheet 'notes' and 'key_with' with ' | ', skipping empties."""
    parts = []
    notes = tag_row.get("notes", "").strip()
    key_with = tag_row.get("key_with", "").strip()
    if notes:
        parts.append(notes)
    if key_with:
        parts.append(key_with)
    return " | ".join(parts)


def _has_human_edits(tag):
    """True if any KeyChange on this tag was made by someone other than 'import'."""
    return tag.changes.exclude(changed_by="import").exists()


def _unit_display(unit):
    """Human-readable unit label: 'address unitnumber'."""
    if unit is None:
        return ""
    addr = unit.property.address_line_1 or ""
    num = unit.unit_number or ""
    return f"{addr} {num}".strip()


def _property_display(prop):
    """Human-readable property label."""
    if prop is None:
        return ""
    return prop.address_line_1 or ""


def _log_change(tag, unit, field, old, new):
    """Append a KeyChange row."""
    KeyChange.objects.create(
        tag=tag,
        unit=unit,
        field=field,
        old_value=str(old) if old is not None else "",
        new_value=str(new) if new is not None else "",
        changed_by="import",
    )


def _parse_date(val):
    """Parse 'YYYY-MM-DD' or datetime string to date, or None."""
    if not val:
        return None
    try:
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def _upsert_access_note(*, unit, prop, source, source_ref, text, source_date):
    """Create or update an AccessNote by its natural key.

    Returns 'created' | 'updated' | 'unchanged'.
    """
    # Lookup matches the DB constraints:
    #   unit-level     → (source, source_ref, unit)     where unit IS NOT NULL
    #   property-level → (source, source_ref, property)  where unit IS NULL
    if unit is not None:
        lookup = {"source": source, "source_ref": source_ref, "unit": unit}
    else:
        lookup = {"source": source, "source_ref": source_ref, "property": prop}

    try:
        existing = AccessNote.objects.get(**lookup)
    except AccessNote.DoesNotExist:
        AccessNote.objects.create(
            unit=unit,
            property=prop,
            source=source,
            source_ref=source_ref,
            text=text,
            source_date=source_date,
        )
        return "created"

    changed = False
    if existing.text != text:
        existing.text = text
        changed = True
    if existing.source_date != source_date:
        existing.source_date = source_date
        changed = True

    if changed:
        existing.save(update_fields=["text", "source_date"])
        return "updated"
    return "unchanged"


# ---------------------------------------------------------------------------
# load_tags
# ---------------------------------------------------------------------------


def load_tags(tag_rows, units_by_addr, property_filter=None):
    """Create/update Tag rows from parsed sheet rows. Log KeyChange for every mutation.

    Args:
        tag_rows: list of dicts from parse_key_box_csv (both boxes).
        units_by_addr: index from build_unit_address_index.
        property_filter: when set, only load tags whose matched
            unit/property is in scope or whose raw address contains
            the filter text.  Tags that match neither are counted as
            out_of_scope and skipped entirely.

    Returns dict with keys:
        created, updated, unchanged, skipped_edited, orphan, out_of_scope.
    """
    stats = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_edited": 0,
        "orphan": 0,
        "out_of_scope": 0,
    }

    for tag_row in tag_rows:
        box = tag_row["box_letter"]
        number = tag_row["tag_number"]
        ring_notes = _join_ring_notes(tag_row)
        raw_address = tag_row["address"]

        # --- Match to unit/property ---
        if tag_row["row_type"] == "property":
            unit, prop, level = match_tag_to_unit(tag_row, units_by_addr)
        else:
            # non_property rows (e.g. "SMART BLANKS") — orphan
            unit, prop, level = None, None, "none"

        # --- Property filter scoping ---
        if property_filter:
            in_scope = False
            if level == "unit" and unit is not None:
                addr = unit.property.address_line_1 or ""
                in_scope = property_filter.lower() in addr.lower()
            elif level == "property" and prop is not None:
                addr = prop.address_line_1 or ""
                in_scope = property_filter.lower() in addr.lower()
            else:
                # Unmatched — check raw sheet address
                in_scope = property_filter.lower() in raw_address.lower()

            if not in_scope:
                stats["out_of_scope"] += 1
                continue

        # --- Determine FK values ---
        if level == "unit" and unit is not None:
            tag_unit = unit
            tag_property = None
        elif level == "property" and prop is not None:
            tag_unit = None
            tag_property = prop
        else:
            tag_unit = None
            tag_property = None

        is_orphan = tag_unit is None and tag_property is None

        # --- Get or create ---
        try:
            existing = Tag.objects.get(box=box, number=number)
        except Tag.DoesNotExist:
            existing = None

        if existing is not None:
            # --- Skip if human-edited ---
            if _has_human_edits(existing):
                stats["skipped_edited"] += 1
                continue

            if is_orphan:
                stats["orphan"] += 1

            # --- Update changed fields ---
            changes = []
            if existing.unit_id != (tag_unit.pk if tag_unit else None):
                old_val = _unit_display(existing.unit)
                new_val = _unit_display(tag_unit)
                changes.append(("unit", old_val, new_val))
                existing.unit = tag_unit

            if existing.property_id != (tag_property.pk if tag_property else None):
                old_val = _property_display(existing.property)
                new_val = _property_display(tag_property)
                changes.append(("property", old_val, new_val))
                existing.property = tag_property

            if existing.ring_notes != ring_notes:
                changes.append(("ring_notes", existing.ring_notes, ring_notes))
                existing.ring_notes = ring_notes

            if existing.imported_address_raw != raw_address:
                changes.append(("imported_address_raw", existing.imported_address_raw, raw_address))
                existing.imported_address_raw = raw_address

            if changes:
                existing.save()
                for field, old, new in changes:
                    _log_change(existing, tag_unit, field, old, new)
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        else:
            if is_orphan:
                stats["orphan"] += 1

            tag = Tag(
                box=box,
                number=number,
                unit=tag_unit,
                property=tag_property,
                ring_notes=ring_notes,
                imported_address_raw=raw_address,
            )
            tag.save()  # save() sets key_id

            summary = f"{tag.key_id} → {raw_address}"
            _log_change(tag, tag_unit, "created", "", summary)
            stats["created"] += 1

    return stats


# ---------------------------------------------------------------------------
# load_access_notes
# ---------------------------------------------------------------------------


def load_access_notes(db_units, unit_notes, property_notes, re_index):
    """Create/update AccessNote rows from Rentvine notes and RentEngine data.

    Args:
        db_units: list of Unit objects (with select_related property).
        unit_notes:     dict[rentvine_unit_id     → list[{note_id, date, text}]]
        property_notes: dict[rentvine_property_id → list[{note_id, date, text}]]
        re_index:       dict[str(rentengine_id)   → raw RE unit dict]

    Returns dict with keys: created, updated, unchanged.
    """
    stats = {"created": 0, "updated": 0, "unchanged": 0}

    # --- Rentvine unit-level notes ---
    for unit in db_units:
        uid = unit.rentvine_id
        for note in unit_notes.get(uid, []):
            result = _upsert_access_note(
                unit=unit,
                prop=None,
                source="rentvine_unit_note",
                source_ref=str(note["note_id"]),
                text=note["text"],
                source_date=_parse_date(note.get("date")),
            )
            stats[result] += 1

    # --- Rentvine property-level notes (once per property, unit=NULL) ---
    seen_property_ids = set()
    for unit in db_units:
        prop = unit.property
        prop_id = prop.rentvine_id
        if prop_id is None or prop_id in seen_property_ids:
            continue
        seen_property_ids.add(prop_id)

        for note in property_notes.get(prop_id, []):
            result = _upsert_access_note(
                unit=None,
                prop=prop,
                source="rentvine_property_note",
                source_ref=str(note["note_id"]),
                text=note["text"],
                source_date=_parse_date(note.get("date")),
            )
            stats[result] += 1

    # --- RentEngine notes (per unit, source_date=None) ---
    for unit in db_units:
        if not unit.rentengine_id:
            continue
        re_raw = re_index.get(str(unit.rentengine_id))
        if not re_raw:
            continue

        re_id_str = str(re_raw["id"])

        # internal_notes
        sai = re_raw.get("structured_access_instructions") or {}
        internal_notes = (sai.get("internal_notes") or "").strip()
        if internal_notes:
            result = _upsert_access_note(
                unit=unit,
                prop=None,
                source="rentengine_internal_notes",
                source_ref=re_id_str,
                text=internal_notes,
                source_date=None,
            )
            stats[result] += 1

        # key_access
        key_access = (re_raw.get("key_access") or "").strip()
        if key_access:
            result = _upsert_access_note(
                unit=unit,
                prop=None,
                source="rentengine_key_access",
                source_ref=re_id_str,
                text=key_access,
                source_date=None,
            )
            stats[result] += 1

    return stats

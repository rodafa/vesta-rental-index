"""Read-only DB lookups for KeyBot Slack replies. No writes."""

import logging
import re

from django.db.models import F, Q

from core.models import Unit
from keys.models import AccessNote, Checkout, Tag
from keys.services import (
    build_unit_address_index,
    normalize_address,
    normalize_unit_token,
)

logger = logging.getLogger(__name__)

# Words stripped from Slack queries before address parsing.
_FILLER_WORDS = frozenset({
    "what", "whats", "what's", "what\u2019s", "is", "the", "key", "keys",
    "lockbox", "code", "number", "for", "at", "please",
})

# Fullmatch for loose key-ID input: A-038, a38, a 038, B-1, etc.
_KEY_ID_RE = re.compile(r"[ABab][\s\-]?\d{1,3}")

# Curly quotes → straight quotes.
_CURLY_QUOTES = str.maketrans("\u2018\u2019\u201c\u201d", "''\"\"")

_SOURCE_DISPLAY = {
    "rentvine_unit_note": "Rentvine",
    "rentvine_property_note": "Rentvine",
    "rentengine_internal_notes": "RentEngine",
    "rentengine_key_access": "RentEngine",
    "keybot": "KeyBot",
}


def _fmt_date(d):
    """Format date or datetime as 'Oct 22, 2025'."""
    return f"{d:%b} {d.day}, {d:%Y}"


def _strip_filler(text: str) -> str:
    """Remove filler words and punctuation from a Slack query."""
    text = text.translate(_CURLY_QUOTES)
    kept = []
    for w in text.split():
        clean = w.lower().rstrip("?.,!")
        if clean in _FILLER_WORDS or not clean:
            continue
        kept.append(w.rstrip("?"))
    return " ".join(kept)


def _normalize_key_id(text: str) -> str | None:
    """Parse loose key-ID input → canonical 'A-038' form, or None."""
    text = text.strip()
    if not _KEY_ID_RE.fullmatch(text):
        return None
    box = text[0].upper()
    num = int(re.search(r"\d+", text).group())
    if num < 1 or num > 200:
        return None
    return f"{box}-{num:03d}"


_WHATS_OUT_RE = re.compile(
    r"what(?:'?s|\u2019s)?\s+out\b", re.IGNORECASE,
)

_TAKE_RE = re.compile(
    r"(?:take|checkout|check\s*out)\s+(.+)", re.IGNORECASE,
)

_RETURN_RE = re.compile(
    r"(?:return|bring\s*back)\s+(.+)", re.IGNORECASE,
)


def parse_query(text: str) -> dict:
    """Classify stripped mention text into {type, value}.

    Types: 'help', 'key_id', 'take', 'return', 'whats_out', 'address', 'empty'.
    """
    text = text.strip()
    if not text:
        return {"type": "empty", "value": ""}
    if text.lower().rstrip("?.,!") in ("help", "?", "", "hi", "hello", "hey"):
        return {"type": "help", "value": ""}

    # "whats out" / "what's out"
    if _WHATS_OUT_RE.fullmatch(text.rstrip("?.,! ")):
        return {"type": "whats_out", "value": ""}

    # "take A-038" / "checkout A38"
    m = _TAKE_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {"type": "take", "value": key_id}

    # "return A-038"
    m = _RETURN_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {"type": "return", "value": key_id}

    # Bare key ID
    key_id = _normalize_key_id(text)
    if key_id is not None:
        return {"type": "key_id", "value": key_id}
    return {"type": "address", "value": text}


def _narrow_by_token(candidates, unit_token):
    """Filter candidates by unit token. Returns (units, was_narrowed)."""
    if not unit_token:
        return candidates, False
    normalized = normalize_unit_token(unit_token)
    narrowed = [
        u for u in candidates
        if normalize_unit_token(u.unit_number) == normalized
    ]
    if narrowed:
        return narrowed, True
    return candidates, False


def find_units_by_street(base_name: str, index: dict) -> list:
    """Units whose normalized base_name equals or prefix-matches the query.

    Exact base_name equality always matches.  Prefix match (one starts
    with the other) requires both strings to be >= 4 chars.
    """
    if not base_name:
        return []
    matches = []
    for (_num, base), units in index.items():
        if base == base_name:
            matches.extend(units)
        elif len(base) >= 4 and len(base_name) >= 4:
            if base.startswith(base_name) or base_name.startswith(base):
                matches.extend(units)
    return matches


def find_units(query_text: str) -> dict:
    """Address lookup → {"units": [...], "exact": bool, "narrowed_by_token": bool}."""
    cleaned = _strip_filler(query_text)
    street_number, base_name, unit_token = normalize_address(cleaned)
    if not base_name:
        return {"units": [], "exact": True, "narrowed_by_token": False}

    db_units = list(
        Unit.objects.filter(rentvine_id__isnull=False)
        .select_related("property")
    )
    index = build_unit_address_index(db_units)

    # --- exact path: query has a street number ---
    if street_number:
        candidates = index.get((street_number, base_name), [])
        if candidates:
            units, by_token = _narrow_by_token(candidates, unit_token)
            return {"units": units, "exact": True, "narrowed_by_token": by_token}
        # No exact (number, base) match.
        # Check if the street number matches a unit number on that street
        # (e.g. "293 Cosmos" → unit 293 at 297 Cosmos Drive).
        street_units = find_units_by_street(base_name, index)
        unit_num_match = [
            u for u in street_units
            if normalize_unit_token(u.unit_number) == street_number
        ]
        if unit_num_match:
            return {"units": unit_num_match, "exact": True, "narrowed_by_token": False}

    # --- street-name fallback ---
    candidates = find_units_by_street(base_name, index)
    if not candidates:
        return {"units": [], "exact": False, "narrowed_by_token": False}
    units, by_token = _narrow_by_token(candidates, unit_token)
    return {"units": units, "exact": False, "narrowed_by_token": by_token}


def find_tag_by_key_id(text: str):
    """Normalize text to key_id form, query Tag table. Returns Tag or None."""
    key_id = _normalize_key_id(text)
    if key_id is None:
        return None
    try:
        return Tag.objects.select_related(
            "unit", "unit__property", "property"
        ).get(key_id=key_id)
    except Tag.DoesNotExist:
        return None


def unit_key_info(unit) -> dict:
    """Gather tags, checkouts, and access notes for one unit as a plain dict."""
    prop = unit.property

    # Active tags on this unit
    unit_tags = list(
        Tag.objects.filter(unit=unit)
        .exclude(status="retired")
        .order_by("box", "number")
    )

    # Property-level tags
    prop_tags = list(
        Tag.objects.filter(property=prop)
        .exclude(status="retired")
        .order_by("box", "number")
    )

    # Open checkouts for all relevant tags
    all_tag_ids = [t.pk for t in unit_tags + prop_tags]
    open_checkouts = {}
    if all_tag_ids:
        for co in Checkout.objects.filter(
            tag_id__in=all_tag_ids, returned_at__isnull=True
        ):
            open_checkouts[co.tag_id] = co

    def _tag_dict(tag):
        co = open_checkouts.get(tag.pk)
        return {
            "key_id": tag.key_id,
            "status": tag.get_status_display(),
            "status_raw": tag.status,
            "ring_notes": tag.ring_notes,
            "checkout": {
                "name": co.checked_out_by_name,
                "since": _fmt_date(co.checked_out_at),
            } if co else None,
        }

    # Access notes: unit-level + property-level (unit IS NULL)
    notes_qs = AccessNote.objects.filter(
        Q(unit=unit) | Q(property=prop, unit__isnull=True)
    ).order_by(
        F("source_date").desc(nulls_last=True),
        "-created_at",
    )

    def _note_dict(note):
        return {
            "text": note.text,
            "source": _SOURCE_DISPLAY.get(note.source, note.source),
            "date": _fmt_date(note.source_date) if note.source_date else None,
            "is_property_level": note.unit_id is None,
        }

    return {
        "address": prop.address_line_1 or "",
        "unit_number": unit.unit_number or "",
        "tags": [_tag_dict(t) for t in unit_tags],
        "property_tags": [_tag_dict(t) for t in prop_tags],
        "notes": [_note_dict(n) for n in notes_qs],
    }


def tag_info(tag) -> dict:
    """Build plain dict for a single tag (key-ID lookup result)."""
    co = Checkout.objects.filter(tag=tag, returned_at__isnull=True).first()

    unit = tag.unit
    prop = tag.property

    result = {
        "key_id": tag.key_id,
        "status": tag.get_status_display(),
        "status_raw": tag.status,
        "ring_notes": tag.ring_notes,
        "checkout": {
            "name": co.checked_out_by_name,
            "since": _fmt_date(co.checked_out_at),
        } if co else None,
        "unit_address": None,
        "unit_number": None,
        "property_address": None,
        "is_orphan": unit is None and prop is None,
    }

    if unit:
        result["unit_address"] = unit.property.address_line_1 or ""
        result["unit_number"] = unit.unit_number or ""
    if prop:
        result["property_address"] = prop.address_line_1 or ""

    return result

"""Read-only DB lookups for KeyBot Slack replies. No writes."""

import logging
import re
import zoneinfo
from datetime import timedelta

from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from core.models import Unit
from keys.models import AccessNote, Checkout, PendingKeyChange, Tag
from keys.services import (
    _SUFFIX_SET,
    build_unit_address_index,
    normalize_address,
    normalize_unit_token,
    unit_label,
)

_ET = zoneinfo.ZoneInfo("America/New_York")

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
    "dashboard": "Dashboard",
}


def _fmt_date(d):
    """Format date or datetime as 'Oct 22, 2025'. Datetimes convert to ET."""
    if hasattr(d, "astimezone"):
        d = d.astimezone(_ET)
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

# Exact-match phrases for the key_health intent (whole query only).
_KEY_HEALTH_PHRASES = frozenset({
    "health", "key health", "missing",
    "what's missing", "whats missing", "what is missing",
})

# ── Update-command regexes ────────────────────────────────────────

# Primary: "set code|lockbox|lockbox code [for] <addr> to <code>"
_SET_CODE_RE = re.compile(
    r"set\s+(?:(?:lockbox\s+)?code|lockbox)\s+(?:for\s+)?(.+?)\s+to\s+(.+)",
    re.IGNORECASE,
)

# Unit label tokens that mark the boundary between address and code
_UNIT_LABEL_RE = re.compile(r"^(?:unit|apt|#|lot)$", re.IGNORECASE)

# Street suffixes (reuse from services) for no-"to" boundary detection
_SUFFIX_SET_LOWER = frozenset(s.lower() for s in _SUFFIX_SET)


def _parse_set_code_no_to(text: str) -> dict | None:
    """Parse 'set code <addr> <code>' without a 'to' separator.

    Rule: scan tokens after the keyword preamble.  The code starts at
    the first token starting with a digit that comes AFTER a recognised
    boundary (unit label + number, or street suffix).  Everything from
    that token onward is code text; everything before is address.
    Returns {"address": ..., "code": ...} or None.
    """
    m = re.match(
        r"set\s+(?:(?:lockbox\s+)?code|lockbox)\s+(?:for\s+)?(.+)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    rest = m.group(1).strip()
    tokens = rest.split()
    if len(tokens) < 2:
        return None

    # Walk tokens looking for a boundary, then a digit-starting token after it
    boundary_seen = False
    for i, tok in enumerate(tokens):
        clean = tok.rstrip(".,").lower()
        # Check if this token is a unit label (next token would be its number)
        if _UNIT_LABEL_RE.match(clean):
            # unit label: boundary is after label + its number
            if i + 1 < len(tokens):
                boundary_seen = True  # mark after consuming unit num
                continue
        # If previous was a unit label, this is its number — boundary after it
        if i > 0 and _UNIT_LABEL_RE.match(tokens[i - 1].rstrip(".,").lower()):
            boundary_seen = True
            continue
        # Street suffix is a boundary
        if clean in _SUFFIX_SET_LOWER:
            boundary_seen = True
            continue
        # After a boundary, first digit-starting token starts the code
        if boundary_seen and tok[0].isdigit():
            addr = " ".join(tokens[:i]).strip()
            code = " ".join(tokens[i:]).strip()
            if addr and code:
                return {"address": addr, "code": code}
    return None


# Command keywords that signal an attempted command
_COMMAND_KEYWORDS = frozenset({"set", "assign", "retire", "missing", "found"})

_ASSIGN_RE = re.compile(
    r"assign\s+([ABab][\s\-]?\d{1,3})\s+to\s+(.+)", re.IGNORECASE,
)
_RETIRE_RE = re.compile(
    r"retire\s+([ABab][\s\-]?\d{1,3})\s*$", re.IGNORECASE,
)
_MARK_MISSING_RE = re.compile(
    r"missing\s+([ABab][\s\-]?\d{1,3})\s*$", re.IGNORECASE,
)
_FOUND_RE = re.compile(
    r"found\s+([ABab][\s\-]?\d{1,3})\s*$", re.IGNORECASE,
)


def parse_query(text: str) -> dict:
    """Classify stripped mention text into {type, value}.

    Types: 'help', 'key_health', 'key_id', 'take', 'return',
           'whats_out', 'set_code', 'assign', 'retire',
           'mark_missing', 'found', 'address', 'empty'.
    """
    text = text.strip()
    if not text:
        return {"type": "empty", "value": ""}
    if text.lower().rstrip("?.,!") in ("help", "?", "", "hi", "hello", "hey"):
        return {"type": "help", "value": ""}

    # "health", "key health", "missing", "what's missing", etc.
    normalized = text.lower().rstrip("?.,!").translate(_CURLY_QUOTES)
    if normalized in _KEY_HEALTH_PHRASES:
        return {"type": "key_health", "value": ""}

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

    # ── Update commands ───────────────────────────────────────────

    # "set code 28 Bass Lane to 1234" / "set lockbox 28 Bass to 1234"
    m = _SET_CODE_RE.fullmatch(text)
    if m:
        return {
            "type": "set_code",
            "value": {"address": m.group(1).strip(), "code": m.group(2).strip()},
        }

    # No-"to" form: "set code 90 moody ave 1776"
    no_to = _parse_set_code_no_to(text)
    if no_to:
        return {"type": "set_code", "value": no_to}

    # "assign A-038 to 28 Bass Lane"
    m = _ASSIGN_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {
                "type": "assign",
                "value": {"key_id": key_id, "address": m.group(2).strip()},
            }

    # "retire A-038"
    m = _RETIRE_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {"type": "retire", "value": {"key_id": key_id}}

    # "missing A-038" (bare "missing" → key_health, already matched above)
    m = _MARK_MISSING_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {"type": "mark_missing", "value": {"key_id": key_id}}

    # "found A-038"
    m = _FOUND_RE.fullmatch(text)
    if m:
        key_id = _normalize_key_id(m.group(1).strip())
        if key_id:
            return {"type": "found", "value": {"key_id": key_id}}

    # Bare key ID
    key_id = _normalize_key_id(text)
    if key_id is not None:
        return {"type": "key_id", "value": key_id}

    # Bad command detection: starts with a known keyword but didn't match
    first_word = text.split()[0].lower() if text.split() else ""
    if first_word in _COMMAND_KEYWORDS:
        return {"type": "bad_command", "value": first_word}

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

    # Access notes: unit-level + property-level (unit IS NULL), exclude hidden
    notes = list(
        AccessNote.objects.filter(
            Q(unit=unit) | Q(property=prop, unit__isnull=True)
        ).exclude(
            hidden=True,
        ).order_by(
            F("source_date").desc(nulls_last=True),
            "-created_at",
        )
    )

    # Batch-fetch requester names for keybot notes
    keybot_refs = [
        n.source_ref for n in notes if n.source == "keybot" and n.source_ref
    ]
    pending_names = {}
    if keybot_refs:
        for p in PendingKeyChange.objects.filter(
            pk__in=keybot_refs,
        ).only("pk", "requester_name"):
            pending_names[str(p.pk)] = p.requester_name

    def _note_dict(note):
        d = {
            "text": note.text,
            "source": _SOURCE_DISPLAY.get(note.source, note.source),
            "date": _fmt_date(note.source_date) if note.source_date else None,
            "is_property_level": note.unit_id is None,
        }
        if note.source == "keybot" and note.source_ref:
            d["author"] = pending_names.get(note.source_ref, "")
        return d

    # KeyBot notes first, then others (each group keeps date order)
    keybot_notes = [_note_dict(n) for n in notes if n.source == "keybot"]
    other_notes = [_note_dict(n) for n in notes if n.source != "keybot"]

    return {
        "address": prop.address_line_1 or "",
        "unit_number": unit.unit_number or "",
        "tags": [_tag_dict(t) for t in unit_tags],
        "property_tags": [_tag_dict(t) for t in prop_tags],
        "notes": keybot_notes + other_notes,
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


# ── Key Health ─────────────────────────────────────────────────────


def _unit_label(u):
    """Deduped address for a Unit in key-health contexts."""
    return unit_label(
        u.address_line_1, u.property.address_line_1, u.unit_number,
    )


def _tag_label(tag):
    """Deduped address for a Tag's assigned unit/property."""
    if tag.unit:
        return _unit_label(tag.unit)
    if tag.property:
        return tag.property.address_line_1 or "unknown"
    return "unassigned"


def key_health() -> dict:
    """Key health report: every category of key/code issue across active units.

    Returns a plain dict of categories → lists of dicts.  All list queries
    use explicit order_by (address, then key_id) for stable output.
    Hidden AccessNotes are excluded (a unit whose only notes are hidden
    counts as having NO code).
    """
    now = timezone.now()

    # Base: active Rentvine-synced units, stable address order.
    active = (
        Unit.objects.filter(is_active=True, rentvine_id__isnull=False)
        .select_related("property")
        .order_by("property__address_line_1", "unit_number")
    )

    # Subqueries for "has key" and "has code".
    has_tag = Tag.objects.filter(
        Q(unit=OuterRef("pk")) | Q(property=OuterRef("property_id"))
    ).exclude(status="retired")

    has_note = AccessNote.objects.filter(
        Q(unit=OuterRef("pk"))
        | Q(property=OuterRef("property_id"), unit__isnull=True)
    ).exclude(hidden=True)

    no_key = active.exclude(Exists(has_tag))
    no_code = active.exclude(Exists(has_note))

    # --- Vacant (RentEngine "Available") ---
    no_key_vacant = list(no_key.filter(rentengine_status="Available"))
    no_code_vacant = list(no_code.filter(rentengine_status="Available"))
    no_code_vacant_pks = {u.pk for u in no_code_vacant}

    # (a) Vacant: no key AND no code
    vacant_both = [u for u in no_key_vacant if u.pk in no_code_vacant_pks]
    vacant_both_pks = {u.pk for u in vacant_both}

    # (b) Vacant: no key only (excludes a)
    vacant_no_key = [u for u in no_key_vacant if u.pk not in vacant_both_pks]

    # (c) Vacant: no code only (excludes a)
    vacant_no_code = [
        u for u in no_code_vacant if u.pk not in vacant_both_pks
    ]

    def _unit_dict(u):
        return {"address": _unit_label(u), "unit_id": u.pk}

    # (d) Overdue checkouts > 24h
    threshold_24h = now - timedelta(hours=24)
    threshold_72h = now - timedelta(hours=72)
    overdue_qs = (
        Checkout.objects.filter(
            returned_at__isnull=True,
            checked_out_at__lt=threshold_24h,
        )
        .select_related(
            "tag", "tag__unit", "tag__unit__property", "tag__property",
        )
        .order_by(
            "tag__unit__property__address_line_1",
            "tag__unit__unit_number",
            "tag__key_id",
        )
    )
    overdue = []
    for co in overdue_qs:
        tag = co.tag
        addr = _tag_label(tag)
        hours = int((now - co.checked_out_at).total_seconds() / 3600)
        overdue.append({
            "key_id": tag.key_id,
            "address": addr,
            "checked_out_by_name": co.checked_out_by_name,
            "checked_out_by_slack_id": co.checked_out_by_slack_id,
            "hours": hours,
            "is_72h": co.checked_out_at < threshold_72h,
        })

    # (e) Missing tags
    missing_qs = (
        Tag.objects.filter(status="missing")
        .select_related("unit", "unit__property", "property")
        .order_by(
            "unit__property__address_line_1",
            "unit__unit_number",
            "key_id",
        )
    )
    missing = [
        {"key_id": t.key_id, "address": _tag_label(t)}
        for t in missing_qs
    ]

    # (f) Duplicate tags: units with 2+ non-retired tags
    dupe_units = (
        active
        .annotate(
            active_tag_count=Count(
                "key_tags", filter=~Q(key_tags__status="retired"),
            ),
        )
        .filter(active_tag_count__gte=2)
        .order_by("property__address_line_1", "unit_number")
    )
    duplicates = []
    for u in dupe_units:
        tag_ids = list(
            Tag.objects.filter(unit=u)
            .exclude(status="retired")
            .order_by("key_id")
            .values_list("key_id", flat=True)
        )
        duplicates.append({"address": _unit_label(u), "key_ids": tag_ids})

    # (g) Orphan tags: no unit, no property, non-retired
    orphans = [
        {"key_id": t.key_id, "tag_pk": t.pk, "imported_address": t.imported_address_raw}
        for t in (
            Tag.objects.filter(unit__isnull=True, property__isnull=True)
            .exclude(status="retired")
            .order_by("key_id")
        )
    ]

    # (h) Occupied (Leased): no key / no code
    occupied_no_key = list(no_key.filter(rentengine_status="Leased"))
    occupied_no_code = list(no_code.filter(rentengine_status="Leased"))

    # (i) Unknown (not in RentEngine): counts only
    unknown_no_key_count = no_key.filter(rentengine_status="").count()
    unknown_no_code_count = no_code.filter(rentengine_status="").count()

    # Coverage totals (for snapshot trend chart)
    total_units = active.count()
    with_key_count = total_units - no_key.count()
    with_code_count = total_units - no_code.count()
    with_both_count = active.filter(
        Exists(has_tag), Exists(has_note),
    ).count()

    return {
        "vacant_no_key_no_code": [_unit_dict(u) for u in vacant_both],
        "vacant_no_key": [_unit_dict(u) for u in vacant_no_key],
        "vacant_no_code": [_unit_dict(u) for u in vacant_no_code],
        "overdue_checkouts": overdue,
        "missing_tags": missing,
        "duplicate_tags": duplicates,
        "orphan_tags": orphans,
        "occupied_no_key": [_unit_dict(u) for u in occupied_no_key],
        "occupied_no_code": [_unit_dict(u) for u in occupied_no_code],
        "unknown_no_key_count": unknown_no_key_count,
        "unknown_no_code_count": unknown_no_code_count,
        "total_units": total_units,
        "with_key_count": with_key_count,
        "with_code_count": with_code_count,
        "with_both_count": with_both_count,
    }

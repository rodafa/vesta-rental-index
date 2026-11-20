"""
Pure functions for the key/lockbox import review.

No DB writes, no API calls — callers supply pre-fetched data.

IMPORTANT: callers must never catch-and-drop fetch exceptions.
Record every failure in the fetch_errors dict so it surfaces in the
review CSV.  Silently dropping errors produces misleading output.
"""

import csv
import html
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------

_SUFFIX_SET = frozenset({
    "st", "street", "rd", "road", "dr", "drive", "ln", "lane",
    "ave", "avenue", "pl", "place", "cir", "circle", "blvd", "boulevard",
    "hwy", "highway", "ct", "court", "terrace", "ter", "trail", "trl", "way",
})

_ORDINALS = {
    "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth",
    "5th": "fifth", "6th": "sixth", "7th": "seventh", "8th": "eighth",
    "9th": "ninth", "10th": "tenth",
}

_DIRECTIONALS = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "n.": "north", "s.": "south", "e.": "east", "w.": "west",
}

_SINGLE_CITIES = frozenset({
    "asheville", "avl", "hendersonville", "woodfin", "arden", "fletcher",
    "weaverville", "swannanoa", "candler", "leicester", "brevard",
    "waynesville", "marion", "canton", "morganton", "clyde", "etowah",
    "columbus", "alexander", "marshall",
})

_DOUBLE_CITIES = frozenset({
    ("black", "mountain"), ("black", "mtn"),
    ("mars", "hill"), ("mills", "river"), ("hot", "springs"),
    ("north", "carolina"),
})

# Patterns that extract a unit token from the address.
# Order matters: more specific patterns first.
_UNIT_PATTERNS = [
    # Descriptive multi-word tokens
    re.compile(r"\b(top\s+level\s+unit)\b", re.I),
    re.compile(r"\b(bottom\s+level\s+unit)\b", re.I),
    re.compile(r"\b(upstairs)\b", re.I),
    re.compile(r"\b(upper\s+unit)\b", re.I),
    # Parenthetical tokens
    re.compile(r"\((left)\)", re.I),
    re.compile(r"\((right)\)", re.I),
    # Explicit prefix tokens
    re.compile(r"\bunit\s+(\S+)", re.I),
    re.compile(r"\bapt\.?\s+(\S+)", re.I),
    re.compile(r"#\s*(\S+)"),
    re.compile(r"\blot\s+(\S+)", re.I),
    # Token after a street suffix: "Ct. A", "Ln 2" (single letter or 1-2 digits).
    # Excludes hwy/highway to avoid capturing highway numbers.
    re.compile(
        r"\b(?:st|street|rd|road|dr|drive|ln|lane|ave|avenue|pl|place|"
        r"cir|circle|blvd|boulevard|ct|court|terrace|ter|"
        r"trail|trl|way)\.?\s+([A-Za-z]|\d{1,2})\b",
        re.I,
    ),
    # Dash separator: " - B" (1-3 char token)
    re.compile(r"\s+-\s+([A-Za-z0-9]{1,3})(?=[\s,]|$)"),
]

_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")


def normalize_address(text):
    """
    Deterministic address normalization.

    Returns (street_number, base_name, unit_token | None).
    base_name is the street-name words BEFORE the first suffix or city,
    concatenated, lowercased, with all punctuation and spaces removed.
    """
    if not text or not text.strip():
        return ("", "", None)

    s = text.strip()

    # 1. Extract unit token from the full string.
    unit_token = None
    for pat in _UNIT_PATTERNS:
        m = pat.search(s)
        if m:
            unit_token = m.group(1).strip().lower()
            s = s[: m.start()] + s[m.end() :]
            break

    # 2. Strip remaining parenthetical notes.
    s = _PAREN_RE.sub(" ", s)

    # 3. Collapse whitespace, strip trailing commas.
    s = " ".join(s.split()).strip().rstrip(",").strip()

    # 4. Lowercase and split.
    parts = s.lower().split()
    if not parts:
        return ("", "", unit_token)

    # 5. Street number.
    street_number = parts[0] if parts[0][:1].isdigit() else ""
    name_words = parts[1:] if street_number else parts

    # 6. Map ordinals and directionals.
    name_words = [_ORDINALS.get(w, w) for w in name_words]
    name_words = [_DIRECTIONALS.get(w, w) for w in name_words]

    # 7. Walk name_words, collecting base parts until suffix or city.
    #    Suffixes break immediately.  City/state/zip only break when
    #    base_parts is already non-empty (so a street named after a city
    #    like "Weaverville Highway" keeps "weaverville").
    base_parts = []
    i = 0
    while i < len(name_words):
        word = name_words[i]
        clean = word.rstrip(".,")

        # Suffix always breaks, even with empty base_parts.
        if clean in _SUFFIX_SET:
            break

        # City / state / zip only break when we already have base content.
        if base_parts:
            if i + 1 < len(name_words):
                next_clean = name_words[i + 1].rstrip(".,")
                if (clean, next_clean) in _DOUBLE_CITIES:
                    break

            if clean in _SINGLE_CITIES:
                break

            if clean == "nc":
                break

            if re.match(r"^\d{5}(-\d{4})?$", clean):
                break

        base_parts.append(clean)
        i += 1

    # 8. base_name: join and strip all non-alphanumeric.
    base_name = re.sub(r"[^a-z0-9]", "", "".join(base_parts))

    return (street_number, base_name, unit_token)


# ---------------------------------------------------------------------------
# Unit token normalization
# ---------------------------------------------------------------------------

_UNIT_PREFIX_RE = re.compile(r"^(unit|apt\.?|suite|lot|#)\s*", re.I)


def normalize_unit_token(text):
    """
    Normalize a unit designator for comparison.

    Strips leading prefixes (Unit, Apt, #, Lot, Suite) and surrounding
    punctuation.  "Unit 2" / "#2" / "2" all normalize to "2".
    """
    if not text:
        return ""
    s = text.strip().lower()
    s = s.strip("()")
    s = _UNIT_PREFIX_RE.sub("", s)
    s = s.strip(" \t-#.")
    return s


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_key_box_csv(path, box_letter):
    """
    Parse a key box CSV export into tag row dicts.

    Finds the header row by locating the cell "Tag Number".
    Returns list of dicts with keys:
        key_id, tag_number, address, notes, key_with, row_type, box_letter
    Skips rows with an empty Address.
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = None
        for raw_row in reader:
            if header is None:
                for i, cell in enumerate(raw_row):
                    if cell.strip().lower() == "tag number":
                        header = [c.strip() for c in raw_row]
                        break
                continue

            row_dict = {}
            for i, col_name in enumerate(header):
                row_dict[col_name] = raw_row[i].strip() if i < len(raw_row) else ""

            tag_str = row_dict.get("Tag Number", "").strip()
            address = row_dict.get("Address", "").strip()
            notes = row_dict.get("Notes", "").strip()
            key_with = row_dict.get("Key With", "").strip()

            if not tag_str:
                continue

            try:
                tag_num = int(tag_str)
            except ValueError:
                continue

            if not address:
                continue

            key_id = f"{box_letter}-{tag_num:03d}"

            if address.upper() == "SMART BLANKS":
                row_type = "non_property"
            else:
                row_type = "property"

            rows.append({
                "key_id": key_id,
                "tag_number": tag_num,
                "address": address,
                "notes": notes,
                "key_with": key_with,
                "row_type": row_type,
                "box_letter": box_letter,
            })

    logger.info(
        "parsed_key_box_csv",
        extra={"path": str(path), "box_letter": box_letter, "rows": len(rows)},
    )
    return rows


def parse_box_or_fail(path, box_letter):
    """Parse a key box CSV; raise ValueError if it yields no property rows."""
    rows = parse_key_box_csv(path, box_letter)
    property_rows = [r for r in rows if r["row_type"] == "property"]
    if not property_rows:
        raise ValueError(
            f"Box {box_letter} file '{path}': no property rows found. "
            f"Check that the 'Tag Number' header exists and the file "
            f"is not empty."
        )
    return rows


# ---------------------------------------------------------------------------
# Rentvine note filtering
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_KEY_KEYWORDS_RE = re.compile(
    r"\b(lock\s?box(?:es)?|codes?|combo|keys?|shackle)\b", re.I
)


def _strip_html(text):
    """Strip HTML tags and decode entities."""
    return html.unescape(_HTML_TAG_RE.sub("", text))


def extract_key_notes(raw_notes):
    """
    Filter Rentvine notes to those containing key/lockbox keywords.

    Args:
        raw_notes: list of Rentvine note dicts
            (noteID, note [HTML], datePosted, isPinned).

    Returns list of {note_id, date, text} where text is plain-text
    and matches at least one keyword.
    """
    results = []
    for note in raw_notes:
        note_html = note.get("note", "") or ""
        plain = _strip_html(note_html)
        if not _KEY_KEYWORDS_RE.search(plain):
            continue

        date_str = (note.get("datePosted") or "")[:10]
        results.append({
            "note_id": note.get("noteID"),
            "date": date_str,
            "text": plain.strip(),
        })
    return results


# ---------------------------------------------------------------------------
# Tag → Unit matching
# ---------------------------------------------------------------------------

def build_unit_address_index(db_units):
    """
    Build (street_number, base_name) → list[Unit] index from DB units.

    Uses the unit's address_line_1 (falling back to property.address_line_1).
    """
    index = {}
    for unit in db_units:
        addr = unit.address_line_1 or unit.property.address_line_1 or ""
        num, base, _ = normalize_address(addr)
        if num and base:
            index.setdefault((num, base), []).append(unit)
    return index


def match_tag_to_unit(tag_row, units_by_addr):
    """
    Match a parsed tag row to a Rentvine unit.

    Args:
        tag_row: dict from parse_key_box_csv (must have row_type="property").
        units_by_addr: dict[(street_number, base_name)] → list[Unit].

    Returns (unit | None, property | None, match_level).
    match_level is "unit", "property", or "none".
    """
    num, base, tag_unit_token = normalize_address(tag_row["address"])
    if not num or not base:
        return (None, None, "none")

    candidates = units_by_addr.get((num, base))
    if not candidates:
        return (None, None, "none")

    tag_normalized = normalize_unit_token(tag_unit_token) if tag_unit_token else ""

    # Single unit at this address.
    if len(candidates) == 1:
        unit = candidates[0]
        db_normalized = normalize_unit_token(unit.unit_number)
        if tag_normalized and db_normalized and tag_normalized != db_normalized:
            return (None, unit.property, "property")
        return (unit, unit.property, "unit")

    # Multiple units — need a token to disambiguate.
    if not tag_normalized:
        return (None, candidates[0].property, "property")

    matched = []
    for unit in candidates:
        db_normalized = normalize_unit_token(unit.unit_number)
        if db_normalized == tag_normalized:
            matched.append(unit)

    if len(matched) == 1:
        return (matched[0], matched[0].property, "unit")

    return (None, candidates[0].property, "property")


# ---------------------------------------------------------------------------
# Review-row builder
# ---------------------------------------------------------------------------

_SHEET_WARNING_RE = re.compile(
    r"not accurate|missing|mystery|double check", re.I
)


def compute_flags(
    unit_level_tags, property_level_tags, re_unit_raw, key_notes,
    tag_row=None,
):
    """
    Compute flag strings for a single review row.

    Args:
        unit_level_tags: list of tag_row dicts matched at unit level.
        property_level_tags: list of tag_row dicts matched at property level.
        re_unit_raw: raw RentEngine unit dict or None.
        key_notes: list of extracted key note dicts for this unit.
        tag_row: for unmatched-tag rows only; None otherwise.

    Returns list of flag strings.
    """
    flags = []

    if tag_row is not None:
        flags.append("UNMATCHED_TAG")
        combined = f"{tag_row.get('notes', '')} {tag_row.get('key_with', '')}"
        if _SHEET_WARNING_RE.search(combined):
            flags.append("SHEET_WARNING")
        return flags

    if not unit_level_tags:
        flags.append("NO_TAG")
    elif len(unit_level_tags) >= 2:
        flags.append("MULTI_TAG")

    if not unit_level_tags and property_level_tags:
        flags.append("PROPERTY_LEVEL_MATCH_ONLY")

    re_internal = ""
    if re_unit_raw:
        sai = re_unit_raw.get("structured_access_instructions") or {}
        re_internal = sai.get("internal_notes") or ""
    if not re_internal and not key_notes:
        flags.append("NO_ACCESS_INFO")

    for tag in unit_level_tags:
        combined = f"{tag.get('notes', '')} {tag.get('key_with', '')}"
        if _SHEET_WARNING_RE.search(combined):
            flags.append("SHEET_WARNING")
            break

    return flags


def build_review_rows(db_units, tag_rows, re_index, rv_key_notes,
                      fetch_errors=None, property_filter=None):
    """
    Build CSV review rows: one per Rentvine unit + one per unmatched tag.

    Args:
        db_units: list of Unit objects (with select_related property).
        tag_rows: combined list from parse_key_box_csv (both boxes).
        re_index: dict[str(rentengine_id) → raw RE unit dict].
        rv_key_notes: dict[rentvine_unit_id → list of
            {note_id, date, text, _prefix}].
            Property-level notes carry _prefix="[property] ".
            Deduplication by note_id is done here.
        fetch_errors: dict[rentvine_unit_id → list[str]] of error
            messages from failed API calls.  Callers must record every
            failure here; see module docstring.
        property_filter: when set, only emit unmatched_tag rows whose
            raw sheet address contains this text (case-insensitive).

    Returns (rows, stats).
    """
    if fetch_errors is None:
        fetch_errors = {}

    units_by_addr = build_unit_address_index(db_units)

    unit_tags = {}       # rentvine_unit_id → list[tag_row]
    property_tags = {}   # rentvine_property_id → list[tag_row]
    unmatched = []

    for tag in tag_rows:
        if tag["row_type"] != "property":
            continue

        unit, prop, level = match_tag_to_unit(tag, units_by_addr)

        if level == "unit" and unit is not None:
            unit_tags.setdefault(unit.rentvine_id, []).append(tag)
        elif level == "property" and prop is not None:
            property_tags.setdefault(prop.rentvine_id, []).append(tag)
        else:
            unmatched.append(tag)

    rows = []
    for unit in db_units:
        uid = unit.rentvine_id
        prop = unit.property
        u_tags = unit_tags.get(uid, [])
        p_tags = property_tags.get(prop.rentvine_id, [])
        re_raw = (
            re_index.get(str(unit.rentengine_id))
            if unit.rentengine_id else None
        )
        notes = rv_key_notes.get(uid, [])

        re_key_access = ""
        re_showing_method = ""
        re_internal_notes = ""
        if re_raw:
            re_key_access = re_raw.get("key_access") or ""
            re_showing_method = re_raw.get("showing_method") or ""
            sai = re_raw.get("structured_access_instructions") or {}
            re_internal_notes = sai.get("internal_notes") or ""

        seen_note_ids = set()
        formatted_notes = []
        for n in notes:
            nid = n.get("note_id")
            if nid is not None and nid in seen_note_ids:
                continue
            if nid is not None:
                seen_note_ids.add(nid)
            prefix = n.get("_prefix", "")
            formatted_notes.append(f"{prefix}{n['date']}: {n['text']}")

        flags = compute_flags(u_tags, p_tags, re_raw, notes)
        unit_errors = fetch_errors.get(uid, [])

        rows.append({
            "row_type": "unit",
            "rentvine_property_id": prop.rentvine_id,
            "rentvine_unit_id": uid,
            "property_address": prop.address_line_1 or "",
            "unit_number": unit.unit_number or "",
            "sheet_key_ids": ";".join(t["key_id"] for t in u_tags),
            "sheet_raw_addresses": ";".join(t["address"] for t in u_tags),
            "sheet_notes": ";".join(
                t["notes"] for t in u_tags if t["notes"]
            ),
            "sheet_key_with": ";".join(
                t["key_with"] for t in u_tags if t["key_with"]
            ),
            "match_level": "unit" if u_tags else "",
            "property_level_key_ids": ";".join(
                t["key_id"] for t in p_tags
            ),
            "property_level_raw_addresses": ";".join(
                t["address"] for t in p_tags
            ),
            "rentengine_key_access": re_key_access,
            "rentengine_showing_method": re_showing_method,
            "rentengine_internal_notes": re_internal_notes,
            "rentvine_key_notes": " | ".join(formatted_notes),
            "flags": ";".join(flags),
            "errors": "; ".join(unit_errors),
        })

    for tag in unmatched:
        if (property_filter
                and property_filter.lower() not in tag["address"].lower()):
            continue
        flags = compute_flags([], [], None, [], tag_row=tag)
        rows.append({
            "row_type": "unmatched_tag",
            "rentvine_property_id": "",
            "rentvine_unit_id": "",
            "property_address": "",
            "unit_number": "",
            "sheet_key_ids": tag["key_id"],
            "sheet_raw_addresses": tag["address"],
            "sheet_notes": tag.get("notes", ""),
            "sheet_key_with": tag.get("key_with", ""),
            "match_level": "none",
            "property_level_key_ids": "",
            "property_level_raw_addresses": "",
            "rentengine_key_access": "",
            "rentengine_showing_method": "",
            "rentengine_internal_notes": "",
            "rentvine_key_notes": "",
            "flags": ";".join(flags),
            "errors": "",
        })

    def sort_key(r):
        is_unmatched = 1 if r["row_type"] == "unmatched_tag" else 0
        return (
            is_unmatched,
            r["property_address"].lower(),
            r["unit_number"].lower(),
        )

    rows.sort(key=sort_key)

    unit_rows = [r for r in rows if r["row_type"] == "unit"]
    stats = {
        "units": len(unit_rows),
        "tagged": sum(1 for r in unit_rows if r["sheet_key_ids"]),
        "untagged": sum(1 for r in unit_rows if not r["sheet_key_ids"]),
        "multi_tag": sum(
            1 for r in unit_rows if "MULTI_TAG" in r["flags"]
        ),
        "unmatched_tags": sum(
            1 for r in rows if r["row_type"] == "unmatched_tag"
        ),
        "with_rentengine_notes": sum(
            1 for r in unit_rows if r["rentengine_internal_notes"]
        ),
        "with_rentvine_key_notes": sum(
            1 for r in unit_rows if r["rentvine_key_notes"]
        ),
        "errors": sum(1 for r in rows if r["errors"]),
    }

    return rows, stats

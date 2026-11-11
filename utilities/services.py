"""
Utility extraction services: pure functions for HTML conversion, note filtering,
quote verification, fingerprinting, conflict detection, and prompt building.

Also contains the per-property extraction orchestrator.
"""

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date
from html import unescape
from html.parser import HTMLParser

from utilities.models import (
    BASIS_CHOICES,
    RESPONSIBILITY_CHOICES,
    SOURCE_TYPE_CHOICES,
    UTILITY_TYPE_CHOICES,
)

logger = logging.getLogger(__name__)

# -- Valid values (derived from model choices, single source of truth) ------

VALID_UTILITY_TYPES = {c[0] for c in UTILITY_TYPE_CHOICES}
VALID_RESPONSIBILITIES = {c[0] for c in RESPONSIBILITY_CHOICES}
VALID_BASIS = {c[0] for c in BASIS_CHOICES}
VALID_SOURCE_TYPES = {c[0] for c in SOURCE_TYPE_CHOICES}
EXTRAS_KEYS = ("tank_size", "tank_location", "last_delivery_date", "last_delivery_amount")

# -- Note filter constants (ported from probe, single source of truth) -----

_SECURITY_RE = re.compile(
    r"\b(door\s*code|lockbox|lock\s*box|key\s*code|gate\s*code"
    r"|alarm\s*code|entry\s*code|access\s*code"
    r"|passcode|pass\s+code|keypad|combo|combination"
    r"|smart\s+lock|deadbolt|lock\s+screen)\b"
    r"|\bpin\b"
    r"|press\s+\d"
    r"|code\s*(is|:)\s*\d",
    re.IGNORECASE,
)

_UTILITY_WORD_RE = re.compile(
    r"\b(water|sewer|septic|well|electric|energy|meter"
    r"|trash|garbage|gas|oil|propane|heat|internet|utilit(?:y|ies))\b",
    re.IGNORECASE,
)

_TENANT_PAYER_RE = re.compile(
    r"\b(?:"
    r"(?:tenant|resident)\s+pays"
    r"|(?:tenant|resident)\s+(?:(?:is|are|will\s+be|currently)\s+)*paying"
    r"|(?:tenant|resident)\s+will\s+pay"
    r"|(?:tenant|resident)\s+responsible"
    r"|(?:tenant|resident)\s+is\s+responsible"
    r"|paid\s+by\s+(?:the\s+)?(?:tenant|resident)"
    r"|(?:tenant|resident)'?s\s+responsibility"
    r"|(?:tenant|resident)\s+to\s+pay"
    r")\b",
    re.IGNORECASE,
)

_OWNER_PAYER_RE = re.compile(
    r"\b(?:"
    r"(?:owner|landlord)\s+pays"
    r"|(?:owner|landlord)\s+(?:(?:is|are|will\s+be|currently)\s+)*paying"
    r"|(?:owner|landlord)\s+will\s+pay"
    r"|(?:owner|landlord)\s+responsible"
    r"|(?:owner|landlord)\s+is\s+responsible"
    r"|landlord\s+covers"
    r"|landlord\s+provides"
    r"|paid\s+by\s+(?:the\s+)?(?:owner|landlord)"
    r"|(?:owner|landlord)'?s\s+responsibility"
    r"|(?:owner|landlord)\s+to\s+pay"
    r")\b",
    re.IGNORECASE,
)


# -- Note filter helpers ---------------------------------------------------

REDACTION_MARKER = "[line removed]"

_BARE_CODE_RE = re.compile(r"^[^\s]{1,12}$")


def _line_is_security(line: str) -> bool:
    """True if the line matches _SECURITY_RE as written or with pipes collapsed."""
    if _SECURITY_RE.search(line):
        return True
    # Table rows join cells with " | "; collapse pipes so the regex can
    # see multi-cell phrases like "Door | Code | 1234" as "Door Code 1234".
    collapsed = line.replace(" | ", " ")
    if collapsed != line and _SECURITY_RE.search(collapsed):
        return True
    return False


def _is_bare_code(line: str) -> bool:
    """True if line looks like a bare access code (<=12 chars, no spaces, has a digit)."""
    return bool(_BARE_CODE_RE.match(line)) and any(c.isdigit() for c in line)


def redact_security_lines(converted_text: str) -> tuple[str, int]:
    """
    Replace lines matching _SECURITY_RE with REDACTION_MARKER.

    Also removes bare-code lines (<=12 chars, no spaces, has a digit) that
    immediately follow a removed line. Consecutive markers collapse to one.

    Returns (redacted_text, lines_removed_count).
    """
    lines = converted_text.split("\n")
    result = []
    removed = 0
    prev_was_removed = False

    for line in lines:
        if _line_is_security(line):
            removed += 1
            if not prev_was_removed:
                result.append(REDACTION_MARKER)
            prev_was_removed = True
        elif prev_was_removed and _is_bare_code(line):
            removed += 1
            # Marker already emitted for the preceding removed line
            prev_was_removed = True
        else:
            result.append(line)
            prev_was_removed = False

    return "\n".join(result), removed


def has_utility_words(plain_text: str) -> bool:
    """True if the text mentions utility-related words."""
    return bool(_UTILITY_WORD_RE.search(plain_text))


# -- HTML → text conversion (ported from probe) ----------------------------

class _TableParser(HTMLParser):
    """Parse an HTML table into rows of cell dicts."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell_text: str | None = None
        self._cell_attrs: dict = {}
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._cell_text = ""
            self._cell_attrs = dict(attrs)
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "td" and self._row is not None:
            self._row.append(
                {
                    "text": (self._cell_text or "").strip(),
                    "colspan": self._cell_attrs.get("colspan", "1"),
                    "rowspan": self._cell_attrs.get("rowspan", "1"),
                }
            )
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell and self._cell_text is not None:
            self._cell_text += data

    def handle_entityref(self, name):
        if self._in_cell and self._cell_text is not None:
            self._cell_text += unescape(f"&{name};")

    def handle_charref(self, name):
        if self._in_cell and self._cell_text is not None:
            self._cell_text += unescape(f"&#{name};")


def _convert_template_html(html: str) -> str:
    """Convert template-format HTML to text: block tags → newlines, strip tags."""
    text = re.sub(
        r"</?(p|div|br|li|tr|h[1-6])\b[^>]*/?>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    result = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


def _convert_table_html(html: str) -> tuple[str, dict]:
    """
    Convert table-format HTML to pipe-delimited text.

    Returns (converted_text, table_flags) where table_flags is:
      {"row_unclear": [list of 1-indexed row numbers], "table_irregular": bool}
    """
    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows

    if not rows:
        return _convert_template_html(html), {"row_unclear": [], "table_irregular": False}

    table_irregular = False
    for row in rows:
        for cell in row:
            if cell["colspan"] != "1" or cell["rowspan"] != "1":
                table_irregular = True
                break
        if table_irregular:
            break

    lines = []
    row_unclear = []

    for i, row in enumerate(rows):
        cell_texts = [cell["text"] for cell in row]
        line = " | ".join(cell_texts)
        lines.append(line)

        if i > 0:
            x_count = sum(
                1 for cell in row if cell["text"].strip().upper() == "X"
            )
            if x_count != 1:
                row_unclear.append(i)

    converted = "\n".join(lines)
    flags = {"row_unclear": row_unclear, "table_irregular": table_irregular}
    return converted, flags


def convert_note_html(html: str) -> tuple[str, str, dict | None]:
    """
    Convert a note's raw HTML to source text.

    Returns (converted_text, format_type, table_flags_or_None).
    format_type is "table" or "template".
    """
    if "<table" in html.lower():
        text, flags = _convert_table_html(html)
        return text, "table", flags
    else:
        return _convert_template_html(html), "template", None


# -- Quote verification ----------------------------------------------------

def collapse_ws(s: str) -> str:
    """Collapse runs of whitespace to a single space."""
    return re.sub(r"\s+", " ", s).strip()


def check_quote(quote: str | None, source_text: str) -> bool:
    """Check if collapsed quote is a substring of collapsed source text."""
    if not quote:
        return False
    if REDACTION_MARKER in quote:
        return False
    return collapse_ws(quote) in collapse_ws(source_text)


def verify_extras_values(extras: dict | None, source_text: str) -> dict:
    """
    For each oil/propane extras key, verify the value is a verbatim substring
    of source_text. Drop to None any value not found.

    Returns a cleaned dict (always has all 4 keys).
    """
    result = {}
    if not extras or not isinstance(extras, dict):
        return {k: None for k in EXTRAS_KEYS}
    for key in EXTRAS_KEYS:
        val = extras.get(key)
        if val and check_quote(str(val), source_text):
            result[key] = val
        else:
            result[key] = None
    return result


# -- JSON extraction -------------------------------------------------------

def _reply_snippet(raw_text: str) -> str:
    """
    Build a safe diagnostic snippet for stdout: first 200 chars, last 100
    chars, and total length. Never logged via logger or stored in DB.
    """
    length = len(raw_text)
    head = raw_text[:200]
    tail = raw_text[-100:] if length > 300 else ""
    if tail:
        return f"({length} chars) HEAD: {head!r} ... TAIL: {tail!r}"
    return f"({length} chars) {head!r}"


def extract_json_object(text: str) -> str | None:
    """
    Strip whitespace and any ``` fences, then return the substring from
    the first '{' to the last '}'. Returns None if no braces found.
    """
    text = text.strip()
    # Strip ``` fences (with optional language tag)
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last ``` line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start:end + 1]


# -- Hashing ---------------------------------------------------------------

def compute_source_text_hash(text: str) -> str:
    """SHA-256 hex of the whitespace-collapsed text."""
    return hashlib.sha256(collapse_ws(text).encode("utf-8")).hexdigest()


def compute_fingerprint(
    rentvine_unit_id: int,
    utility_type: str,
    source_type: str,
    source_note_id: int | None,
    source_text_hash: str,
    evidence_quote: str,
) -> str:
    """
    SHA-256 hex of the canonical fingerprint string.

    Inputs: (rentvine_unit_id, utility_type, source_type, source_note_id,
    source_text_hash, evidence_quote).
    """
    parts = [
        str(rentvine_unit_id),
        utility_type,
        source_type,
        str(source_note_id) if source_note_id is not None else "",
        source_text_hash,
        collapse_ws(evidence_quote),
    ]
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- Conflict detection ----------------------------------------------------

def detect_conflicts(proposals: list[dict]) -> list[dict]:
    """
    Group proposals by (rentvine_unit_id, utility_type). For any group with
    more than one distinct non-unknown conflict class, mark all proposals
    in the group with has_conflict=True and write conflict_note.

    Mutates and returns the list.
    """
    from utilities.grid import conflict_class

    groups = defaultdict(list)
    for p in proposals:
        groups[(p["rentvine_unit_id"], p["utility_type"])].append(p)

    for (unit_id, utype), group in groups.items():
        classes = {
            conflict_class(p["responsibility"]) for p in group
            if p["responsibility"] != "unknown"
        }
        if len(classes) > 1:
            # Build conflict note naming sources and values
            parts = []
            for p in group:
                src_desc = _source_description(p)
                parts.append(f"{src_desc}: {p['responsibility']}")
            note = f"{utype} @ unit {unit_id}: " + " vs ".join(parts)
            for p in group:
                p["has_conflict"] = True
                existing = p.get("conflict_note") or ""
                if note not in existing:
                    if existing:
                        p["conflict_note"] = existing + "; " + note
                    else:
                        p["conflict_note"] = note

    return proposals


def _source_description(proposal: dict) -> str:
    """Short human-readable description of a proposal's source."""
    if proposal["source_type"] == "note":
        return f"note {proposal['source_note_id']}"
    return f"listing (unit {proposal['rentvine_unit_id']})"


def recompute_conflict_for_group(proposals):
    """
    Pure function: recompute has_conflict and conflict_note for a group of
    proposals sharing the same (rentvine_unit_id, utility_type).

    For each proposal:
    - Split conflict_note on "; ".
    - Keep pieces starting with "Quote says" (payer-phrase).
    - Keep other pieces (group-conflict) ONLY if the group still has a real
      conflict under the new conflict_class rule.
    - new_has_conflict = any kept payer-phrase pieces OR group still conflicts.

    Returns list of (proposal_pk, new_has_conflict, new_conflict_note) tuples.
    """
    from utilities.grid import conflict_class

    # Does the group still have a real conflict?
    classes = {
        conflict_class(p["responsibility"]) for p in proposals
        if p["responsibility"] != "unknown"
    }
    group_conflicts = len(classes) > 1

    results = []
    for p in proposals:
        old_note = p.get("conflict_note") or ""
        pieces = [s for s in old_note.split("; ") if s] if old_note else []

        payer_pieces = [s for s in pieces if s.startswith("Quote says")]
        group_pieces = [s for s in pieces if not s.startswith("Quote says")]

        kept = list(payer_pieces)
        if group_conflicts:
            kept.extend(group_pieces)

        new_has_conflict = bool(payer_pieces) or group_conflicts
        new_note = "; ".join(kept)
        results.append((p["id"], new_has_conflict, new_note))

    return results


# -- Rentvine data fetching ------------------------------------------------

def _safe_bool_field(data: dict, *keys) -> bool:
    """Return True if any of *keys* holds a truthy value. True if absent."""
    for key in keys:
        val = data.get(key)
        if val is not None:
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                return bool(val)
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)
    return True


def fetch_active_units(client, property_id: int) -> list[dict]:
    """
    Fetch active units from Rentvine for a property.

    Returns list of {"rentvine_unit_id": int} dicts.
    """
    units_data = client.get_all(f"/properties/{property_id}/units")
    active = []
    for u in units_data:
        inner = u.get("unit") or u
        uid = inner.get("unitID")
        if not uid:
            continue
        if not _safe_bool_field(inner, "isActive", "is_active", "active"):
            continue
        active.append({"rentvine_unit_id": int(uid)})
    return active


def fetch_property_address(client, property_id: int) -> str:
    """
    Fetch a short address string for a property from Rentvine.

    Lets RentvineAPIError propagate. Falls back to "property <id>" only
    when the response has no usable address fields.
    """
    data = client.get(f"/properties/{property_id}")
    rec = (data.get("property") or data) if isinstance(data, dict) else {}
    addr = (
        rec.get("address")
        or rec.get("addressLine1")
        or rec.get("address1")
    )
    if not addr:
        street = " ".join(filter(None, [
            rec.get("streetNumber"), rec.get("streetName"),
        ]))
        addr = street or None
    if addr:
        parts = [addr, rec.get("city"), rec.get("stateID"), rec.get("postalCode")]
        return " ".join(filter(None, parts))
    return f"property {property_id}"


def fetch_notes(client, object_type_id: int, object_id: int) -> list[dict]:
    """
    Fetch notes from Rentvine for an object (7=unit, 6=property).

    Returns raw note dicts with noteID, note (HTML), datePosted, isPinned.
    """
    raw = client.get_all(f"/notes/{object_type_id}/{object_id}")
    notes = []
    for item in raw:
        n = item.get("note") or item
        notes.append(n)
    return notes


def fetch_listing_description(client, property_id: int, unit_id: int) -> str | None:
    """Fetch listing description text for a unit. Returns None if empty."""
    data = client.get(
        f"/properties/{property_id}/units/{unit_id}",
        params={"includes": "listing"},
    )
    listing = data.get("listing") or {}
    desc = (listing.get("description") or "").strip()
    return desc if desc else None


# -- Note processing pipeline ---------------------------------------------

def filter_and_convert_notes(
    raw_notes: list[dict],
    level: str,
    unit_id: int | None,
) -> tuple[list[dict], int, int]:
    """
    Convert HTML notes to text, redact security lines, and filter.

    Args:
        raw_notes: list of Rentvine note dicts.
        level: "unit" or "property".
        unit_id: the Rentvine unit ID (None for property-level).

    Returns:
        (converted_notes, dropped_count, lines_redacted) where
        converted_notes is a list of dicts with keys: note_id, unit_id,
        level, date_posted, converted_text, source_text_hash.
    """
    dropped = 0
    lines_redacted = 0
    converted = []

    for n in raw_notes:
        nid = int(n.get("noteID", 0))
        note_html = n.get("note", "")

        # 1. Convert HTML → text first
        text, fmt, table_flags = convert_note_html(note_html)

        # 2. Redact security lines
        redacted, removed = redact_security_lines(text)
        lines_redacted += removed

        if removed:
            logger.info(
                "Lines redacted",
                extra={"note_id": nid, "lines_removed": removed},
            )

        # 3. If only markers remain, drop as security
        non_marker = [
            ln for ln in redacted.split("\n")
            if ln.strip() and ln.strip() != REDACTION_MARKER
        ]
        if not non_marker:
            logger.info(
                "Note dropped",
                extra={"note_id": nid, "reason": "security"},
            )
            dropped += 1
            continue

        # 4. Utility-word check on redacted text
        if not has_utility_words(redacted):
            logger.info(
                "Note dropped",
                extra={"note_id": nid, "reason": "no_utility_words"},
            )
            dropped += 1
            continue

        converted.append({
            "note_id": nid,
            "unit_id": unit_id,
            "level": level,
            "date_posted": n.get("datePosted", None),
            "converted_text": redacted,
            "source_text_hash": compute_source_text_hash(redacted),
        })

    converted.sort(key=lambda x: x["date_posted"] or "")
    return converted, dropped, lines_redacted


# -- Prompt building -------------------------------------------------------

SYSTEM_PROMPT = """\
You are a utility-information extractor for rental properties. You receive
source texts (notes and listing descriptions) and return structured JSON.

RULES:
1. You are a renderer, not a reporter. Only restate what the text explicitly
   says. Anything not stated is "unknown" or null. Never infer providers,
   dates, or amounts that are not written in the source text.
2. Oil and propane are DIFFERENT utilities. Never merge or relabel them.
   HEAT lines like "Electric/Forced Air/ Propane" list what is written;
   do not decide which one is the primary heat source.
3. For the table format, use the column headers (Landlord / Resident / N/A)
   to decide responsibility. Map Landlord to "owner", Resident to "tenant",
   N/A to "not_applicable". If an X is missing, doubled, or the header is
   unclear, use "unknown".
4. For template-format notes, a field that says N/A, NONE, or NO for a
   utility maps responsibility to "not_applicable" (meaning the utility does
   not apply to this property). Use "unknown" only when the text does not say
   or is genuinely ambiguous.
5. Where a note's SUMMARY line and a labeled field (WATER:, SEWER:, TRASH:,
   etc.) differ about who pays for the same utility, emit a row for each
   side. Never merge or pick one — always include both rows so the human
   reviewer sees the disagreement.
6. Where notes and the listing description disagree about the same utility,
   emit a row for each side. Never merge or pick one.
7. Where two different notes disagree about the same utility (who pays,
   provider, or how it is billed or set up), emit a row for each note.
   Do not prefer the newer note and do not merge them.
8. Ignore non-utility items: pest control, lawn care, filters, smoke
   detectors, security systems, laundry, parking, pets, Section 8,
   fireplace, plumbing-abuse clauses.
9. Each evidence_quote must be an EXACT verbatim substring of the cited
   source text (after HTML conversion). Do not paraphrase, reorder words,
   or fix typos. Copy character-for-character. Each quote must be the
   SHORTEST span that supports the row: one labeled line from a template
   note, or the ENTIRE converted table row (including the pipes) for table
   notes so the X's column is visible in the quote. For SUMMARY-vs-field
   disagreements, cite each side with its own row and quote.
10. For oil and propane ONLY, populate the extras object with tank_size,
    tank_location, last_delivery_date, last_delivery_amount — each null if
    not stated in the source text. Every non-null extras value must be an
    exact verbatim substring of the cited source text.
11. The "arrangement" field captures ONLY how the utility is billed or set
    up between landlord and tenant (examples: "TENANT NEEDS TO GO IN PERSON
    TO SET UP", "water is under the owner's name and the bill is sent to
    the tenant", "does not open a water account"). It must be an exact
    verbatim substring of the same source text as evidence_quote. It is
    null unless the source explicitly describes a billing or setup
    arrangement. Do NOT put tank location, meter info, provider names,
    or general property descriptions here — those belong in extras or
    provider.
12. basis: set to "stated" when the evidence_quote line itself names the
    utility AND says who pays (e.g. "WATER: Tenant"). Set to "catch_all"
    when responsibility is inferred from a separate blanket line such as
    "TENANT PAYS FOR ALL OTHER UTILITIES" or "All utilities tenant
    responsibility". For catch_all rows, evidence_quote must contain TWO
    lines separated by a newline: line 1 is the utility-specific line
    (e.g. "ELECTRIC: Duke Energy"), line 2 is the catch-all line. Each
    line must be an exact verbatim substring of the source text.
13. Create a row ONLY for the utility named by the line's own label
    (WATER, SEWER, ELECTRIC, etc.). A provider name that mentions another
    utility (e.g. "HENDERSONVILLE WATER/SEWER") does not create a row for
    that other utility. If the source text has a separate labeled line for
    that other utility, it will get its own row from that line.
14. Responsibility comes from any of these sources:
    (a) explicit payer words such as "tenant pays", "owner pays",
        "landlord covers", "resident pays", "included in rent";
    (b) a labeled field whose value names the party, e.g.
        "WATER: TENANT" or "ELECTRIC - DUKE ENERGY - TENANT PAYS";
    (c) the Landlord / Resident / N/A table columns defined in rule 3;
    (d) explicit not-applicable statements such as "NO GAS/OIL" or
        "N/A" as defined in rule 4.
    Rules 3 and 4 still apply unchanged.
    What must NOT set responsibility: wording about whose NAME an account
    or meter is in, who is billed, master meters, reimbursement
    arrangements, or bill pass-through. Put such wording in "arrangement"
    and do NOT let it change responsibility. If none of (a)-(d) is
    present, use "unknown".
15. Lines reading "[line removed]" are redaction gaps. Never quote them
    and never infer anything from them.

Return ONLY a JSON object with no markdown fences, no commentary:
{
  "utilities": [
    {
      "utility_type": "electric|water|sewer|gas|oil|propane|trash|internet|septic|well|other",
      "responsibility": "tenant|owner|included_in_rent|not_applicable|unknown",
      "basis": "stated|catch_all",
      "provider": "<string or null>",
      "evidence_quote": "<exact verbatim substring — or two newline-separated lines for catch_all>",
      "source": "note|listing_description",
      "note_id": <int or null>,
      "unit_id": <int or null>,
      "source_date": "YYYY-MM-DD or null",
      "arrangement": "<exact verbatim substring describing billing/setup arrangement, or null>",
      "extras": null
    }
  ]
}

For oil and propane rows, extras is:
{
  "tank_size": "<exact verbatim substring or null>",
  "tank_location": "<exact verbatim substring or null>",
  "last_delivery_date": "<exact verbatim substring or null>",
  "last_delivery_amount": "<exact verbatim substring or null>"
}

For all other utility types, extras is null.

Your entire reply must be the JSON object and nothing else. Start with { and end with }."""


def build_system_prompt() -> str:
    """Return the system prompt for utility extraction."""
    return SYSTEM_PROMPT


def build_user_prompt(
    property_id: int,
    address: str,
    converted_notes: list[dict],
    listing_descs: list[dict],
) -> str:
    """
    Assemble the user message from converted notes and listing descriptions.

    converted_notes: list of dicts with note_id, unit_id, level, date_posted,
        converted_text.
    listing_descs: list of dicts with unit_id, description.
    """
    parts = [
        f"Extract utility information for property {property_id} ({address}).",
        "",
        "SOURCES:",
    ]

    for cn in converted_notes:
        level_label = (
            f"unit_id={cn['unit_id']}"
            if cn["unit_id"]
            else "property-level"
        )
        parts.append("")
        parts.append(
            f"--- NOTE (id={cn['note_id']}, {level_label}, "
            f"date={cn['date_posted']}) ---"
        )
        parts.append(cn["converted_text"])
        parts.append("--- END NOTE ---")

    for ld in listing_descs:
        parts.append("")
        parts.append(
            f"--- LISTING DESCRIPTION (unit_id={ld['unit_id']}) ---"
        )
        parts.append(ld["description"])
        parts.append("--- END LISTING DESCRIPTION ---")

    parts.append("")
    parts.append("Return the JSON object described in your instructions.")
    return "\n".join(parts)


# -- Source lookup builder -------------------------------------------------

def build_source_lookup(
    converted_notes: list[dict],
    listing_descs: list[dict],
) -> dict:
    """
    Build a lookup dict for quote checking and source resolution.

    Keys:
      ("note", note_id) -> {"text": str, "level": str, "unit_id": int|None,
                            "date_posted": str|None, "source_text_hash": str}
      ("listing", unit_id) -> {"text": str, "source_text_hash": str}

    Also builds a set of valid note_ids and valid listing unit_ids.
    """
    lookup = {}
    valid_note_ids = set()
    valid_listing_unit_ids = set()

    for cn in converted_notes:
        nid = cn["note_id"]
        lookup[("note", nid)] = {
            "text": cn["converted_text"],
            "level": cn["level"],
            "unit_id": cn["unit_id"],
            "date_posted": cn["date_posted"],
            "source_text_hash": cn["source_text_hash"],
        }
        valid_note_ids.add(nid)

    for ld in listing_descs:
        uid = ld["unit_id"]
        lookup[("listing", uid)] = {
            "text": ld["description"],
            "source_text_hash": compute_source_text_hash(ld["description"]),
        }
        valid_listing_unit_ids.add(uid)

    return {
        "lookup": lookup,
        "valid_note_ids": valid_note_ids,
        "valid_listing_unit_ids": valid_listing_unit_ids,
    }


# -- Payer-phrase guard ----------------------------------------------------

def check_payer_phrase(evidence_quote: str, responsibility: str) -> tuple[bool, str]:
    """
    Check if explicit payer phrases in the evidence_quote contradict
    the assigned responsibility.

    Returns (has_conflict, conflict_note). Only flags when one side has
    explicit payer phrases and the other does not, and the responsibility
    names the wrong party. Both-match or neither-match is inconclusive.
    """
    tenant_match = bool(_TENANT_PAYER_RE.search(evidence_quote))
    owner_match = bool(_OWNER_PAYER_RE.search(evidence_quote))

    # Both match or neither → inconclusive
    if tenant_match == owner_match:
        return False, ""

    if tenant_match and responsibility == "owner":
        return True, "Quote says tenant/resident pays but row says owner"
    if owner_match and responsibility == "tenant":
        return True, "Quote says owner/landlord pays but row says tenant"

    return False, ""


# -- Row validation and proposal building ----------------------------------

def validate_and_build_proposals(
    model_utilities: list[dict],
    source_info: dict,
    active_unit_ids: list[int],
    property_id: int,
) -> tuple[list[dict], dict]:
    """
    Validate model output rows against source texts and build proposal dicts.

    Resolves source_level and unit from the actual note/listing identity
    (never from model output). Fans out property-level notes to all active
    units. Drops rows with invalid references or failed quote checks.

    Returns:
        (proposals, counters) where proposals is a list of dicts ready for
        DB insertion, and counters is {"quote_failed": N, "invalid_ref": N,
        "invalid_field": N, "arrangement_cleared": N}.
    """
    lookup = source_info["lookup"]
    valid_note_ids = source_info["valid_note_ids"]
    valid_listing_unit_ids = source_info["valid_listing_unit_ids"]

    proposals = []
    counters = {
        "quote_failed": 0,
        "invalid_ref": 0,
        "invalid_field": 0,
        "arrangement_cleared": 0,
    }

    for row in model_utilities:
        # -- Skip non-dict rows --
        if not isinstance(row, dict):
            logger.warning(
                "Row dropped: not a dict",
                extra={"row_type": type(row).__name__, "property_id": property_id},
            )
            counters["invalid_field"] += 1
            continue

        utility_type = row.get("utility_type", "")
        responsibility = row.get("responsibility", "")
        basis = row.get("basis", "")
        provider = row.get("provider") or ""
        evidence_quote = row.get("evidence_quote") or ""
        source = row.get("source", "")
        note_id = row.get("note_id")
        model_unit_id = row.get("unit_id")
        source_date_raw = row.get("source_date")
        arrangement = row.get("arrangement") or ""
        extras_raw = row.get("extras")

        # -- Validate enum fields --
        if utility_type not in VALID_UTILITY_TYPES:
            logger.warning(
                "Row dropped: invalid utility_type",
                extra={"utility_type": utility_type, "property_id": property_id},
            )
            counters["invalid_field"] += 1
            continue
        if responsibility not in VALID_RESPONSIBILITIES:
            logger.warning(
                "Row dropped: invalid responsibility",
                extra={"responsibility": responsibility, "property_id": property_id},
            )
            counters["invalid_field"] += 1
            continue
        if basis not in VALID_BASIS:
            logger.warning(
                "Row dropped: invalid basis",
                extra={"basis": basis, "property_id": property_id},
            )
            counters["invalid_field"] += 1
            continue

        # -- Resolve source: note or listing? --
        if source == "note":
            try:
                note_id_int = int(note_id) if note_id is not None else None
            except (ValueError, TypeError):
                logger.warning(
                    "Row dropped: note_id not an integer",
                    extra={"note_id": note_id, "property_id": property_id},
                )
                counters["invalid_ref"] += 1
                continue
            if note_id_int is None or note_id_int not in valid_note_ids:
                logger.warning(
                    "Row dropped: note_id not in input",
                    extra={"note_id": note_id, "property_id": property_id},
                )
                counters["invalid_ref"] += 1
                continue
            note_id = note_id_int
            src_key = ("note", note_id)
            src = lookup[src_key]
            source_type = "note"
            source_level = src["level"]
            source_unit_id = src["unit_id"]
            source_text = src["text"]
            source_text_hash = src["source_text_hash"]
            source_date = _parse_date(src["date_posted"])
            source_note_id = note_id

        elif source == "listing_description":
            # Validate that the unit_id the model cited is a real listing
            try:
                cited_uid = int(model_unit_id) if model_unit_id is not None else None
            except (ValueError, TypeError):
                logger.warning(
                    "Row dropped: listing unit_id not an integer",
                    extra={"unit_id": model_unit_id, "property_id": property_id},
                )
                counters["invalid_ref"] += 1
                continue
            if cited_uid not in valid_listing_unit_ids:
                logger.warning(
                    "Row dropped: listing unit_id not in input",
                    extra={"unit_id": cited_uid, "property_id": property_id},
                )
                counters["invalid_ref"] += 1
                continue
            src_key = ("listing", cited_uid)
            src = lookup[src_key]
            source_type = "listing_description"
            source_level = "unit"
            source_unit_id = cited_uid
            source_text = src["text"]
            source_text_hash = src["source_text_hash"]
            source_date = None
            source_note_id = None

        else:
            logger.warning(
                "Row dropped: invalid source type",
                extra={"source": source, "property_id": property_id},
            )
            counters["invalid_ref"] += 1
            continue

        # -- Quote verification --
        if basis == "catch_all":
            # Two lines, verify each separately
            lines = evidence_quote.split("\n")
            if len(lines) < 2:
                logger.warning(
                    "Row dropped: catch_all quote has < 2 lines",
                    extra={"property_id": property_id, "utility_type": utility_type},
                )
                counters["quote_failed"] += 1
                continue
            line1 = lines[0]
            line2 = "\n".join(lines[1:])
            if not check_quote(line1, source_text) or not check_quote(line2, source_text):
                logger.warning(
                    "Row dropped: catch_all quote check failed",
                    extra={"property_id": property_id, "utility_type": utility_type},
                )
                counters["quote_failed"] += 1
                continue
        else:
            if not check_quote(evidence_quote, source_text):
                logger.warning(
                    "Row dropped: quote check failed",
                    extra={"property_id": property_id, "utility_type": utility_type},
                )
                counters["quote_failed"] += 1
                continue

        # -- Arrangement verification --
        if arrangement and not check_quote(arrangement, source_text):
            logger.info(
                "Arrangement cleared: failed quote check",
                extra={"property_id": property_id, "utility_type": utility_type},
            )
            arrangement = ""
            counters["arrangement_cleared"] += 1

        # -- Payer-phrase guard --
        payer_conflict, payer_note = check_payer_phrase(
            evidence_quote, responsibility,
        )

        # -- Extras verification (oil/propane only) --
        if utility_type in ("oil", "propane") and extras_raw:
            extras = verify_extras_values(extras_raw, source_text)
        elif utility_type in ("oil", "propane"):
            extras = {k: None for k in EXTRAS_KEYS}
        else:
            extras = {}

        # -- Parse source_date --
        if source_date is None and source_date_raw:
            source_date = _parse_date(source_date_raw)

        # -- Fan-out: property-level note → one proposal per active unit --
        if source_level == "property":
            target_unit_ids = active_unit_ids
        else:
            # Unit-level note or listing: applies to its own unit only
            if source_unit_id not in active_unit_ids:
                logger.warning(
                    "Row dropped: source unit not in active units",
                    extra={
                        "source_unit_id": source_unit_id,
                        "property_id": property_id,
                    },
                )
                counters["invalid_ref"] += 1
                continue
            target_unit_ids = [source_unit_id]

        for uid in target_unit_ids:
            fp = compute_fingerprint(
                rentvine_unit_id=uid,
                utility_type=utility_type,
                source_type=source_type,
                source_note_id=source_note_id,
                source_text_hash=source_text_hash,
                evidence_quote=evidence_quote,
            )
            proposals.append({
                "rentvine_property_id": property_id,
                "rentvine_unit_id": uid,
                "utility_type": utility_type,
                "responsibility": responsibility,
                "basis": basis,
                "provider": provider,
                "arrangement": arrangement,
                "extras": extras,
                "evidence_quote": evidence_quote,
                "quote_verified": True,
                "source_type": source_type,
                "source_level": source_level,
                "source_note_id": source_note_id,
                "source_date": source_date,
                "source_text_hash": source_text_hash,
                "fingerprint": fp,
                "has_conflict": payer_conflict,
                "conflict_note": payer_note,
            })

    return proposals, counters


def _parse_date(val: str | None) -> date | None:
    """Parse a date string (YYYY-MM-DD or datetime) to a date, or None."""
    if not val:
        return None
    try:
        # Handle both "2024-01-15" and "2024-01-15 10:30:00" formats
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


# -- Per-property extraction orchestrator ----------------------------------

def extract_property_utilities(
    rv_client,
    ai_client,
    model_name: str,
    property_id: int,
    address: str,
    dry_run: bool = False,
    run=None,
) -> dict:
    """
    Extract utility proposals for one property.

    1. Fetch active units from Rentvine.
    2. Fetch and filter notes (unit-level + property-level).
    3. Fetch listing descriptions.
    4. Call Anthropic model.
    5. Validate output, build proposals, detect conflicts.
    6. Save to DB (unless dry_run).

    Returns dict with keys:
        active_units, notes_sent, notes_dropped, input_tokens, output_tokens,
        created, skipped, quote_failed, invalid_ref, invalid_field,
        arrangement_cleared, proposals (list, for dry_run display), error.
    """
    result = {
        "active_units": 0,
        "notes_sent": 0,
        "notes_dropped": 0,
        "lines_redacted": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "created": 0,
        "skipped": 0,
        "quote_failed": 0,
        "invalid_ref": 0,
        "invalid_field": 0,
        "arrangement_cleared": 0,
        "proposals": [],
        "error": None,
        "raw_reply_snippet": None,
    }

    # 1. Fetch active units
    active_units = fetch_active_units(rv_client, property_id)
    time.sleep(0.2)

    if not active_units:
        logger.info(
            "No active units, skipping",
            extra={"property_id": property_id},
        )
        return result

    active_unit_ids = [u["rentvine_unit_id"] for u in active_units]
    result["active_units"] = len(active_unit_ids)

    # 2. Fetch and filter notes
    all_converted_notes = []
    total_dropped = 0
    total_lines_redacted = 0

    # Unit-level notes
    for uid in active_unit_ids:
        raw = fetch_notes(rv_client, 7, uid)
        time.sleep(0.2)
        converted, dropped, redacted = filter_and_convert_notes(raw, "unit", uid)
        all_converted_notes.extend(converted)
        total_dropped += dropped
        total_lines_redacted += redacted

    # Property-level notes
    raw_prop = fetch_notes(rv_client, 6, property_id)
    time.sleep(0.2)
    converted_prop, dropped_prop, redacted_prop = filter_and_convert_notes(
        raw_prop, "property", None,
    )
    all_converted_notes.extend(converted_prop)
    total_dropped += dropped_prop
    total_lines_redacted += redacted_prop

    result["notes_sent"] = len(all_converted_notes)
    result["notes_dropped"] = total_dropped
    result["lines_redacted"] = total_lines_redacted

    # 3. Fetch listing descriptions
    listing_descs = []
    for uid in active_unit_ids:
        desc = fetch_listing_description(rv_client, property_id, uid)
        time.sleep(0.2)
        if desc:
            listing_descs.append({"unit_id": uid, "description": desc})

    # 4. Check for empty case
    if not all_converted_notes and not listing_descs:
        logger.info(
            "No utility data found, skipping model call",
            extra={"property_id": property_id},
        )
        return result

    # 5. Build prompt and call model
    user_prompt = build_user_prompt(
        property_id, address, all_converted_notes, listing_descs,
    )

    try:
        response = ai_client.messages.create(
            model=model_name,
            max_tokens=8192,
            temperature=0,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        result["error"] = f"Anthropic API error: {exc}"
        logger.error(
            "Model call failed",
            extra={"property_id": property_id, "error": str(exc)},
        )
        return result

    result["input_tokens"] = response.usage.input_tokens
    result["output_tokens"] = response.usage.output_tokens

    if response.stop_reason != "end_turn":
        result["error"] = f"Model stop_reason: {response.stop_reason}"
        logger.error(
            "Model truncated or stopped unexpectedly",
            extra={
                "property_id": property_id,
                "stop_reason": response.stop_reason,
            },
        )
        return result

    # 6. Parse JSON
    raw_text = response.content[0].text
    json_str = extract_json_object(raw_text)
    if json_str is None:
        result["error"] = "No JSON object found in model reply"
        result["raw_reply_snippet"] = _reply_snippet(raw_text)
        logger.error(
            "Model output has no JSON object",
            extra={"property_id": property_id},
        )
        return result

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        result["error"] = f"JSON parse error: {exc}"
        result["raw_reply_snippet"] = _reply_snippet(raw_text)
        logger.error(
            "Model output JSON parse failed",
            extra={"property_id": property_id, "error": str(exc)},
        )
        return result

    model_utilities = parsed.get("utilities", [])
    if not isinstance(model_utilities, list):
        result["error"] = "Model output 'utilities' is not a list"
        return result

    # 7. Build source lookup
    source_info = build_source_lookup(all_converted_notes, listing_descs)

    # 8. Validate and build proposals
    proposals, counters = validate_and_build_proposals(
        model_utilities, source_info, active_unit_ids, property_id,
    )
    result["quote_failed"] = counters["quote_failed"]
    result["invalid_ref"] = counters["invalid_ref"]
    result["invalid_field"] = counters["invalid_field"]
    result["arrangement_cleared"] = counters["arrangement_cleared"]

    # 9. Detect conflicts (before saving)
    proposals = detect_conflicts(proposals)

    # 10. Save or collect
    if dry_run:
        result["proposals"] = proposals
        result["created"] = len(proposals)
        return result

    # Import here to avoid circular imports at module level
    from django.db import transaction

    from utilities.models import UtilityProposal

    created = 0
    skipped = 0

    with transaction.atomic():
        for p in proposals:
            _, was_created = UtilityProposal.objects.get_or_create(
                fingerprint=p["fingerprint"],
                defaults={
                    "run": run,
                    "rentvine_property_id": p["rentvine_property_id"],
                    "rentvine_unit_id": p["rentvine_unit_id"],
                    "utility_type": p["utility_type"],
                    "responsibility": p["responsibility"],
                    "basis": p["basis"],
                    "provider": p["provider"],
                    "arrangement": p["arrangement"],
                    "extras": p["extras"],
                    "evidence_quote": p["evidence_quote"],
                    "quote_verified": p["quote_verified"],
                    "source_type": p["source_type"],
                    "source_level": p["source_level"],
                    "source_note_id": p["source_note_id"],
                    "source_date": p["source_date"],
                    "source_text_hash": p["source_text_hash"],
                    "has_conflict": p["has_conflict"],
                    "conflict_note": p["conflict_note"],
                    "status": "pending",
                },
            )
            if was_created:
                created += 1
            else:
                skipped += 1

    result["created"] = created
    result["skipped"] = skipped
    result["proposals"] = proposals
    return result

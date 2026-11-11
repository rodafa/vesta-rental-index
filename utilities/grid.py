"""
Pure functions for the utilities grid: cell status computation, grid building,
filter predicates, tile counts, and CSV export helpers.

No database access — all functions take plain dicts/lists.
"""

import re
from collections import defaultdict

# Grid column keys (the 8 utility columns displayed)
GRID_COLUMNS = ("electric", "water", "sewer", "gas", "oil", "propane", "trash", "internet")

# Alias types: water column is also satisfied by "well"; sewer by "septic"
ALIAS_MAP = {"water": "well", "sewer": "septic"}

_OK_LABELS = {
    "tenant": "Tenant",
    "owner": "Owner",
    "included_in_rent": "Included",
}

_PEND_LABELS = {
    "tenant": "Tenant",
    "owner": "Owner",
    "included_in_rent": "Included",
    "not_applicable": "N/A",
}

_ALIAS_LABELS = {
    "well": "Well",
    "septic": "Septic",
}

RESPONSIBILITY_DISPLAY = {
    "tenant": "Tenant",
    "owner": "Owner",
    "included_in_rent": "Included in Rent",
    "not_applicable": "Not Applicable",
    "unknown": "Unknown",
}


# ---------------------------------------------------------------------------
# Conflict classification
# ---------------------------------------------------------------------------

def conflict_class(responsibility):
    """
    Map responsibility to a conflict equivalence class.

    "owner" and "included_in_rent" mean the same thing to a tenant,
    so they are treated as one class for conflict purposes.
    """
    if responsibility in ("owner", "included_in_rent"):
        return "owner_side"
    return responsibility


# ---------------------------------------------------------------------------
# Blanket-proposal detection
# ---------------------------------------------------------------------------

SPECIFIC_UTILITY_WORDS = frozenset({
    "water", "sewer", "septic", "well", "electric", "gas",
    "oil", "propane", "trash", "garbage", "internet",
})

BLANKET_EXCLUSION_PHRASES = frozenset({
    "except", "excluding", "other than", "besides", "but",
})

_SPECIFIC_UTILITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in sorted(SPECIFIC_UTILITY_WORDS)) + r")\b",
    re.IGNORECASE,
)

_BLANKET_EXCLUSION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in sorted(BLANKET_EXCLUSION_PHRASES)) + r")\b",
    re.IGNORECASE,
)

_BLANKET_UTILITY_RE = re.compile(r"\butilit(?:y|ies)\b", re.IGNORECASE)

# A blanket statement ("tenant pays all utilities") does not prove a property
# has oil or propane.  These columns stay Miss unless a direct row exists.
BLANKET_EXCLUDED_COLUMNS = frozenset({"oil", "propane"})


# ---------------------------------------------------------------------------
# Column groups (for supersede logic)
# ---------------------------------------------------------------------------

COLUMN_GROUPS = {
    "water": "water_well", "well": "water_well",
    "sewer": "sewer_septic", "septic": "sewer_septic",
    "electric": "electric", "gas": "gas",
    "oil": "oil", "propane": "propane",
    "trash": "trash", "internet": "internet",
    "other": "other",
}


def column_group(utility_type):
    """Return the column group key for a utility type."""
    return COLUMN_GROUPS.get(utility_type, utility_type)


def siblings_to_supersede(chosen, pending_rows):
    """
    Return the ids of OTHER pending rows in the same column group as chosen.

    Args:
        chosen: dict with "id" and "utility_type".
        pending_rows: list of dicts with "id" and "utility_type".

    Never includes the chosen id. Never includes rows from a different group.
    """
    group = column_group(chosen["utility_type"])
    return [
        r["id"] for r in pending_rows
        if r["id"] != chosen["id"] and column_group(r["utility_type"]) == group
    ]


def approved_rows_to_replace(approved_types, utility_type):
    """
    Return the approved utility types in the same column group OTHER than
    the one being saved.

    Args:
        approved_types: iterable of utility_type strings the unit has approved.
        utility_type: the type being saved now.

    E.g. saving "water" when "well" is approved returns ["well"].
    """
    group = column_group(utility_type)
    return [t for t in approved_types if t != utility_type and column_group(t) == group]


# ---------------------------------------------------------------------------
# Approval / reject / manual-answer decision functions (pure, no Django)
# ---------------------------------------------------------------------------

def check_approve_proposal(proposal_dict):
    """
    Decide if a proposal can be approved.
    Returns (ok, error_message).
    """
    if proposal_dict.get("status") != "pending":
        return False, "Proposal is no longer pending."
    if proposal_dict.get("responsibility") == "unknown":
        return False, "Cannot approve an unknown-payer row. Use Change to set the answer."
    return True, ""


ALLOWED_TYPES_FOR_COLUMN = {
    "water": ("water", "well"),
    "sewer": ("sewer", "septic"),
    "electric": ("electric",),
    "gas": ("gas",),
    "oil": ("oil",),
    "propane": ("propane",),
    "trash": ("trash",),
    "internet": ("internet",),
}

MANUAL_RESPONSIBILITIES = ("tenant", "owner", "included_in_rent", "not_applicable")


def validate_reject(review_note):
    """Validate a reject action. Returns (ok, error_message)."""
    note = review_note.strip() if review_note else ""
    if len(note) > 500:
        return False, "Reject note must be 500 characters or fewer."
    return True, ""


def validate_manual_answer(column, utility_type, responsibility, provider, extras):
    """
    Validate a manual answer form submission.
    Returns (ok, error_message, cleaned_data).
    """
    # Validate column
    if column not in ALLOWED_TYPES_FOR_COLUMN:
        return False, "Invalid column.", {}

    # Validate utility_type for this column
    utility_type = (utility_type or "").strip()
    if utility_type not in ALLOWED_TYPES_FOR_COLUMN[column]:
        allowed = ", ".join(ALLOWED_TYPES_FOR_COLUMN[column])
        return False, f"Invalid type for this column. Allowed: {allowed}.", {}

    # Validate responsibility
    responsibility = (responsibility or "").strip()
    if responsibility not in MANUAL_RESPONSIBILITIES:
        return False, "Invalid responsibility.", {}

    # Validate provider
    provider = (provider or "").strip()
    if len(provider) > 200:
        return False, "Provider must be 200 characters or fewer.", {}

    # Validate extras (oil/propane only)
    cleaned_extras = {}
    if utility_type in ("oil", "propane"):
        tank_size = (extras.get("tank_size") or "").strip()
        tank_location = (extras.get("tank_location") or "").strip()
        if len(tank_size) > 100:
            return False, "Tank size must be 100 characters or fewer.", {}
        if len(tank_location) > 100:
            return False, "Tank location must be 100 characters or fewer.", {}
        if tank_size:
            cleaned_extras["tank_size"] = tank_size
        if tank_location:
            cleaned_extras["tank_location"] = tank_location

    return True, "", {
        "utility_type": utility_type,
        "responsibility": responsibility,
        "provider": provider,
        "extras": cleaned_extras,
    }


def blanket_columns_to_fill(approved_map, pending_proposals_map, responsibility):
    """
    Given a blanket proposal's responsibility, return the list of column
    utility_types to fill.

    Only the 6 non-excluded columns.  Skips columns that already have an
    approved row in the column group, or that have a pending non-unknown
    proposal in the column group.

    Args:
        approved_map: {utility_type: row_dict} for the unit.
        pending_proposals_map: {utility_type: [proposal_dicts]} for the unit.
        responsibility: the blanket responsibility string.

    Returns list of utility_type strings (grid column names).
    """
    approved_groups = {column_group(t) for t in approved_map}
    result = []
    for col in GRID_COLUMNS:
        if col in BLANKET_EXCLUDED_COLUMNS:
            continue
        grp = column_group(col)
        # Skip if any type in this group is already approved
        if grp in approved_groups:
            continue
        # Check for pending non-unknown proposals in the column group
        has_pending = False
        # Check the column itself and its alias
        types_in_group = [t for t, g in COLUMN_GROUPS.items() if g == grp]
        for t in types_in_group:
            for p in pending_proposals_map.get(t, []):
                if p.get("responsibility") != "unknown":
                    has_pending = True
                    break
            if has_pending:
                break
        if has_pending:
            continue
        result.append(col)
    return result


# ---------------------------------------------------------------------------
# Cell status
# ---------------------------------------------------------------------------

def compute_cell_status(
    utility_type,
    approved_row,
    alias_approved_row,
    pending_proposals,
    alias_pending_proposals,
    *,
    blanket_pending=(),
):
    """
    Determine the status of a single grid cell.

    Priority: approved direct > approved alias > pending direct > pending alias
    > pending blanket > miss.

    Returns {"status": str, "label": str, "flag": bool, "blanket": bool,
    "inferred": bool}.
    """
    _base = {"flag": False, "blanket": False, "inferred": False}

    # 1. Approved direct type
    if approved_row is not None:
        resp = approved_row["responsibility"]
        if resp == "not_applicable":
            return {**_base, "status": "na", "label": "NA"}
        return {**_base, "status": "ok", "label": _OK_LABELS.get(resp, resp)}

    # 2. Approved alias type
    if alias_approved_row is not None:
        resp = alias_approved_row["responsibility"]
        if resp == "not_applicable":
            return {**_base, "status": "na", "label": "NA"}
        alias_type = ALIAS_MAP.get(utility_type, "")
        return {**_base, "status": "ok", "label": _ALIAS_LABELS.get(alias_type, alias_type)}

    # 3. Pending direct type (exclude unknown — counts as no information)
    relevant = [p for p in pending_proposals if p.get("responsibility") != "unknown"]
    if relevant:
        label = _PEND_LABELS.get(relevant[0]["responsibility"], relevant[0]["responsibility"])
        flag, inferred = _compute_flag(relevant)
        return {**_base, "status": "pend", "label": label, "flag": flag, "inferred": inferred}

    # 4. Pending alias type
    alias_relevant = [p for p in alias_pending_proposals if p.get("responsibility") != "unknown"]
    if alias_relevant:
        alias_type = ALIAS_MAP.get(utility_type, "")
        label = _ALIAS_LABELS.get(alias_type, alias_type)
        flag, inferred = _compute_flag(alias_relevant)
        return {**_base, "status": "pend", "label": label, "flag": flag, "inferred": inferred}

    # 5. Pending blanket
    blanket_relevant = [p for p in blanket_pending if p.get("responsibility") != "unknown"]
    if blanket_relevant:
        label = _PEND_LABELS.get(
            blanket_relevant[0]["responsibility"],
            blanket_relevant[0]["responsibility"],
        )
        return {**_base, "status": "pend", "label": label, "flag": False, "blanket": True, "inferred": True}

    # 6. Info-only: pending rows exist but ALL are responsibility "unknown"
    all_pending = list(pending_proposals) + list(alias_pending_proposals)
    if any(p.get("responsibility") == "unknown" for p in all_pending):
        return {**_base, "status": "info", "label": "Info only"}

    # 7. Miss
    return {**_base, "status": "miss", "label": ""}


def _compute_flag(proposals):
    """
    Compute flag and inferred for a set of pending proposals.

    Returns (flag: bool, inferred: bool).
    flag = True only for real conflicts (has_conflict or disagreeing conflict_class).
    inferred = True when any proposal has basis "catch_all".
    """
    has_real_conflict = any(p.get("has_conflict") for p in proposals)
    classes = {conflict_class(p["responsibility"]) for p in proposals}
    classes_disagree = len(classes) > 1
    flag = has_real_conflict or classes_disagree
    inferred = any(p.get("basis") == "catch_all" for p in proposals)
    return flag, inferred


def is_blanket_proposal(p):
    """
    True if a proposal is a blanket utility statement (type "other" with a
    general "utility/utilities" quote that names no specific utility and
    contains no exclusion phrases).
    """
    if p.get("utility_type") != "other":
        return False
    if p.get("responsibility") not in ("tenant", "owner", "included_in_rent"):
        return False
    quote = p.get("evidence_quote", "")
    if not _BLANKET_UTILITY_RE.search(quote):
        return False
    if _SPECIFIC_UTILITY_RE.search(quote):
        return False
    if _BLANKET_EXCLUSION_RE.search(quote):
        return False
    return True


# ---------------------------------------------------------------------------
# Grid builder
# ---------------------------------------------------------------------------

def _natural_sort_key(s):
    """Sort key that orders numeric substrings by value ('2' before '10')."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', s)]


def occupancy_text(occupancy):
    """Format occupancy for display in template and CSV.

    Returns 'Occupied \u00b7 since Mon d, YYYY', 'Occupied', or 'Vacant'.
    """
    if not occupancy.get("occupied"):
        return "Vacant"
    move_in = occupancy.get("move_in_date")
    if move_in is None:
        return "Occupied"
    return f"Occupied \u00b7 since {move_in.strftime('%b')} {move_in.day}, {move_in.year}"


def build_grid(units, approved_map, proposals_map, notes_map, occupancy_map):
    """
    Build the full grid: one row per unit, one cell per utility column.

    Args:
        units: list of dicts from fetch_active_units()
        approved_map: {unit_id: {utility_type: row_dict}} from fetch_approved_utilities()
        proposals_map: {rentvine_unit_id: {utility_type: [proposal_dicts]}}
        notes_map: {unit_id: {"latest": str, "count": int, ...}}
        occupancy_map: {unit_id: {"occupied": bool, "move_in_date": date|None}}

    Returns list of row dicts, sorted by address then unit_number.
    """
    rows = []
    for unit in units:
        unit_id = unit["id"]
        rentvine_id = unit["rentvine_id"]

        approved = approved_map.get(unit_id, {})
        proposals = proposals_map.get(rentvine_id, {}) if rentvine_id else {}
        blanket_rows = [p for p in proposals.get("other", []) if is_blanket_proposal({**p, "utility_type": "other"})]

        cells = {}
        cell_list = []
        for col in GRID_COLUMNS:
            alias_col = ALIAS_MAP.get(col)

            approved_row = approved.get(col)
            alias_approved_row = approved.get(alias_col) if alias_col else None

            pending = proposals.get(col, [])
            alias_pending = proposals.get(alias_col, []) if alias_col else []

            blanket = [] if col in BLANKET_EXCLUDED_COLUMNS else blanket_rows
            cell = compute_cell_status(
                col, approved_row, alias_approved_row, pending, alias_pending,
                blanket_pending=blanket,
            )
            cells[col] = cell
            cell_list.append({"column": col, **cell})

        occ = occupancy_map.get(unit_id, {"occupied": False, "move_in_date": None})
        rows.append({
            "unit_id": unit_id,
            "rentvine_id": rentvine_id,
            "address": unit["display_address"],
            "unit_number": unit.get("unit_number", ""),
            "cells": cells,
            "cell_list": cell_list,
            "notes_summary": notes_map.get(unit_id, {"latest": "", "count": 0}),
            "occupancy": occ,
            "occupancy_text": occupancy_text(occ),
        })

    rows.sort(key=lambda r: (_natural_sort_key(r["address"]), _natural_sort_key(r["unit_number"])))
    return rows


def prepare_panel_proposals(proposals, utility_type):
    """
    Filter and annotate proposals for the side panel.

    Keeps column-specific and alias proposals (is_blanket=False),
    keeps blanket "other" proposals (is_blanket=True),
    drops non-blanket "other" proposals.

    Args:
        proposals: list of proposal dicts from the selector.
        utility_type: the clicked column name (e.g. "electric").

    Returns list of proposal dicts with added "is_blanket" key.
    """
    alias = ALIAS_MAP.get(utility_type)
    result = []
    for p in proposals:
        ptype = p.get("utility_type", "")
        if ptype == utility_type or (alias and ptype == alias):
            result.append({**p, "is_blanket": False})
        elif ptype == "other" and is_blanket_proposal(p):
            if utility_type not in BLANKET_EXCLUDED_COLUMNS:
                result.append({**p, "is_blanket": True})
    return result


# ---------------------------------------------------------------------------
# Filter predicates (operate on a single row)
# ---------------------------------------------------------------------------

def is_complete(row):
    """True if all 8 columns are ok or na."""
    return all(c["status"] in ("ok", "na") for c in row["cells"].values())


def has_gap(row):
    """True if any column is miss."""
    return any(c["status"] == "miss" for c in row["cells"].values())


def needs_review(row):
    """True if any column is pend or info."""
    return any(c["status"] in ("pend", "info") for c in row["cells"].values())


def is_flagged(row):
    """True if any cell has flag=True."""
    return any(c["flag"] for c in row["cells"].values())


def missing_oil_propane(row):
    """True if the oil or propane column is miss."""
    return (
        row["cells"]["oil"]["status"] == "miss"
        or row["cells"]["propane"]["status"] == "miss"
    )


# ---------------------------------------------------------------------------
# Tile counts (computed over ALL rows, not filtered)
# ---------------------------------------------------------------------------

def compute_tile_counts(rows):
    """Return a dict of the four summary tile counts."""
    return {
        "complete": sum(1 for r in rows if is_complete(r)),
        "needs_review": sum(1 for r in rows if needs_review(r)),
        "has_gap": sum(1 for r in rows if has_gap(r)),
        "missing_oil_propane": sum(1 for r in rows if missing_oil_propane(r)),
    }


# ---------------------------------------------------------------------------
# Filtering (applied to row list)
# ---------------------------------------------------------------------------

VALID_FILTERS = {"all", "gap", "review", "oil_propane", "flagged"}


def apply_filter(rows, filter_name, query=""):
    """
    Apply a named filter and optional address search to the row list.
    Returns a new list (does not mutate the original).
    """
    result = rows

    if query:
        q = query.lower()
        result = [r for r in result if q in r["address"].lower()]

    if filter_name == "gap":
        result = [r for r in result if has_gap(r)]
    elif filter_name == "review":
        result = [r for r in result if needs_review(r)]
    elif filter_name == "oil_propane":
        result = [r for r in result if missing_oil_propane(r)]
    elif filter_name == "flagged":
        result = [r for r in result if is_flagged(r)]

    return result


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def csv_cell_text(cell):
    """Convert a cell status dict to human-readable CSV text."""
    status = cell["status"]
    label = cell["label"]
    if status == "ok":
        return label
    if status == "na":
        return "NA"
    if status == "pend":
        text = f"Needs review: {label}" if label else "Needs review"
        if cell.get("blanket"):
            text += " (blanket)"
        elif cell.get("inferred"):
            text += " (inferred)"
        return text
    if status == "info":
        return "Info only"
    return "Missing"


def sanitize_csv_value(value):
    """Prefix values that start with formula-trigger characters."""
    if value and value[0] in ("=", "+", "-", "@"):
        return f"'{value}"
    return value


def build_csv_rows(rows):
    """Build CSV data rows from grid rows. Returns list of string lists."""
    result = []
    for row in rows:
        line = [
            sanitize_csv_value(row["address"]),
            sanitize_csv_value(row["unit_number"]),
            sanitize_csv_value(row["occupancy_text"]),
        ]
        for col in GRID_COLUMNS:
            line.append(sanitize_csv_value(csv_cell_text(row["cells"][col])))
        note_text = row["notes_summary"].get("latest", "")
        line.append(sanitize_csv_value(note_text))
        result.append(line)
    return result


# ---------------------------------------------------------------------------
# Provider display name normalisation
# ---------------------------------------------------------------------------

# Manual alias: casefold form → canonical casefold group key.
# "Duke" alone should be grouped with "Duke Energy".
PROVIDER_ALIASES = {"duke": "duke energy"}


def build_provider_display_map(provider_strings):
    """
    Build a mapping {original_string: display_name} from a list of provider
    strings (with duplicates — frequency matters).

    Rules:
    - Collapse internal whitespace in each string.
    - Group by casefold (after alias resolution).
    - Display name: the most-frequent variant that is NOT all-uppercase and
      NOT all-lowercase.  If every variant is all-caps or all-lower, keep the
      most-frequent variant as-is (never title-case, so FCC stays FCC).
    """
    from collections import Counter

    # 1. Collapse whitespace, keep originals
    collapsed = []
    for s in provider_strings:
        c = re.sub(r"\s+", " ", s).strip()
        if c:
            collapsed.append(c)

    # 2. Group by casefold (with alias resolution)
    groups = defaultdict(list)
    for c in collapsed:
        key = c.casefold()
        key = PROVIDER_ALIASES.get(key, key)
        groups[key].append(c)

    # 3. Pick display name per group
    display_map = {}
    for key, variants in groups.items():
        freq = Counter(variants)
        # Prefer mixed-case variants
        mixed = [(v, n) for v, n in freq.items()
                 if not v.isupper() and not v.islower()]
        if mixed:
            best = max(mixed, key=lambda vn: vn[1])[0]
        else:
            best = freq.most_common(1)[0][0]
        for v in freq:
            display_map[v] = best

    return display_map

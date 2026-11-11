"""
Pure functions for the utilities grid: cell status computation, grid building,
filter predicates, tile counts, and CSV export helpers.

No database access — all functions take plain dicts/lists.
"""

import re

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

    Returns {"status": "ok"|"na"|"pend"|"miss", "label": str, "flag": bool,
    "blanket": bool}.
    """
    # 1. Approved direct type
    if approved_row is not None:
        resp = approved_row["responsibility"]
        if resp == "not_applicable":
            return {"status": "na", "label": "NA", "flag": False, "blanket": False}
        return {"status": "ok", "label": _OK_LABELS.get(resp, resp), "flag": False, "blanket": False}

    # 2. Approved alias type
    if alias_approved_row is not None:
        resp = alias_approved_row["responsibility"]
        if resp == "not_applicable":
            return {"status": "na", "label": "NA", "flag": False, "blanket": False}
        alias_type = ALIAS_MAP.get(utility_type, "")
        return {"status": "ok", "label": _ALIAS_LABELS.get(alias_type, alias_type), "flag": False, "blanket": False}

    # 3. Pending direct type (exclude unknown — counts as no information)
    relevant = [p for p in pending_proposals if p.get("responsibility") != "unknown"]
    if relevant:
        label = _PEND_LABELS.get(relevant[0]["responsibility"], relevant[0]["responsibility"])
        flag = _compute_flag(relevant)
        return {"status": "pend", "label": label, "flag": flag, "blanket": False}

    # 4. Pending alias type
    alias_relevant = [p for p in alias_pending_proposals if p.get("responsibility") != "unknown"]
    if alias_relevant:
        alias_type = ALIAS_MAP.get(utility_type, "")
        label = _ALIAS_LABELS.get(alias_type, alias_type)
        flag = _compute_flag(alias_relevant)
        return {"status": "pend", "label": label, "flag": flag, "blanket": False}

    # 5. Pending blanket
    blanket_relevant = [p for p in blanket_pending if p.get("responsibility") != "unknown"]
    if blanket_relevant:
        label = _PEND_LABELS.get(
            blanket_relevant[0]["responsibility"],
            blanket_relevant[0]["responsibility"],
        )
        return {"status": "pend", "label": label, "flag": True, "blanket": True}

    # 6. Miss
    return {"status": "miss", "label": "", "flag": False, "blanket": False}


def _compute_flag(proposals):
    """True if proposals have a conflict, catch-all basis, or disagree on responsibility."""
    if any(p.get("has_conflict") for p in proposals):
        return True
    if any(p.get("basis") == "catch_all" for p in proposals):
        return True
    responsibilities = {p["responsibility"] for p in proposals}
    if len(responsibilities) > 1:
        return True
    return False


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

            cell = compute_cell_status(
                col, approved_row, alias_approved_row, pending, alias_pending,
                blanket_pending=blanket_rows,
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
    """True if any column is pend."""
    return any(c["status"] == "pend" for c in row["cells"].values())


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
        return text
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

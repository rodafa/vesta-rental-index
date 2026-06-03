"""
LeadSimple process classification, filtering, and unit matching.

Pure functions — no models, no side effects beyond logging.
"""

import logging
import re
from datetime import date, datetime, time

import zoneinfo

from integrations.property_meld.services import normalize_address

logger = logging.getLogger(__name__)

ET = zoneinfo.ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Process type classification map (full UUIDs from live probe)
# ---------------------------------------------------------------------------

PROCESS_TYPE_MAP = {
    # Monthly owner notes
    "1865ad03-f740-4a99-b1a2-e2440430f3f4": {
        "category": "renewal",
        "target": "monthly",
    },  # 06 Lease Renewals
    "44ac428c-30c3-4548-8a59-6cc5c687e5f8": {
        "category": "renewal",
        "target": "monthly",
    },  # 070 Lease Renewals (legacy dup)
    "e0dfad31-5459-4578-a0a1-b062180e769b": {
        "category": "move_out",
        "target": "monthly",
    },  # 08 Move Out
    "8311a377-6d68-48a0-855d-145fd1380ff4": {
        "category": "move_out",
        "target": "monthly",
    },  # 09 Move Outs (legacy dup)
    "98e21401-58d3-4a6a-b6b0-f22dd11dc831": {
        "category": "issues",
        "target": "monthly",
    },  # Issues
    "83f4af06-ad96-4ca7-9d21-5a1b00087ac6": {
        "category": "rehab_to_turn",
        "target": "monthly",
    },  # 09 Rehab to Turn
    "864f1bf2-53c8-4135-9196-9f6ecf15cf16": {
        "category": "move_in",
        "target": "monthly",
    },  # 05 Move Ins
    "9b99ac28-0775-46cf-8537-12b4b08719b2": {
        "category": "onboarding",
        "target": "monthly",
    },  # 01 Owner Onboarding
    "7e16b91a-bb9c-447e-937d-db3ce45f9588": {
        "category": "onboarding",
        "target": "monthly",
    },  # 02 Property Onboarding
    # Weekly leasing email
    "942fa5d5-6fc6-4495-8c94-bdb311525172": {
        "category": "marketing",
        "target": "weekly",
    },  # 03 Property Marketing Process
    "c9927e92-5c41-4c33-8a9e-8dbd6312eeb6": {
        "category": "application",
        "target": "weekly",
    },  # 04 Applications
    # Excluded from v1
    "75d01712-66f5-44fe-9595-f571e2449896": {
        "category": "late_rent",
        "target": "excluded",
    },  # 07 Late Rent
    "ee35a777-a854-47d4-82c1-87d022926783": {
        "category": "offboarding",
        "target": "excluded",
    },  # 10 Property Off Boarding
}


# ---------------------------------------------------------------------------
# Unit number normalization
# ---------------------------------------------------------------------------

_UNIT_PREFIX_RE = re.compile(
    r"^(?:unit|apt|apartment|suite|ste|#)\s*", re.IGNORECASE
)


def normalize_unit_number(raw):
    """
    Normalize a unit number for matching.

    Strips leading Unit/Apt/Suite/# tokens, lowercases, removes
    punctuation and whitespace.

    Examples: "Unit 3" -> "3", "Apt A" -> "a", "#31" -> "31",
              "Suite 200" -> "200", "" -> ""
    """
    if not raw:
        return ""
    s = raw.strip()
    s = _UNIT_PREFIX_RE.sub("", s)
    s = re.sub(r"[.,#\-]", "", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_processes(processes):
    """
    Classify a list of raw API process dicts by process_type_id.

    Returns list of dicts, each augmented with 'category' and 'target'.
    Unknown process_type_ids are logged and excluded.
    """
    classified = []
    for proc in processes:
        pt = proc.get("process_type") or {}
        pt_id = pt.get("id") or proc.get("process_type_id") or ""

        mapping = PROCESS_TYPE_MAP.get(pt_id)
        if mapping is None:
            logger.warning(
                "leadsimple_unknown_process_type",
                extra={
                    "process_type_id": pt_id,
                    "process_type_name": pt.get("name", ""),
                    "process_id": proc.get("id", ""),
                },
            )
            continue

        classified.append({
            **proc,
            "category": mapping["category"],
            "target": mapping["target"],
        })

    return classified


# ---------------------------------------------------------------------------
# Monthly window filter
# ---------------------------------------------------------------------------


def _parse_dt_et(iso_str):
    """Parse an ISO datetime string and convert to America/New_York."""
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        # API returns UTC-like strings without explicit tz — treat as UTC
        import zoneinfo as zi
        dt = dt.replace(tzinfo=zi.ZoneInfo("UTC"))
    return dt.astimezone(ET)


def filter_for_monthly(classified, period_start, period_end):
    """
    Keep only monthly-target processes within the reporting window.

    Window (ET calendar-month boundaries):
    - Include if closed_at IS NULL (still open), OR
    - closed_at (in ET) falls within [period_start, period_end] inclusive.

    period_start and period_end are date objects representing the
    first and last day of the reporting month.
    """
    window_start = datetime.combine(period_start, time.min, tzinfo=ET)
    window_end = datetime.combine(period_end, time(23, 59, 59, 999999), tzinfo=ET)

    result = []
    for proc in classified:
        if proc["target"] != "monthly":
            continue

        closed_at_str = proc.get("closed_at")
        if closed_at_str is None:
            # Still open — include
            result.append(proc)
            continue

        closed_et = _parse_dt_et(closed_at_str)
        if closed_et is None:
            result.append(proc)
            continue

        if window_start <= closed_et <= window_end:
            result.append(proc)

    return result


# ---------------------------------------------------------------------------
# Unit matching
# ---------------------------------------------------------------------------


def build_unit_lookup(units_queryset):
    """
    Build a lookup dict: (normalized_address, normalized_unit_number) -> Unit.

    For single-unit properties (unit_number is empty), the key uses "" as
    the unit_number component.
    """
    lookup = {}
    for unit in units_queryset.select_related("property"):
        addr = normalize_address(unit.address_line_1 or unit.property.address_line_1)
        unit_num = normalize_unit_number(unit.unit_number)
        lookup[(addr, unit_num)] = unit
    return lookup


def _extract_address(proc):
    """Extract the property address from a process's properties[0]."""
    props = proc.get("properties") or []
    if not props:
        return None, None, None
    prop = props[0]
    address = prop.get("address") or ""
    unit_obj = prop.get("unit") or {}
    raw_unit_number = unit_obj.get("unit_number") or ""
    return address, raw_unit_number, prop


def match_to_units(processes, unit_lookup):
    """
    Match each process to a core Unit via normalized address + unit_number.

    Returns list of (process_dict, unit_or_none) tuples.
    Processes with zero properties are skipped with a log.
    """
    matched = []
    for proc in processes:
        address, raw_unit_number, prop_data = _extract_address(proc)
        if prop_data is None:
            logger.info(
                "leadsimple_no_property",
                extra={"process_id": proc.get("id", "")},
            )
            continue

        norm_addr = normalize_address(address)
        norm_unit = normalize_unit_number(raw_unit_number)

        unit = unit_lookup.get((norm_addr, norm_unit))

        # Fallback: try empty unit_number (single-unit match)
        if unit is None and norm_unit:
            unit = unit_lookup.get((norm_addr, ""))

        matched.append((proc, unit))

    return matched


# ---------------------------------------------------------------------------
# Owner cross-check
# ---------------------------------------------------------------------------


def _normalize_email(email):
    """Lowercase and strip an email for comparison."""
    return (email or "").strip().lower()


def cross_check_owner(proc, matched_owner):
    """
    Compare the process's contact_roles owner against the matched core Owner.

    Informational log only — never changes the match. Compares on
    normalized email first, name as fallback.
    """
    contact_roles = proc.get("contact_roles")
    if not contact_roles:
        return

    # contact_roles is a list of role objects
    owner_contacts = []
    if isinstance(contact_roles, list):
        for role in contact_roles:
            key = (role.get("key") or "").lower()
            if key in ("owners", "owner"):
                for contact in role.get("contacts") or []:
                    owner_contacts.append(contact)

    if not owner_contacts:
        return

    matched_email = _normalize_email(matched_owner.email)
    matched_name = (matched_owner.name or "").strip().lower()

    for contact in owner_contacts:
        contact_emails = [_normalize_email(e) for e in (contact.get("emails") or [])]
        contact_name = (contact.get("name") or "").strip().lower()

        # Check email match first
        if matched_email and contact_emails:
            if matched_email in contact_emails:
                return  # Match confirmed
            logger.info(
                "leadsimple_owner_email_mismatch",
                extra={
                    "process_id": proc.get("id", ""),
                    "matched_owner": matched_owner.name,
                    "contact_name": contact.get("name", ""),
                },
            )
            return

        # Fallback: name comparison
        if matched_name and contact_name:
            if matched_name == contact_name:
                return  # Match confirmed
            logger.info(
                "leadsimple_owner_name_mismatch",
                extra={
                    "process_id": proc.get("id", ""),
                    "matched_owner": matched_owner.name,
                    "contact_name": contact.get("name", ""),
                },
            )


# ---------------------------------------------------------------------------
# Group by owner
# ---------------------------------------------------------------------------


def group_by_owner(matched_processes):
    """
    Group (process, unit) pairs by owner.

    Returns dict: {owner_id: {"owner": Owner, "processes": [process_dict, ...]}}.
    Processes with no matched unit are skipped with a log.
    """
    groups = {}
    for proc, unit in matched_processes:
        if unit is None:
            logger.info(
                "leadsimple_no_unit_match",
                extra={
                    "process_id": proc.get("id", ""),
                    "address": (proc.get("properties") or [{}])[0].get("address", ""),
                },
            )
            continue

        # Walk: unit -> property -> portfolio -> owners
        prop = unit.property
        portfolio = prop.portfolio
        if not portfolio:
            continue

        for owner in portfolio.owners.filter(is_active=True):
            cross_check_owner(proc, owner)
            if owner.pk not in groups:
                groups[owner.pk] = {"owner": owner, "processes": []}
            groups[owner.pk]["processes"].append(proc)

    return groups


def build_owner_data(owner, processes):
    """
    Build the data dict for a single owner's monthly pipeline report.

    Groups processes by category for template consumption.
    """
    by_category = {}
    for proc in processes:
        cat = proc.get("category", "unknown")
        by_category.setdefault(cat, []).append({
            "process_id": proc.get("id"),
            "name": proc.get("name", ""),
            "category": cat,
            "stage_name": (proc.get("stage") or {}).get("name", ""),
            "stage_status": (proc.get("stage") or {}).get("status", ""),
            "created_at": proc.get("created_at"),
            "closed_at": proc.get("closed_at"),
            "address": ((proc.get("properties") or [{}])[0]).get("address", ""),
            "unit_number": (
                ((proc.get("properties") or [{}])[0]).get("unit", {}) or {}
            ).get("unit_number", ""),
        })

    return {
        "owner_first_name": owner.first_name or (owner.name or "Owner").split()[0],
        "processes_by_category": by_category,
        "total_count": len(processes),
    }

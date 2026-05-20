"""
LeadSimple pipeline data source.

Fetches pipeline deals via the live API, then matches to properties by address.
Used by monthly owner reports, dashboard draft notes, and weekly reports.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Keywords used to classify deal stage names
_STAGE_KEYWORDS = {
    "application": ["application", "screening", "apply", "applicant"],
    "move_in": ["move-in", "move in", "onboard", "move_in"],
    "renewal": ["renew", "renewal", "extension"],
    "late_rent": ["late", "notice", "eviction", "delinquent", "past due"],
    "move_out": ["move-out", "move out", "vacate", "move_out"],
    "issues": ["issue", "dispute", "complaint", "concern", "violation"],
}

# Regex to extract the leading street number + street name from an address.
_STREET_RE = re.compile(r"^(\d+\s+\S+(?:\s+\S+)?)")


def _normalize_address(addr: str) -> str:
    """Lowercase, strip whitespace, remove periods/commas/# symbols."""
    return re.sub(r"[.,#]", "", (addr or "").strip().lower())


def _extract_street_key(addr: str) -> str:
    """
    Extract normalized street number + street name for comparison.
    Returns e.g. "123 main st" from "123 Main St., Apt B, Austin TX".
    Falls back to the full normalized address if pattern doesn't match.
    """
    normalized = _normalize_address(addr)
    m = _STREET_RE.match(normalized)
    return m.group(1) if m else normalized


def fetch_all_active_deals() -> list:
    """Fetch all active LeadSimple processes. Returns [] on any error."""
    try:
        from integrations.leadsimple.client import LeadSimpleClient

        return LeadSimpleClient().get_active_processes()
    except Exception:
        logger.warning("LeadSimple: could not fetch active processes", exc_info=True)
        return []


def fetch_all_deals(include_closed_since=None) -> list:
    """Fetch all processes, optionally including recently-closed ones.

    Args:
        include_closed_since: Optional date. Include processes closed on or after
            this date. If None, only returns open processes.

    Returns [] on any error.
    """
    try:
        from integrations.leadsimple.client import LeadSimpleClient

        return LeadSimpleClient().fetch_all_processes(
            include_closed_since=include_closed_since
        )
    except Exception:
        logger.warning("LeadSimple: could not fetch processes", exc_info=True)
        return []


def classify_deal(deal: dict) -> str:
    """Classify a deal into a pipeline type by stage_name keyword matching."""
    stage = (deal.get("stage_name") or "").lower()
    for pipeline_type, keywords in _STAGE_KEYWORDS.items():
        if any(kw in stage for kw in keywords):
            return pipeline_type
    return "other"


def match_deals_to_address(address, all_deals):
    """Match deals to an address string using street-key comparison.

    Args:
        address: Property address string (e.g. "123 Main St").
        all_deals: List of deal dicts from fetch_all_active_deals() or fetch_all_deals().

    Returns list of matched deal dicts with 'pipeline_type' added.
    """
    if not address:
        return []

    property_street_key = _extract_street_key(address)
    matched = []
    for deal in all_deals:
        deal_address = deal.get("address") or deal.get("name") or ""
        deal_street_key = _extract_street_key(deal_address)

        if deal_street_key == property_street_key:
            pipeline_type = classify_deal(deal)
            matched.append({
                "name": deal.get("name", ""),
                "stage_name": deal.get("stage_name", ""),
                "created_at": deal.get("created_at", ""),
                "closed_at": deal.get("closed_at", ""),
                "comments": deal.get("comments", ""),
                "pipeline_type": pipeline_type,
            })
    return matched


def get_property_pipeline_context(property_obj, all_deals: list,
                                  pipeline_types=None) -> dict:
    """Match deals to property_obj, grouped by pipeline type.

    Args:
        property_obj: Object with .address_line_1 attribute.
        all_deals: List of deal dicts.
        pipeline_types: Optional set/list of types to include (e.g. {"application",
            "move_in"}). If None, all types are included.

    Returns dict with keys: applications, move_ins, renewals, late_rent,
    move_outs, issues, other.
    """
    street = (property_obj.address_line_1 or "").strip()
    if not street:
        return _empty_context()

    logger.debug(
        "LeadSimple address match: property %s (%s), street_key=%s",
        property_obj.pk, street, _extract_street_key(street),
    )

    matched = match_deals_to_address(street, all_deals)

    context = _empty_context()
    type_to_key = {
        "application": "applications",
        "move_in": "move_ins",
        "renewal": "renewals",
        "late_rent": "late_rent",
        "move_out": "move_outs",
        "issues": "issues",
        "other": "other",
    }

    for deal in matched:
        pt = deal["pipeline_type"]
        if pipeline_types and pt not in pipeline_types:
            continue
        key = type_to_key.get(pt, "other")
        context[key].append(deal)

    return context


def get_resolved_for_property(property_obj, all_deals, week_start, week_end):
    """Return deals matched to property that were closed within [week_start, week_end].

    Args:
        property_obj: Object with .address_line_1 attribute.
        all_deals: List of deal dicts (should come from fetch_all_deals with
            include_closed_since=week_start).
        week_start: date — inclusive start.
        week_end: date — inclusive end.

    Returns list of matched deal dicts that have a closed_at within range.
    """
    from datetime import date as date_type

    street = (property_obj.address_line_1 or "").strip()
    matched = match_deals_to_address(street, all_deals)

    resolved = []
    for deal in matched:
        closed_str = deal.get("closed_at", "")
        if not closed_str:
            continue
        try:
            closed_date = date_type.fromisoformat(closed_str)
        except (ValueError, TypeError):
            continue
        if week_start <= closed_date <= week_end:
            resolved.append(deal)
    return resolved


def _empty_context() -> dict:
    return {
        "applications": [],
        "move_ins": [],
        "renewals": [],
        "late_rent": [],
        "move_outs": [],
        "issues": [],
        "other": [],
    }

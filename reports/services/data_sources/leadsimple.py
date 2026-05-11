"""
LeadSimple data source for monthly owner reports.
Fetches pipeline deals via the live API, then matches to properties by address.
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
    """
    Fetch all active LeadSimple processes. Returns [] on any error.
    """
    try:
        from integrations.leadsimple.client import LeadSimpleClient

        return LeadSimpleClient().get_active_processes()
    except Exception:
        logger.warning("LeadSimple: could not fetch active processes", exc_info=True)
        return []


def classify_deal(deal: dict) -> str:
    """Classify a deal into a pipeline type by stage_name keyword matching."""
    stage = (deal.get("stage_name") or "").lower()
    for pipeline_type, keywords in _STAGE_KEYWORDS.items():
        if any(kw in stage for kw in keywords):
            return pipeline_type
    return "other"


def get_property_pipeline_context(property_obj, all_deals: list) -> dict:
    """
    Match deals from all_deals to property_obj using normalized street
    number + street name comparison.
    Groups matched deals by pipeline type.

    Returns:
        {
            applications: [...],
            move_ins: [...],
            renewals: [...],
            late_rent: [...],
            move_outs: [...],
            other: [...],
        }
    """
    street = (property_obj.address_line_1 or "").strip()
    if not street:
        return _empty_context()

    property_street_key = _extract_street_key(street)

    logger.debug(
        "LeadSimple address match: property %s (%s), street_key=%s",
        property_obj.pk, street, property_street_key,
    )

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
                "comments": deal.get("comments", ""),
                "pipeline_type": pipeline_type,
            })

    context = _empty_context()
    for deal in matched:
        pt = deal["pipeline_type"]
        if pt == "application":
            context["applications"].append(deal)
        elif pt == "move_in":
            context["move_ins"].append(deal)
        elif pt == "renewal":
            context["renewals"].append(deal)
        elif pt == "late_rent":
            context["late_rent"].append(deal)
        elif pt == "move_out":
            context["move_outs"].append(deal)
        elif pt == "issues":
            context["issues"].append(deal)
        else:
            context["other"].append(deal)

    return context


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

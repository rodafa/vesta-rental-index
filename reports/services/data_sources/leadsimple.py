"""
LeadSimple data source for monthly owner reports.
Fetches pipeline deals via the live API, then matches to properties by address.
"""
import logging

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


def fetch_all_active_deals() -> list:
    """
    Fetch all active LeadSimple deals. Returns [] on any error.
    Keeps the API call at pipeline level, not per-property.
    """
    try:
        from integrations.leadsimple.client import LeadSimpleClient

        return LeadSimpleClient().get_active_deals()
    except Exception:
        logger.warning("LeadSimple: could not fetch active deals", exc_info=True)
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
    Match deals from all_deals to property_obj by substring address match.
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
    street = (property_obj.address_line_1 or "").strip().lower()
    if not street:
        return _empty_context()

    matched = []
    for deal in all_deals:
        deal_name = (deal.get("name") or "").lower()
        if street in deal_name:
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

"""
PropertyMeld data source for monthly owner reports.
Queries the local Meld model — no live API calls needed (synced hourly).
"""
import logging
import re
from datetime import datetime

from django.db.models import Q
from django.utils.timezone import make_aware

logger = logging.getLogger(__name__)

# Statuses that represent owner- or tenant-initiated cancellations.
# These are excluded entirely from owner notes per reporting policy.
_CANCELLED_STATUSES = {"MANAGER_CANCELED", "TENANT_CANCELED"}

# Regex to extract the leading street number + street name from an address.
# Captures "123 Main St" from "123 Main St, Austin, TX 78701" etc.
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


def get_melds_for_period(property_obj, month_start, month_end) -> list:
    """
    Return melds for property_obj that were open at any point during
    [month_start, month_end). Cancelled melds are excluded entirely.

    "Open at any point" means created before month_end AND (created or modified
    on or after month_start).

    Matching strategy: extract street number + street name from both sides,
    normalize (lowercase, strip punctuation), and compare for equality.
    Falls back to icontains query, then post-filters with normalized matching.
    """
    from maintenance.models import Meld

    street = (property_obj.address_line_1 or "").strip()
    if not street:
        return []

    property_street_key = _extract_street_key(street)

    period_start_dt = make_aware(datetime(month_start.year, month_start.month, month_start.day))
    period_end_dt = make_aware(datetime(month_end.year, month_end.month, month_end.day))

    # Use icontains as a broad pre-filter, then post-filter with normalized matching
    candidates = (
        Meld.objects.filter(
            property_address__icontains=street.split()[0],  # filter by street number
        )
        .exclude(status__in=_CANCELLED_STATUSES)
        .filter(source_created_at__lt=period_end_dt)
        .filter(
            Q(source_modified_at__gte=period_start_dt)
            | Q(source_created_at__gte=period_start_dt)
        )
        .order_by("-source_created_at")
    )

    logger.debug(
        "PropertyMeld address match (fallback): property %s (%s), street_key=%s",
        property_obj.pk, street, property_street_key,
    )

    results = []
    for m in candidates:
        meld_street_key = _extract_street_key(m.property_address or "")
        if meld_street_key != property_street_key:
            continue

        results.append({
            "meld_id": m.property_meld_id,
            "description": m.brief_description,
            "status": m.status,
            "category": m.category,
            "priority": m.priority,
            "has_vendor": bool(m.assigned_vendor_name),
            "scheduled_date": m.scheduled_date.isoformat() if m.scheduled_date else None,
            "created_at": m.source_created_at.date().isoformat() if m.source_created_at else None,
            "completed_date": m.completed_date.isoformat() if m.completed_date else None,
        })

    return results

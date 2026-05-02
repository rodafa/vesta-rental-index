"""
PropertyMeld data source for monthly owner reports.
Queries the local Meld model — no live API calls needed (synced hourly).
"""
import logging
from datetime import datetime

from django.db.models import Q
from django.utils.timezone import make_aware

logger = logging.getLogger(__name__)

# Statuses that represent owner- or tenant-initiated cancellations.
# These are excluded entirely from owner notes per reporting policy.
_CANCELLED_STATUSES = {"MANAGER_CANCELED", "TENANT_CANCELED"}


def get_melds_for_period(property_obj, month_start, month_end) -> list:
    """
    Return melds for property_obj that were open at any point during
    [month_start, month_end). Cancelled melds are excluded entirely.

    "Open at any point" means created before month_end AND (created or modified
    on or after month_start).

    Address matching: Meld.property_address ICONTAINS property.address_line_1
    (using only the street component to avoid city/state noise).
    """
    from maintenance.models import Meld

    street = (property_obj.address_line_1 or "").strip()
    if not street:
        return []

    period_start_dt = make_aware(datetime(month_start.year, month_start.month, month_start.day))
    period_end_dt = make_aware(datetime(month_end.year, month_end.month, month_end.day))

    melds = (
        Meld.objects.filter(
            property_address__icontains=street,
        )
        .exclude(status__in=_CANCELLED_STATUSES)
        .filter(source_created_at__lt=period_end_dt)
        .filter(
            Q(source_modified_at__gte=period_start_dt)
            | Q(source_created_at__gte=period_start_dt)
        )
        .order_by("-source_created_at")
    )

    results = []
    for m in melds:
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

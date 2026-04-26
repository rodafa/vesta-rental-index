"""
PropertyMeld data source for monthly owner reports.
Queries the local Meld model — no live API calls needed (synced hourly).
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_recent_melds(property_obj, since_days: int = 30) -> list:
    """
    Return melds associated with property_obj that were created or modified
    within the past since_days days.

    Address matching: Meld.property_address ICONTAINS property.address_line_1
    (using only the street component to avoid city/state noise).
    """
    from maintenance.models import Meld

    street = (property_obj.address_line_1 or "").strip()
    if not street:
        return []

    cutoff = timezone.now() - timedelta(days=since_days)

    melds = Meld.objects.filter(
        property_address__icontains=street,
    ).filter(
        Q(source_modified_at__gte=cutoff) | Q(source_created_at__gte=cutoff)
    ).order_by("-source_created_at")

    results = []
    for m in melds:
        results.append({
            "meld_id": m.property_meld_id,
            "description": m.brief_description,
            "status": m.status,
            "category": m.category,
            "priority": m.priority,
            "vendor": m.assigned_vendor_name,
            "scheduled_date": m.scheduled_date.isoformat() if m.scheduled_date else None,
            "created_at": m.source_created_at.date().isoformat() if m.source_created_at else None,
            "completed_date": m.completed_date.isoformat() if m.completed_date else None,
        })

    return results

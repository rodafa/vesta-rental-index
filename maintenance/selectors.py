"""
Data selectors for maintenance email products.

Logic lives here; views and commands are thin orchestrators.
"""

import logging

from django.db.models import Q

from integrations.property_meld.mappers import EMAIL_CANCELED_BUCKET, EMAIL_CLOSED_BUCKET

from .models import Meld

logger = logging.getLogger(__name__)


def _meld_to_dict(meld):
    """Convert a Meld instance to a plain dict for template/AI consumption."""
    unit_address = ""
    if meld.unit:
        unit_address = meld.unit.display_address
    elif meld.property:
        unit_address = str(meld.property)
    else:
        unit_address = meld.property_address

    return {
        "id": meld.pk,
        "property_meld_id": meld.property_meld_id,
        "reference_id": meld.reference_id,
        "brief_description": meld.brief_description,
        "category": meld.category,
        "status": meld.status,
        "priority": meld.priority,
        "assigned_vendor_name": meld.assigned_vendor_name,
        "owner_approval_status": meld.owner_approval_status,
        "scheduled_date": meld.scheduled_date,
        "completion_date": meld.completion_date or meld.marked_complete,
        "started": meld.started,
        "source_created_at": meld.source_created_at,
        "unit_address": unit_address,
    }


def get_owner_maintenance_data(owner, week_start, week_end):
    """
    Gather melds for a single owner, grouped into email sections.

    Path: Owner -> portfolios -> properties -> Meld.property FK.

    Returns dict with keys:
        owner_first_name, open_melds, closed_melds, canceled_melds, needs_approval
    """
    portfolio_ids = owner.portfolios.values_list("id", flat=True)
    if not portfolio_ids:
        return {
            "owner_first_name": _first_name(owner),
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "needs_approval": [],
        }

    # Base queryset: melds linked to owner's properties via property FK
    base_qs = Meld.objects.filter(
        property__portfolio_id__in=portfolio_ids,
    )

    # Exclude merged melds
    base_qs = base_qs.exclude(~Q(merged_meld_data={}))

    # Exclude routine lawn/mowing melds
    base_qs = base_qs.exclude(
        Q(category__in=["EXTERIOR", "LANDSCAPING"])
        & (
            Q(brief_description__icontains="mow")
            | Q(brief_description__icontains="lawn care")
            | Q(brief_description__icontains="grass")
        )
    )

    terminal_statuses = EMAIL_CLOSED_BUCKET | EMAIL_CANCELED_BUCKET

    # Open: not in any terminal status (no date bound — all currently open)
    open_melds = list(
        base_qs.exclude(status__in=terminal_statuses)
        .select_related("unit", "property")
        .order_by("-source_created_at")
    )

    # Closed: completed within the week window
    closed_melds = list(
        base_qs.filter(status__in=EMAIL_CLOSED_BUCKET)
        .filter(
            Q(
                completion_date__date__gte=week_start,
                completion_date__date__lte=week_end,
            )
            | Q(
                completion_date__isnull=True,
                marked_complete__date__gte=week_start,
                marked_complete__date__lte=week_end,
            )
        )
        .select_related("unit", "property")
        .order_by("-completion_date", "-marked_complete")
    )

    # Canceled: terminal date within the week window
    canceled_melds = list(
        base_qs.filter(status__in=EMAIL_CANCELED_BUCKET)
        .filter(
            Q(
                source_modified_at__date__gte=week_start,
                source_modified_at__date__lte=week_end,
            )
            | Q(
                source_modified_at__isnull=True,
                marked_complete__date__gte=week_start,
                marked_complete__date__lte=week_end,
            )
        )
        .select_related("unit", "property")
        .order_by("-source_modified_at", "-marked_complete")
    )

    # Needs approval: subset of open
    needs_approval = [
        m for m in open_melds if m.owner_approval_status == "Requested"
    ]

    return {
        "owner_first_name": _first_name(owner),
        "open_melds": [_meld_to_dict(m) for m in open_melds],
        "closed_melds": [_meld_to_dict(m) for m in closed_melds],
        "canceled_melds": [_meld_to_dict(m) for m in canceled_melds],
        "needs_approval": [_meld_to_dict(m) for m in needs_approval],
    }


def _first_name(owner):
    """Extract a first name for greeting."""
    if owner.first_name:
        return owner.first_name
    if owner.name:
        return owner.name.split()[0]
    return "Owner"

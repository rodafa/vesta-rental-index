"""
Data selectors for maintenance email products.

Logic lives here; views and commands are thin orchestrators.
"""

import logging
from decimal import Decimal

from django.db.models import Q, Sum

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


def get_owner_maintenance_data(owner, period_start, period_end):
    """
    Gather melds for a single owner, grouped into email sections.

    Path: Owner -> portfolios -> properties -> Meld.property FK.

    Returns dict with keys:
        owner_first_name, open_melds, closed_melds, canceled_melds,
        needs_approval, _has_data
    """
    portfolio_ids = owner.portfolios.values_list("id", flat=True)
    if not portfolio_ids:
        return {
            "owner_first_name": _first_name(owner),
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "needs_approval": [],
            "_has_data": False,
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

    # Closed: completed within the period window
    closed_melds = list(
        base_qs.filter(status__in=EMAIL_CLOSED_BUCKET)
        .filter(
            Q(
                completion_date__date__gte=period_start,
                completion_date__date__lte=period_end,
            )
            | Q(
                completion_date__isnull=True,
                marked_complete__date__gte=period_start,
                marked_complete__date__lte=period_end,
            )
        )
        .select_related("unit", "property")
        .order_by("-completion_date", "-marked_complete")
    )

    # Canceled: terminal date within the period window
    canceled_melds = list(
        base_qs.filter(status__in=EMAIL_CANCELED_BUCKET)
        .filter(
            Q(
                source_modified_at__date__gte=period_start,
                source_modified_at__date__lte=period_end,
            )
            | Q(
                source_modified_at__isnull=True,
                marked_complete__date__gte=period_start,
                marked_complete__date__lte=period_end,
            )
        )
        .select_related("unit", "property")
        .order_by("-source_modified_at", "-marked_complete")
    )

    # Needs approval: subset of open
    needs_approval = [
        m for m in open_melds if m.owner_approval_status == "Requested"
    ]

    open_dicts = [_meld_to_dict(m) for m in open_melds]
    closed_dicts = [_meld_to_dict(m) for m in closed_melds]
    canceled_dicts = [_meld_to_dict(m) for m in canceled_melds]

    return {
        "owner_first_name": _first_name(owner),
        "open_melds": open_dicts,
        "closed_melds": closed_dicts,
        "canceled_melds": canceled_dicts,
        "needs_approval": [_meld_to_dict(m) for m in needs_approval],
        "_has_data": bool(open_dicts or closed_dicts or canceled_dicts),
    }


def _first_name(owner):
    """Extract a first name for greeting."""
    if owner.first_name:
        return owner.first_name
    if owner.name:
        return owner.name.split()[0]
    return "Owner"


# ---------------------------------------------------------------------------
# Meld cost aggregation (from RentVine bills / bill charges)
# ---------------------------------------------------------------------------


def get_meld_cost(meld) -> Decimal | None:
    """
    Total non-voided charge amount across all non-voided bills linked to
    a single meld, or None if no charges exist.
    """
    from accounting.models import BillCharge

    return BillCharge.objects.filter(
        bill__meld=meld,
        bill__is_voided=False,
        is_voided=False,
    ).aggregate(total=Sum("amount"))["total"]


def get_meld_costs(meld_ids: list[int]) -> dict[int, Decimal]:
    """
    Batch cost lookup.  Returns {meld_pk: Decimal} for melds that have
    charges.  Missing keys mean no charges (None semantics).
    """
    from accounting.models import BillCharge

    if not meld_ids:
        return {}

    rows = (
        BillCharge.objects.filter(
            bill__meld_id__in=meld_ids,
            bill__is_voided=False,
            is_voided=False,
        )
        .values("bill__meld_id")
        .annotate(total=Sum("amount"))
    )
    return {row["bill__meld_id"]: row["total"] for row in rows}

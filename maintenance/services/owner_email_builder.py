"""
Build maintenance summary email data and render HTML for owner emails.
"""

import logging
from datetime import date

from django.db.models import Q, Sum
from django.template.loader import render_to_string

from integrations.property_meld.mappers import EMAIL_CANCELED_BUCKET, EMAIL_CLOSED_BUCKET
from maintenance.models import Meld
from properties.models import Owner, Unit

logger = logging.getLogger(__name__)


def build_maintenance_email_data(owner: Owner, date_start: date, date_end: date) -> dict:
    """
    Assemble maintenance email data for a single owner.

    Returns dict with keys:
        open_melds: list of meld dicts (all currently open for this owner)
        closed_melds: list of meld dicts (closed within date range)
        canceled_melds: list of meld dicts (canceled within date range)
        total_spend: Decimal total of expenditures for closed melds
        unit_count: number of owner units with melds
    """
    # Find owner's units: Owner → portfolios → properties → units
    portfolio_ids = owner.portfolios.values_list("id", flat=True)
    owner_units = Unit.objects.filter(
        property__portfolio_id__in=portfolio_ids,
        is_active=True,
    )
    unit_ids = set(owner_units.values_list("id", flat=True))

    if not unit_ids:
        return {
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "total_spend": 0,
            "unit_count": 0,
        }

    # Base queryset: melds linked to owner's units, exclude merged melds
    base_qs = Meld.objects.filter(unit_fk_id__in=unit_ids).exclude(
        # Skip melds that were merged into another meld
        ~Q(merged_meld_data={})
    )

    # Exclude lawn mowing melds — they don't need owner summaries
    base_qs = base_qs.exclude(
        Q(category__in=["EXTERIOR", "LANDSCAPING"])
        & (
            Q(brief_description__icontains="mow")
            | Q(brief_description__icontains="lawn care")
            | Q(brief_description__icontains="grass")
        )
    )

    # Open: status NOT in CLOSED/CANCELED buckets (no date bound)
    open_statuses = EMAIL_CLOSED_BUCKET | EMAIL_CANCELED_BUCKET
    open_melds = list(
        base_qs.exclude(status__in=open_statuses)
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-source_created_at")
    )

    # Closed: completion_date (fallback marked_complete) within date range
    closed_melds = list(
        base_qs.filter(status__in=EMAIL_CLOSED_BUCKET)
        .filter(
            Q(completion_date__date__gte=date_start, completion_date__date__lte=date_end)
            | Q(
                completion_date__isnull=True,
                marked_complete__date__gte=date_start,
                marked_complete__date__lte=date_end,
            )
        )
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-completion_date", "-marked_complete")
    )

    # Canceled: status in CANCELED bucket, source_modified_at within date range
    canceled_melds = list(
        base_qs.filter(
            status__in=EMAIL_CANCELED_BUCKET,
            source_modified_at__date__gte=date_start,
            source_modified_at__date__lte=date_end,
        )
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-source_modified_at")
    )

    # Total spend for closed melds
    closed_meld_ids = [m.pk for m in closed_melds]
    total_spend = 0
    if closed_meld_ids:
        from maintenance.models import Expenditure

        total_spend = (
            Expenditure.objects.filter(
                meld_id__in=closed_meld_ids,
                status__in=["BILLED", "APPROVED"],
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )

    return {
        "open_melds": [_meld_to_dict(m) for m in open_melds],
        "closed_melds": [_meld_to_dict(m) for m in closed_melds],
        "canceled_melds": [_meld_to_dict(m) for m in canceled_melds],
        "total_spend": total_spend,
        "unit_count": len(unit_ids),
    }


def _meld_to_dict(meld: Meld) -> dict:
    """Convert a Meld instance to a dict for template rendering."""
    from maintenance.models import Expenditure

    # Calculate cost for this meld
    cost = (
        Expenditure.objects.filter(
            meld=meld, status__in=["BILLED", "APPROVED"]
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    # Determine display summary
    summary = meld.staff_summary or meld.ai_summary or ""

    # Completion date for display
    completion = meld.completion_date or meld.marked_complete

    return {
        "id": meld.pk,
        "property_meld_id": meld.property_meld_id,
        "reference_id": meld.reference_id,
        "brief_description": meld.brief_description,
        "category": meld.category,
        "summary": summary,
        "status": meld.status,
        "priority": meld.priority,
        "assigned_vendor_name": meld.assigned_vendor_name,
        "owner_approval_status": meld.owner_approval_status,
        "completion_date": completion,
        "source_created_at": meld.source_created_at,
        "cost": cost,
        "tenant_rating": meld.tenant_rating,
        "unit_address": meld.unit_fk.display_address if meld.unit_fk else meld.property_address,
        "summary_status": meld.summary_status,
    }


def build_maintenance_email_html(
    owner: Owner, email_data: dict, date_start: date, date_end: date
) -> str:
    """Render the maintenance email HTML template."""
    context = {
        "owner": owner,
        "owner_first_name": owner.first_name or owner.name.split()[0],
        "date_start": date_start,
        "date_end": date_end,
        "open_melds": email_data["open_melds"],
        "closed_melds": email_data["closed_melds"],
        "canceled_melds": email_data["canceled_melds"],
        "total_spend": email_data["total_spend"],
        "open_count": len(email_data["open_melds"]),
        "closed_count": len(email_data["closed_melds"]),
        "canceled_count": len(email_data["canceled_melds"]),
    }
    return render_to_string(
        "maintenance/owner_maintenance_email.html", context
    )

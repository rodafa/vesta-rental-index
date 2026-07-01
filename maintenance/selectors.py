"""
Data selectors for maintenance email products.

Logic lives here; views and commands are thin orchestrators.
"""

import html
import logging
import re
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils.html import strip_tags

from integrations.property_meld.mappers import EMAIL_CANCELED_BUCKET, EMAIL_CLOSED_BUCKET

from .models import Meld, WorkOrder

logger = logging.getLogger(__name__)

# Regex to collapse whitespace runs left after HTML stripping.
_WHITESPACE_RUN = re.compile(r"\s+")

# Strip RentVine's PM-migration boilerplate (anchored on the marker text, not
# on <hr> — some descriptions contain legitimate <hr> tags from zInspector).
_MIGRATION_BOILERPLATE = re.compile(
    r"(<hr\s*/?>)?\s*Migrated from Property Meld.*",
    re.IGNORECASE | re.DOTALL,
)

# Split on the first block-level break to extract a short title.
_FIRST_LINE_SPLIT = re.compile(r"<br\s*/?>|</?p\s*/?>|<hr\s*/?>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Shared meld-gathering helper
# ---------------------------------------------------------------------------


def _get_filtered_base_qs(portfolio_ids):
    """
    Return a base Meld queryset for the given portfolio IDs with standard
    exclusions applied (merged melds, routine lawn/mowing).

    Single source of truth for what counts as a "real" meld.
    """
    qs = Meld.objects.filter(property__portfolio_id__in=portfolio_ids)

    # Exclude merged melds
    qs = qs.exclude(~Q(merged_meld_data={}))

    # Exclude routine lawn/mowing melds
    qs = qs.exclude(
        Q(category__in=["EXTERIOR", "LANDSCAPING"])
        & (
            Q(brief_description__icontains="mow")
            | Q(brief_description__icontains="lawn care")
            | Q(brief_description__icontains="grass")
        )
    )

    return qs


def _gather_meld_buckets(base_qs, period_start, period_end):
    """
    Given a filtered base queryset, sort melds into open/closed/canceled/
    needs_approval buckets.

    Returns dict: {open_melds, closed_melds, canceled_melds, needs_approval, _has_data}
    where each list contains Meld model instances (with unit/property selected).
    """
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

    return {
        "open_melds": open_melds,
        "closed_melds": closed_melds,
        "canceled_melds": canceled_melds,
        "needs_approval": needs_approval,
        "_has_data": bool(open_melds or closed_melds or canceled_melds),
    }


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------


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


def get_portfolio_maintenance_data(portfolio, period_start, period_end):
    """
    Gather melds for a single portfolio, grouped into email sections.

    Path: Portfolio -> properties -> Meld.property FK.

    Returns dict with keys:
        open_melds, closed_melds, canceled_melds, needs_approval, _has_data
    where each meld list contains plain dicts (via _meld_to_dict).
    """
    base_qs = _get_filtered_base_qs([portfolio.pk])
    buckets = _gather_meld_buckets(base_qs, period_start, period_end)

    return {
        "open_melds": [_meld_to_dict(m) for m in buckets["open_melds"]],
        "closed_melds": [_meld_to_dict(m) for m in buckets["closed_melds"]],
        "canceled_melds": [_meld_to_dict(m) for m in buckets["canceled_melds"]],
        "needs_approval": [_meld_to_dict(m) for m in buckets["needs_approval"]],
        "_has_data": buckets["_has_data"],
    }


def get_owner_maintenance_data(owner, period_start, period_end):
    """
    Gather melds for a single owner, grouped into email sections.

    Path: Owner -> portfolios -> properties -> Meld.property FK.

    Returns dict with keys:
        owner_first_name, open_melds, closed_melds, canceled_melds,
        needs_approval, _has_data
    """
    portfolio_ids = list(owner.portfolios.values_list("id", flat=True))
    if not portfolio_ids:
        return {
            "owner_first_name": _first_name(owner),
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "needs_approval": [],
            "_has_data": False,
        }

    base_qs = _get_filtered_base_qs(portfolio_ids)
    buckets = _gather_meld_buckets(base_qs, period_start, period_end)

    return {
        "owner_first_name": _first_name(owner),
        "open_melds": [_meld_to_dict(m) for m in buckets["open_melds"]],
        "closed_melds": [_meld_to_dict(m) for m in buckets["closed_melds"]],
        "canceled_melds": [_meld_to_dict(m) for m in buckets["canceled_melds"]],
        "needs_approval": [_meld_to_dict(m) for m in buckets["needs_approval"]],
        "_has_data": buckets["_has_data"],
    }


def get_portfolio_maintenance_summary(portfolio, period_start, period_end):
    """
    Light maintenance overview for a single portfolio's monthly section.

    Returns dict with counts of open, closed, and canceled items for the period.
    No detailed meld dicts, no dollar figures (those come from PortfolioStatement).

    Used by monthly owner notes — does NOT replace get_owner_maintenance_data().
    """
    base_qs = _get_filtered_base_qs([portfolio.pk])

    terminal_statuses = EMAIL_CLOSED_BUCKET | EMAIL_CANCELED_BUCKET

    open_count = base_qs.exclude(status__in=terminal_statuses).count()

    closed_count = (
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
        .count()
    )

    canceled_count = (
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
        .count()
    )

    total = open_count + closed_count + canceled_count
    has_data = bool(total)

    # Build summary_line and detect notable items
    summary_line = ""
    notable_items = []

    if has_data:
        # Build count breakdown
        breakdown = []
        if open_count:
            breakdown.append(f"{open_count} open")
        if closed_count:
            breakdown.append(f"{closed_count} completed")
        if canceled_count:
            breakdown.append(f"{canceled_count} canceled")
        summary_line = (
            f"{total} work order{'s' if total != 1 else ''} this period"
            f" ({', '.join(breakdown)})."
        )

        # Detect notable open melds: EMERGENCY/HIGH priority or habitability categories
        _HABITABILITY_CATEGORIES = {"PLUMBING", "ELECTRICAL", "HVAC", "ROOFING", "STRUCTURAL"}
        open_melds = (
            base_qs.exclude(status__in=terminal_statuses)
            .filter(
                Q(priority__in=["EMERGENCY", "HIGH"])
                | Q(category__in=_HABITABILITY_CATEGORIES)
            )
            .select_related("unit", "property")
        )
        for meld in open_melds:
            notable_items.append({
                "brief_description": meld.brief_description,
                "category": meld.category,
                "priority": meld.priority,
            })

        if notable_items:
            # Add notable detail to summary_line
            notable_descs = []
            for item in notable_items[:3]:  # Cap at 3
                prio = item["priority"]
                cat = (item["category"] or "").lower()
                desc = item["brief_description"] or "issue"
                if prio == "EMERGENCY":
                    notable_descs.append(f"emergency {cat} {desc}".rstrip())
                elif prio == "HIGH":
                    notable_descs.append(f"high-priority {cat} {desc}".rstrip())
                else:
                    notable_descs.append(f"{cat} {desc}".rstrip())

            if len(notable_descs) == 1:
                summary_line += f" One {notable_descs[0]} is being addressed."
            else:
                summary_line += (
                    f" Notable: {', '.join(notable_descs[:2])}"
                    + (" and more" if len(notable_descs) > 2 else "")
                    + " are being addressed."
                )

    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "canceled_count": canceled_count,
        "summary_line": summary_line,
        "notable_items": notable_items,
        "_has_data": has_data,
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


# ---------------------------------------------------------------------------
# WorkOrder selectors (RentVine-native, replaces Meld for weekly emails)
# ---------------------------------------------------------------------------


def _strip_migration_boilerplate(raw_html: str) -> str:
    """Remove RentVine PM-migration trailer from raw HTML."""
    return _MIGRATION_BOILERPLATE.sub("", raw_html)


def _clean_html(raw_html: str) -> str:
    """Strip migration boilerplate, HTML tags, and collapse whitespace.

    Block-level tags (<br>, <p>, <hr>) are replaced with spaces before
    stripping so adjacent words don't run together.
    """
    if not raw_html:
        return ""
    cleaned = _strip_migration_boilerplate(raw_html)
    # Insert spaces at block-level boundaries before stripping tags
    cleaned = _FIRST_LINE_SPLIT.sub(" ", cleaned)
    return _WHITESPACE_RUN.sub(" ", html.unescape(strip_tags(cleaned))).strip()


def _extract_title(raw_html: str) -> str:
    """
    Extract a short title from a work-order description.

    Strips migration boilerplate, takes the first non-empty segment
    (before the first <br>, <p>, or <hr> tag), cleans HTML tags +
    entities, and caps at 120 characters.
    """
    if not raw_html:
        return ""
    stripped = _strip_migration_boilerplate(raw_html)
    segments = _FIRST_LINE_SPLIT.split(stripped)
    # Take the first non-empty segment (descriptions starting with <p>
    # produce an empty first element).
    first_line = ""
    for seg in segments:
        candidate = _WHITESPACE_RUN.sub(" ", html.unescape(strip_tags(seg))).strip()
        if candidate:
            first_line = candidate
            break
    if len(first_line) > 120:
        first_line = first_line[:120].rsplit(" ", 1)[0] + "..."
    return first_line


def _work_order_to_dict(wo, cost=None):
    """Convert a WorkOrder instance to a plain dict for template/AI consumption."""
    unit_address = ""
    property_address = ""
    unit_label = ""

    if wo.unit:
        unit_address = wo.unit.display_address
        unit_label = (wo.unit.unit_number or "").strip()
    if wo.property:
        property_address = (wo.property.address_line_1 or str(wo.property)).strip()
    if not property_address and unit_address:
        property_address = unit_address
    if not unit_address and property_address:
        unit_address = property_address
    if not property_address:
        property_address = "Address not specified"
    if not unit_address:
        unit_address = property_address

    return {
        "id": wo.pk,
        "work_order_number": wo.work_order_number,
        "title": _extract_title(wo.description),
        "description": _clean_html(wo.description),
        "vendor_name": wo.vendor_name,
        "is_owner_approved": wo.is_owner_approved,
        "estimated_amount": wo.estimated_amount,
        "date_closed": wo.date_closed,
        "scheduled_start_date": wo.scheduled_start_date,
        "source_created_at": wo.source_created_at,
        "source_modified_at": wo.source_modified_at,
        "unit_address": unit_address,
        "property_address": property_address,
        "unit_label": unit_label,
        "cost": cost,
    }


def _group_by_address(wo_dicts):
    """Group work-order dicts by property_address, preserving insertion order.

    Returns [{"property_address": str, "work_orders": [dict], "show_unit": bool}].
    show_unit is True only when the group spans more than one distinct
    non-blank unit_label (i.e. a multi-unit property with WOs in different units).
    """
    from collections import OrderedDict

    groups = OrderedDict()
    for wo in wo_dicts:
        groups.setdefault(wo["property_address"], []).append(wo)
    return [
        {
            "property_address": addr,
            "work_orders": wos,
            "show_unit": len({w["unit_label"] for w in wos if w["unit_label"]}) > 1,
        }
        for addr, wos in groups.items()
    ]


def get_work_order_costs(rentvine_ids: list[int]) -> dict[int, Decimal]:
    """
    Batch cost lookup for work orders via the native bill.work_order_id link.

    Returns {work_order_rentvine_id: Decimal} for work orders that have
    charges. Missing keys mean no charges (None semantics).
    """
    from accounting.models import BillCharge

    if not rentvine_ids:
        return {}

    rows = (
        BillCharge.objects.filter(
            bill__work_order_id__in=rentvine_ids,
            bill__is_voided=False,
            is_voided=False,
        )
        .values("bill__work_order_id")
        .annotate(total=Sum("amount"))
    )
    return {row["bill__work_order_id"]: row["total"] for row in rows}


def get_portfolio_work_order_data(portfolio, period_start, period_end):
    """
    Gather work orders for a single portfolio, grouped into four email buckets.

    All work orders for the portfolio are included — no isSharedWithOwner filter,
    no isInternal exclusion.

    Buckets (using validated lifecycle fields, not status-ID enums):
      - open: not closed, not cancelled, no scheduled_start_date
      - scheduled: not closed, not cancelled, scheduled_start_date IS set
      - completed: closed within the period window
      - cancelled: cancelled within the period window

    Open/scheduled work orders are included regardless of age.
    Completed/cancelled are windowed to the period.
    """
    base_qs = WorkOrder.objects.filter(portfolio=portfolio, is_active=True).select_related(
        "unit", "property"
    )

    # "Open" = not cancelled AND not closed
    open_q = Q(cancelled_by_user_id__isnull=True) & Q(
        closed_by_user_id__isnull=True, date_closed__isnull=True
    )

    open_qs = list(
        base_qs.filter(open_q, scheduled_start_date__isnull=True).order_by(
            "-source_created_at"
        )
    )

    scheduled_qs = list(
        base_qs.filter(open_q, scheduled_start_date__isnull=False).order_by(
            "-source_created_at"
        )
    )

    # Completed: closed_by_user_id set OR date_closed set.
    # Window by date_closed; fall back to source_modified_at when
    # date_closed is null.
    completed_qs = list(
        base_qs.filter(
            Q(closed_by_user_id__isnull=False) | Q(date_closed__isnull=False)
        )
        .filter(
            Q(
                date_closed__gte=period_start,
                date_closed__lte=period_end,
            )
            | Q(
                date_closed__isnull=True,
                source_modified_at__date__gte=period_start,
                source_modified_at__date__lte=period_end,
            )
        )
        .order_by("-date_closed", "-source_modified_at")
    )

    # Cancelled: cancelled_by_user_id set.
    # source_modified_at is the cancel-time proxy — RentVine has no
    # dedicated cancelled-at field.
    cancelled_qs = list(
        base_qs.filter(cancelled_by_user_id__isnull=False)
        .filter(
            source_modified_at__date__gte=period_start,
            source_modified_at__date__lte=period_end,
        )
        .order_by("-source_modified_at")
    )

    # Batch cost lookup
    all_rv_ids = [
        wo.rentvine_id
        for wo in open_qs + scheduled_qs + completed_qs + cancelled_qs
    ]
    costs = get_work_order_costs(all_rv_ids)

    def _to_dicts(wo_list):
        return [
            _work_order_to_dict(wo, cost=costs.get(wo.rentvine_id))
            for wo in wo_list
        ]

    return {
        "open": _group_by_address(_to_dicts(open_qs)),
        "scheduled": _group_by_address(_to_dicts(scheduled_qs)),
        "completed": _group_by_address(_to_dicts(completed_qs)),
        "cancelled": _group_by_address(_to_dicts(cancelled_qs)),
        "_has_data": bool(
            open_qs or scheduled_qs or completed_qs or cancelled_qs
        ),
    }

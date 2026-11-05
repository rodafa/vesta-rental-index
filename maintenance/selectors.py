"""
Data selectors for maintenance email products.

Logic lives here; views and commands are thin orchestrators.
"""

import html
import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils.html import strip_tags

from integrations.property_meld.mappers import EMAIL_CANCELED_BUCKET, EMAIL_CLOSED_BUCKET

from .models import Meld, WorkOrder
from .sanitizers import sanitize_banned_words
from .zinspector import parse_zinspector_description

logger = logging.getLogger(__name__)

# Regex to collapse whitespace runs left after HTML stripping.
_WHITESPACE_RUN = re.compile(r"\s+")

# Strip RentVine's PM-migration boilerplate (anchored on the marker text, not
# on <hr> — some descriptions contain legitimate <hr> tags from zInspector).
_MIGRATION_BOILERPLATE = re.compile(
    r"(<hr\s*/?>)?\s*Migrated from Property Meld.*",
    re.IGNORECASE | re.DOTALL,
)

# Split on the first block-level break or newline to extract a short title.
_FIRST_LINE_SPLIT = re.compile(r"<br\s*/?>|</?p\s*/?>|<hr\s*/?>|\r?\n", re.IGNORECASE)

# Strip internal WO references like "(WO #7ddd9fc9)" from titles.
_INTERNAL_WO_REF = re.compile(r"\s*\(WO\s+#[0-9a-fA-F]+\)")

# Routine recurring lawn-care phrases to exclude from the weekly maintenance
# email (WorkOrder path).  Each is matched case-insensitively as a substring
# of WorkOrder.description.  Verified against prod: 166 matches, zero
# false-positives against one-off/complaint/non-lawn rows.
LAWN_CARE_EXCLUSION_PATTERNS = (
    "seasonal lawn care",
    "bi-weekly lawn care",
    "bi-weekly lawncare",
    "weekly lawn maintenance",
    "bi-weekly mowing",
    "recurring lawn maintenance",
)

# zInspector trade category → owner-facing title.
# Vocabulary confirmed from prod data (120 WOs, 8 distinct categories).
# Unknown trades pass through with a warning log.
_TRADE_TITLE_MAP = {
    "Maintenance": "Punch List",
    "Cleaning": "Cleaning",
    "Carpet Cleaning": "Cleaning",
    "Landscaping": "Lawn Care",
    "Repairs": "Repairs",
    "Painting": "Painting",
    "Appliance": "Appliance",
    "Other": "Recommended",
}


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
    (before the first <br>, <p>, <hr>, or newline), cleans HTML tags +
    entities, strips internal WO refs, and caps at 120 characters.
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
    first_line = _INTERNAL_WO_REF.sub("", first_line).strip()
    if len(first_line) > 120:
        first_line = first_line[:120].rsplit(" ", 1)[0] + "..."
    return first_line


def _process_raw_description(raw_description, wo_number=None):
    """Process a raw work-order description into title, description, and zInspector fields.

    Shared by both the Rentvine path (_work_order_to_dict) and the Mason path
    (mason_selectors overrides).

    Returns a dict with keys: title, description, _raw_description, is_punch_list,
    zi_items, zi_item_count, zi_human_summary, zi_category.
    """
    title = _extract_title(raw_description)
    description = _clean_html(raw_description)
    is_punch_list = False
    zi_items = None
    zi_item_count = None
    zi_human_summary = None
    zi_category = None

    zi = parse_zinspector_description(raw_description)
    if zi is not None and len(zi["items"]) >= 2:
        cat = zi["category"]
        mapped = _TRADE_TITLE_MAP.get(cat)
        if mapped is None:
            logger.warning(
                "zinspector_unknown_trade",
                extra={"trade": cat, "wo": wo_number},
            )
            mapped = cat
        title = mapped
        # Description is the operator's free-text note only — never the raw
        # zInspector block (contains internal URLs).  Empty when the prefix
        # was just the trade word with no colon.
        description = zi["human_summary"]
        is_punch_list = True
        zi_items = zi["items"]
        zi_item_count = len(zi["items"])
        zi_human_summary = zi["human_summary"]
        zi_category = cat
    elif zi is not None and len(zi["items"]) == 1:
        cat = zi["category"]
        mapped = _TRADE_TITLE_MAP.get(cat)
        if mapped is None:
            logger.warning(
                "zinspector_unknown_trade",
                extra={"trade": cat, "wo": wo_number},
            )
            mapped = cat
        item = zi["items"][0]
        item_text = item["detail"] if item.get("detail") else (
            f"{item['room']}: {item['component']}" if item.get("room") else ""
        )
        title = mapped
        description = item_text
        zi_items = zi["items"]
        zi_item_count = 1
        zi_human_summary = zi["human_summary"]
        zi_category = cat

    # Sanitize banned words from title, description, and zInspector item details
    title = sanitize_banned_words(title)
    description = sanitize_banned_words(description)
    if zi_items:
        for item in zi_items:
            if item.get("detail"):
                item["detail"] = sanitize_banned_words(item["detail"])

    return {
        "title": title,
        "description": description,
        "_raw_description": raw_description,
        "is_punch_list": is_punch_list,
        "zi_items": zi_items,
        "zi_item_count": zi_item_count,
        "zi_human_summary": zi_human_summary,
        "zi_category": zi_category,
    }


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

    processed = _process_raw_description(
        wo.description or "", wo_number=wo.work_order_number,
    )

    return {
        "id": wo.pk,
        "property_id": wo.property_id,
        "unit_id": wo.unit_id,
        "work_order_number": wo.work_order_number,
        **processed,
        "vendor_name": wo.vendor_name,
        "is_owner_approved": wo.is_owner_approved,
        "is_internal": wo.is_internal,
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
    show_unit is True when the property itself has more than one active unit,
    regardless of how many units appear in the current batch of work orders.
    """
    from collections import OrderedDict

    from django.db.models import Count

    from core.models import Unit

    groups = OrderedDict()
    for wo in wo_dicts:
        groups.setdefault(wo["property_address"], []).append(wo)

    # Collect distinct property IDs and batch-query active unit counts.
    property_ids = {
        wo["property_id"]
        for wos in groups.values()
        for wo in wos
        if wo.get("property_id")
    }
    unit_counts = {}
    if property_ids:
        unit_counts = {
            row["property_id"]: row["unit_count"]
            for row in Unit.objects.filter(
                property_id__in=property_ids, is_active=True,
            ).values("property_id").annotate(unit_count=Count("id"))
        }

    return [
        {
            "property_address": addr,
            "work_orders": wos,
            "show_unit": any(
                unit_counts.get(wo.get("property_id"), 0) > 1
                for wo in wos
            ),
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
    lawn_q = Q()
    for pat in LAWN_CARE_EXCLUSION_PATTERNS:
        lawn_q |= Q(description__icontains=pat)
    base_qs = WorkOrder.objects.filter(portfolio=portfolio, is_active=True).exclude(
        lawn_q
    ).select_related("unit", "property")

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


def get_portfolio_work_order_summary(portfolio, period_start, period_end):
    """
    Light maintenance overview for a single portfolio's monthly section,
    sourced from RentVine WorkOrder (replaces the Meld-based
    get_portfolio_maintenance_summary for the monthly owner note).

    Returns dict with counts of open, closed, and canceled items for the
    period.  Same return shape as get_portfolio_maintenance_summary().

    Bucket rules (lifecycle fields, not status-ID enums):
      - open: not closed AND not cancelled (includes scheduled — no
        scheduled_start_date filter). No period window.
      - closed: (closed_by_user_id set OR date_closed set) AND NOT
        cancelled. Period-windowed by date_closed; falls back to
        source_modified_at when date_closed is null.
      - canceled: cancelled_by_user_id set. Period-windowed by
        source_modified_at.
    Cancellation wins when a WO is both closed and cancelled, matching
    WorkOrder.derived_status precedence.
    """
    base_qs = WorkOrder.objects.filter(portfolio=portfolio, is_active=True)

    # Open: not cancelled AND not closed — regardless of age
    open_count = (
        base_qs.filter(
            cancelled_by_user_id__isnull=True,
            closed_by_user_id__isnull=True,
            date_closed__isnull=True,
        )
        .count()
    )

    # Canceled: cancelled_by_user_id set, windowed by source_modified_at
    canceled_count = (
        base_qs.filter(cancelled_by_user_id__isnull=False)
        .filter(
            source_modified_at__date__gte=period_start,
            source_modified_at__date__lte=period_end,
        )
        .count()
    )

    # Closed: (closed_by_user_id set OR date_closed set) AND NOT cancelled.
    # Window by date_closed; fall back to source_modified_at when
    # date_closed is null.
    closed_count = (
        base_qs.filter(
            Q(closed_by_user_id__isnull=False) | Q(date_closed__isnull=False)
        )
        .filter(cancelled_by_user_id__isnull=True)
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
        .count()
    )

    total = open_count + closed_count + canceled_count
    has_data = bool(total)

    summary_line = ""
    if has_data:
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

    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "canceled_count": canceled_count,
        "summary_line": summary_line,
        "notable_items": [],
        "_has_data": has_data,
    }


# ---------------------------------------------------------------------------
# Mason maintenance facts for the monthly owner note prompt
# ---------------------------------------------------------------------------


def _match_join_failures_to_portfolio(
    mason_sweep, portfolio, period_start, period_end,
):
    """Match join-failed Mason WOs to a portfolio by propertyAddress.

    Mason WOs that couldn't be joined to a local WorkOrder (sync lag, ID
    mismatch) still carry ``propertyAddress``.  We match that against local
    Property.address_line_1 for the target portfolio's properties.

    Returns a list of dicts for join-failed WOs whose ``propertyAddress``
    matches a property in this portfolio AND whose completed/cancelled date
    falls in the period.  These are surfaced in ``validation_flags`` so a
    reviewer knows what was dropped.
    """
    from maintenance.mason_selectors import _parse_date

    from core.models import Property

    join_failures = mason_sweep.get("join_failures", [])
    if not join_failures:
        return []

    # Build {normalised_address: property} for this portfolio's properties
    portfolio_addrs = {}
    for prop in Property.objects.filter(
        portfolio=portfolio, is_active=True,
    ):
        addr = (prop.address_line_1 or "").strip().lower()
        if addr:
            portfolio_addrs[addr] = prop

    matched = []
    for failure in join_failures:
        mason_addr = (failure.get("propertyAddress") or "").strip().lower()
        if not mason_addr or mason_addr not in portfolio_addrs:
            continue
        cd = _parse_date(failure.get("_completed_date"))
        cand = _parse_date(failure.get("_cancelled_date"))
        in_period = (
            (cd and period_start <= cd <= period_end)
            or (cand and period_start <= cand <= period_end)
        )
        if in_period:
            matched.append({
                "description": (failure.get("description") or "")[:120],
                "propertyAddress": failure.get("propertyAddress") or "",
                "mason_status": failure.get("_mason_status") or "",
                "pms_work_order_number": failure.get("pms_work_order_number") or "",
            })

    return matched


def _extract_mason_cancel_reason(local_wo):
    """Extract the Mason cancellation reason from the local WorkOrder description.

    Mason appends a resolution note to the RentVine description after a
    ``-------`` separator, e.g.::

        Concrete in walkway outside unit door is broken.
        -------
        mason: Work not necessary - This is not going to be addressed

    Returns the text after ``mason:`` (stripped), or "" when no annotation.
    """
    desc = local_wo.description or ""
    sep_idx = desc.find("-------")
    if sep_idx == -1:
        return ""
    after_sep = desc[sep_idx + 7:].strip()
    # Skip the "mason:" or "Mason:" prefix
    for prefix in ("mason:", "Mason:"):
        if after_sep.startswith(prefix):
            after_sep = after_sep[len(prefix):].strip()
            break
    # Strip trailing [Source] block and anything after it
    source_idx = after_sep.find("[Source]")
    if source_idx != -1:
        after_sep = after_sep[:source_idx].strip()
    # Also strip at double-newline (other trailing metadata)
    nl_idx = after_sep.find("\n\n")
    if nl_idx != -1:
        after_sep = after_sep[:nl_idx].strip()
    return after_sep


_CANCEL_INCLUDE_TERMS = ("not addressed", "not going to be addressed", "deferred")
_CANCEL_EXCLUDE_TERMS = (
    "different work order",
    "cancel and recreate",
    "already done",
    "no longer needed",
    "duplicate",
)


def _is_deferred_cancel(reason):
    """Return True when a cancelled WO should appear in the watch list.

    Include-terms always win (even if an exclude-term also matches).
    Unrecognized reasons default to include.
    """
    lower = reason.lower()
    if any(t in lower for t in _CANCEL_INCLUDE_TERMS):
        return True
    if any(t in lower for t in _CANCEL_EXCLUDE_TERMS):
        return False
    # Unrecognized: include (safe default — show rather than hide)
    return True


# ── Safety / habitability constants for zero-cost filter ────────────
_SAFETY_PRIORITY = 3  # RentVine priority_id: 1=Low, 2=Normal, 3=High/Emergency
_HABITABILITY_TRADE_IDS = {
    1,   # Security System
    9,   # Electrical
    12,  # Fire/Water Damage
    17,  # HVAC
    26,  # Plumbing
    28,  # Roofing
}


def _is_safety_or_habitability(local_wo):
    """Return True if the work order is emergency-priority or habitability-trade."""
    if not local_wo:
        return False
    if local_wo.priority_id == _SAFETY_PRIORITY:
        return True
    if local_wo.vendor_trade_id in _HABITABILITY_TRADE_IDS:
        return True
    return False


def _completed_in_period(wo, completed_date, local_wo, period_start, period_end):
    """Return the effective completion date if the WO completed in the period.

    Uses exactly ONE date to avoid a job appearing in two months' notes:
    - If the local WorkOrder has ``date_closed``, use it exclusively.
    - Otherwise fall back to the Mason visit-derived ``completed_date``.

    Returns the chosen date only if it falls in the period, else None.
    """
    local_closed = local_wo.date_closed if local_wo else None
    effective = local_closed if local_closed else completed_date
    if effective and period_start <= effective <= period_end:
        return effective
    return None


def get_portfolio_mason_maintenance_facts(
    portfolio, period_start, period_end, mason_sweep,
):
    """
    Per-work-order maintenance facts for the monthly portfolio note AI prompt.

    Uses pre-fetched Mason sweep data (called ONCE per run) joined to local
    WorkOrder records for portfolio filtering and RentVine cost lookup.

    Returns::

        {
            "completed": [{address, unit_number, description, work_performed_note,
                           completed_date, cost, rentvine_id}, ...],
            "deferred": [{address, unit_number, description, cancelled_date,
                          cancel_reason}, ...],
            "open": [{address, unit_number, description, status, open_since,
                      scheduled_date}, ...],
            "cost_facts": {rentvine_id: Decimal, ...},
            "join_failures": [{description, propertyAddress, mason_status}, ...],
            "_has_data": bool,
        }
    """
    from maintenance.mason_selectors import _parse_date

    all_mason_wos = mason_sweep["work_orders"]

    # Filter to this portfolio via local WorkOrder
    portfolio_wos = [
        wo for wo in all_mason_wos
        if wo.get("_local_wo") and wo["_local_wo"].portfolio_id == portfolio.pk
    ]

    # ── Join-failure WOs matched to this portfolio by propertyAddress ──
    join_failure_wos = _match_join_failures_to_portfolio(
        mason_sweep, portfolio, period_start, period_end,
    )

    # Exclude lawn + recurring/cadence (same rule as weekly email)
    after_lawn = []
    for wo in portfolio_wos:
        if wo["_is_lawn_ish"] and (wo["_is_recurring"] or wo["_has_cadence"]):
            continue
        after_lawn.append(wo)

    # Exclude "excluded" bucket (duplicates etc.)
    active_wos = [wo for wo in after_lawn if wo["_mapped_bucket"] != "excluded"]

    # Batch cost lookup via RentVine bills
    rentvine_ids = [
        wo["_rentvine_id"] for wo in active_wos if wo.get("_rentvine_id")
    ]
    costs = get_work_order_costs(rentvine_ids)

    completed = []
    deferred = []
    open_wos = []

    for wo in active_wos:
        local_wo = wo["_local_wo"]
        address = (
            local_wo.property.address_line_1
            if local_wo.property
            else ""
        )
        unit_number = (
            local_wo.unit.unit_number
            if local_wo.unit and local_wo.unit.unit_number
            else ""
        )
        desc = (wo.get("description") or "").strip()
        bucket = wo["_mapped_bucket"]
        rv_id = wo.get("_rentvine_id")

        completed_date = _parse_date(wo.get("_completed_date"))
        cancelled_date = _parse_date(wo.get("_cancelled_date"))

        # Recurring: only completions in period
        if wo["_is_recurring"]:
            if bucket == "completed":
                effective = _completed_in_period(
                    wo, completed_date, local_wo, period_start, period_end,
                )
                if effective:
                    wo_cost = costs.get(rv_id) if rv_id else None
                    if not wo_cost and not _is_safety_or_habitability(local_wo):
                        continue
                    completed.append({
                        "address": address,
                        "unit_number": unit_number,
                        "description": desc,
                        "work_performed_note": wo.get("_work_performed_note") or "",
                        "completed_date": str(effective),
                        "cost": wo_cost,
                        "rentvine_id": rv_id,
                    })
            continue

        if bucket in ("open", "scheduled"):
            created_at = wo.get("createdAt", "")
            open_since = created_at[:10] if created_at and len(created_at) >= 10 else ""
            sched = _parse_date(wo.get("_scheduled_date"))
            open_wos.append({
                "address": address,
                "unit_number": unit_number,
                "description": desc,
                "status": "scheduled" if bucket == "scheduled" else "open",
                "open_since": open_since,
                "scheduled_date": str(sched) if sched else None,
            })
        elif bucket == "completed":
            effective = _completed_in_period(
                wo, completed_date, local_wo, period_start, period_end,
            )
            if effective:
                wo_cost = costs.get(rv_id) if rv_id else None
                if not wo_cost and not _is_safety_or_habitability(local_wo):
                    continue
                completed.append({
                    "address": address,
                    "unit_number": unit_number,
                    "description": desc,
                    "work_performed_note": wo.get("_work_performed_note") or "",
                    "completed_date": str(effective),
                    "cost": wo_cost,
                    "rentvine_id": rv_id,
                })
        elif bucket == "cancelled":
            # One-date rule: local date_closed wins, else Mason cancelled_date
            local_closed = local_wo.date_closed if local_wo else None
            effective_cancel = local_closed if local_closed else cancelled_date
            # 90-day lookback so deferred items persist across months
            deferred_start = period_end - timedelta(days=90)
            if effective_cancel and deferred_start <= effective_cancel <= period_end:
                reason = _extract_mason_cancel_reason(local_wo)
                if _is_deferred_cancel(reason):
                    deferred.append({
                        "address": address,
                        "unit_number": unit_number,
                        "description": desc,
                        "cancelled_date": str(effective_cancel),
                        "cancel_reason": reason,
                    })

    # Build cost_facts for dollar validator
    cost_facts = {}
    for item in completed:
        if item.get("rentvine_id") and item.get("cost") is not None:
            cost_facts[item["rentvine_id"]] = item["cost"]

    return {
        "completed": completed,
        "deferred": deferred,
        "open": open_wos,
        "cost_facts": cost_facts,
        "join_failures": join_failure_wos,
        "_has_data": bool(completed or deferred or open_wos),
    }


def get_unattributed_maintenance_costs(portfolio, period_start, period_end):
    """
    Detect whether unattributed maintenance bills exist for this portfolio
    in the period.

    A bill is "unattributed maintenance" when:
    - work_order_id IS NULL and meld_id IS NULL (not linked to any WO)
    - is_voided = False
    - bill_date falls in the period
    - At least one non-voided BillCharge has a chargeAccountID in
      MAINTENANCE_EXPENSE_ACCOUNT_IDS
    - The charge's portfolioID matches the portfolio

    Returns bool. No amounts exposed.
    """
    from accounting.models import Bill
    from django.conf import settings

    if not settings.MAINTENANCE_EXPENSE_ACCOUNT_IDS:
        return False

    if not hasattr(portfolio, "rentvine_id") or not portfolio.rentvine_id:
        return False

    portfolio_rv_id = str(portfolio.rentvine_id)
    maint_acct_ids = {str(a) for a in settings.MAINTENANCE_EXPENSE_ACCOUNT_IDS}

    unlinked_bills = Bill.objects.filter(
        work_order_id__isnull=True,
        meld_id__isnull=True,
        is_voided=False,
        bill_date__gte=period_start,
        bill_date__lte=period_end,
    ).prefetch_related("charges")

    for bill in unlinked_bills:
        for charge in bill.charges.all():
            if charge.is_voided:
                continue
            tx = charge.raw_data.get("transaction", charge.raw_data)
            charge_acct = str(tx.get("chargeAccountID") or "")
            if charge_acct not in maint_acct_ids:
                continue
            # Verify portfolio via portfolioID in raw_data
            rv_portfolio = str(tx.get("portfolioID") or "")
            if rv_portfolio == portfolio_rv_id:
                return True

    return False

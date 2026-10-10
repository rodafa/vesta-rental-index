"""
Mason-sourced portfolio work-order selector.

Produces the IDENTICAL output shape as get_portfolio_work_order_data() so that
everything downstream (AI prompt, snapshot, template, edit handler) works
unchanged.

This module does NOT fetch from the Mason API — it receives pre-fetched data
from integrations.mason.services.fetch_mason_work_order_sweep(), which is
called ONCE before the portfolio loop and passed in.
"""

import logging
from datetime import date

from .selectors import (
    LAWN_CARE_EXCLUSION_PATTERNS,
    _group_by_address,
    _work_order_to_dict,
    get_work_order_costs,
)

logger = logging.getLogger(__name__)


def _mason_description_to_text(raw):
    """Convert a Mason description to plain text.

    Mason descriptions may be plain text or lightly formatted.  We reuse
    the local WorkOrder's description (already HTML from Rentvine) through
    _work_order_to_dict for title/description extraction, so this helper
    is only used when we need to override with Mason-specific content.
    """
    if not raw:
        return ""
    return raw.strip()


def _parse_date(val):
    """Parse an ISO date string to a date object, or return None."""
    if not val:
        return None
    try:
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def get_portfolio_work_order_data_mason(
    portfolio, period_start, period_end, mason_sweep,
):
    """
    Gather work orders for a single portfolio from pre-fetched Mason data,
    grouped into four email buckets.

    Produces the SAME output shape as get_portfolio_work_order_data():

        {
            "open": [{"property_address", "work_orders": [dict], "show_unit"}],
            "scheduled": [...],
            "completed": [...],
            "cancelled": [...],
            "_has_data": bool,
        }

    mason_sweep: the dict returned by fetch_mason_work_order_sweep().

    Fields from Rentvine (via local WorkOrder): cost, estimated_amount,
    is_owner_approved, unit_address, property_address, unit_label.

    Fields from Mason: description (for title extraction), vendor_name,
    scheduled_start_date (scheduled_date), date_closed (completed_date).

    Recurring work orders appear ONLY in Completed.
    """
    all_mason_wos = mason_sweep["work_orders"]
    rentvine_lookup = mason_sweep["rentvine_lookup"]

    # Filter to this portfolio
    portfolio_wos = []
    for wo in all_mason_wos:
        local_wo = wo.get("_local_wo")
        if local_wo and local_wo.portfolio_id == portfolio.pk:
            portfolio_wos.append(wo)

    # Apply lawn care exclusion (on Mason description)
    filtered_wos = []
    for wo in portfolio_wos:
        desc_lower = (wo.get("description") or "").lower()
        excluded = False
        for pat in LAWN_CARE_EXCLUSION_PATTERNS:
            if pat in desc_lower:
                excluded = True
                break
        if not excluded:
            filtered_wos.append(wo)

    # Exclude WOs with _mapped_bucket == "excluded"
    active_wos = [
        wo for wo in filtered_wos if wo["_mapped_bucket"] != "excluded"
    ]

    # Batch cost lookup — uses Rentvine bill data (unchanged)
    rentvine_ids = [
        wo["_rentvine_id"]
        for wo in active_wos
        if wo.get("_rentvine_id")
    ]
    costs = get_work_order_costs(rentvine_ids)

    # Build work-order dicts using _work_order_to_dict (local WO for
    # structural fields), then override Mason-sourced fields.
    open_dicts = []
    scheduled_dicts = []
    completed_dicts = []
    cancelled_dicts = []

    for wo in active_wos:
        local_wo = wo["_local_wo"]
        cost = costs.get(wo["_rentvine_id"]) if wo.get("_rentvine_id") else None

        # Build base dict from local WorkOrder (address, unit, property,
        # cost, estimated_amount, is_owner_approved, zInspector parsing)
        wo_dict = _work_order_to_dict(local_wo, cost=cost)

        # Override with Mason-sourced fields
        mason_vendor = wo.get("_vendor_name", "")
        if mason_vendor:
            wo_dict["vendor_name"] = mason_vendor

        # work_order_number from pms.workOrderNumber (friendlyId is null)
        pms = wo.get("pms") or {}
        pms_wo_number = pms.get("workOrderNumber")
        if pms_wo_number:
            wo_dict["work_order_number"] = str(pms_wo_number)

        # Mason-sourced dates
        scheduled_date = _parse_date(wo.get("_scheduled_date"))
        completed_date = _parse_date(wo.get("_completed_date"))
        cancelled_date = _parse_date(wo.get("_cancelled_date"))

        wo_dict["scheduled_start_date"] = scheduled_date
        wo_dict["date_closed"] = completed_date

        # Source timestamps from Mason
        wo_dict["source_created_at"] = wo.get("createdAt")
        wo_dict["source_modified_at"] = wo.get("updatedAt")

        bucket = wo["_mapped_bucket"]

        # Recurring work orders: ONLY in Completed, never in Open/Scheduled.
        # Still windowed — a recurring WO that completed outside the period
        # should not appear.
        if wo["_is_recurring"]:
            if bucket == "completed":
                if completed_date and period_start <= completed_date <= period_end:
                    completed_dicts.append(wo_dict)
            # else: skip — recurring WOs do not appear in Open or Scheduled
            continue

        # Bucket assignment
        if bucket == "open":
            open_dicts.append(wo_dict)
        elif bucket == "scheduled":
            scheduled_dicts.append(wo_dict)
        elif bucket == "completed":
            # Window to period using the derived completed_date only.
            # Do NOT fall back to updatedAt — it changes for any reason
            # and would let WOs through that completed outside the window.
            if completed_date and period_start <= completed_date <= period_end:
                completed_dicts.append(wo_dict)
            elif not completed_date:
                logger.warning(
                    "mason_completed_wo_no_date",
                    extra={
                        "work_order_number": wo_dict.get("work_order_number"),
                        "mason_id": wo.get("id"),
                    },
                )
        elif bucket == "cancelled":
            # Window to period using the derived cancelled_date only.
            if cancelled_date and period_start <= cancelled_date <= period_end:
                cancelled_dicts.append(wo_dict)
            elif not cancelled_date:
                logger.warning(
                    "mason_cancelled_wo_no_date",
                    extra={
                        "work_order_number": wo_dict.get("work_order_number"),
                        "mason_id": wo.get("id"),
                    },
                )

    # Sort to match existing ordering
    open_dicts.sort(
        key=lambda d: d.get("source_created_at") or "", reverse=True,
    )
    scheduled_dicts.sort(
        key=lambda d: d.get("source_created_at") or "", reverse=True,
    )
    completed_dicts.sort(
        key=lambda d: (d.get("date_closed") or "", d.get("source_modified_at") or ""),
        reverse=True,
    )
    cancelled_dicts.sort(
        key=lambda d: d.get("source_modified_at") or "", reverse=True,
    )

    return {
        "open": _group_by_address(open_dicts),
        "scheduled": _group_by_address(scheduled_dicts),
        "completed": _group_by_address(completed_dicts),
        "cancelled": _group_by_address(cancelled_dicts),
        "_has_data": bool(
            open_dicts or scheduled_dicts or completed_dicts or cancelled_dicts
        ),
    }

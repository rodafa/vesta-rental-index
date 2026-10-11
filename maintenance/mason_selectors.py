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

# LAWN_CARE_EXCLUSION_PATTERNS (defined in selectors.py) is used only by the
# Rentvine work-order path.  On the Mason path the recurring flag replaces it.
# Both the patterns and their definition in selectors.py get deleted along
# with the Rentvine work-order path at cutover.
from .selectors import (
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


def _build_turn_groups(open_dicts, scheduled_dicts, completed_dicts):
    """Build turn groups from work orders with work_order_type == 'turn'.

    Groups by (property_id, unit_id) — unit_id may be None for
    single-unit properties (falls back to property_id only).  Only
    creates a group when 2+ turn WOs share the same key.  Lone turn
    WOs render as normal cards.

    Cancelled WOs are excluded — they render as normal cards in the
    Cancelled bucket so nothing is silently lost.

    Returns a list of turn group dicts for the snapshot.
    """
    from collections import OrderedDict

    turn_wos = OrderedDict()  # unit_key -> [(bucket, wo_dict), ...]
    for bucket_name, dicts in (
        ("open", open_dicts),
        ("scheduled", scheduled_dicts),
        ("completed", completed_dicts),
    ):
        for wo in dicts:
            if wo.get("work_order_type") != "turn":
                continue
            uid = wo.get("unit_id") or ""
            pid = wo.get("property_id") or ""
            key = f"{pid}:{uid}"
            turn_wos.setdefault(key, []).append((bucket_name, wo))

    groups = []
    for key, entries in turn_wos.items():
        if len(entries) < 2:
            continue

        first_wo = entries[0][1]
        wo_numbers_by_bucket = {}
        completed_dates = []
        trades = []

        for bucket_name, wo in entries:
            wo_numbers_by_bucket.setdefault(bucket_name, []).append(
                wo["work_order_number"]
            )
            if bucket_name == "completed" and wo.get("date_closed"):
                completed_dates.append(wo["date_closed"])

            zi_count = wo.get("zi_item_count")
            count_suffix = (
                f" ({zi_count} items)" if zi_count and zi_count > 1 else ""
            )
            trades.append({
                "title": wo.get("title", ""),
                "count_suffix": count_suffix,
                "vendor_name": wo.get("vendor_name", ""),
                "cost": wo.get("cost"),
                "bucket": bucket_name,
            })

        groups.append({
            "unit_key": key,
            "address": first_wo.get("property_address", ""),
            "unit_label": first_wo.get("unit_label", ""),
            "work_order_numbers": wo_numbers_by_bucket,
            "completed_dates": (
                sorted(completed_dates) if completed_dates else []
            ),
            "trades": trades,
        })

    return groups


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

    # Exclude routine lawn/grounds work entirely — never in any bucket.
    # Two independent triggers, both gated on lawn-ish:
    #   1. recurring flag (source=mason:recurring or workOrderType=recurring)
    #   2. cadence phrase in description (bi-weekly, weekly, monthly, etc.)
    # Non-recurring, non-cadence lawn work passes through normally.
    # Recurring non-lawn work (e.g. seasonal inspections) keeps its
    # existing behavior: completion notice in Completed only (lines 168-173).
    after_lawn_filter = []
    for wo in portfolio_wos:
        if wo["_is_lawn_ish"]:
            pms = wo.get("pms") or {}
            wo_num = pms.get("workOrderNumber")
            desc_head = (wo.get("description") or "")[:80]
            if wo["_is_recurring"]:
                logger.info(
                    "mason_recurring_lawn_excluded",
                    extra={
                        "work_order_number": wo_num,
                        "description": desc_head,
                    },
                )
                continue
            if wo["_has_cadence"]:
                logger.info(
                    "mason_cadence_lawn_excluded",
                    extra={
                        "work_order_number": wo_num,
                        "description": desc_head,
                    },
                )
                continue
        after_lawn_filter.append(wo)

    # Exclude WOs with _mapped_bucket == "excluded"
    active_wos = [
        wo for wo in after_lawn_filter if wo["_mapped_bucket"] != "excluded"
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

        # Mason-sourced metadata
        wo_dict["source_created_at"] = wo.get("createdAt")
        wo_dict["source_modified_at"] = wo.get("updatedAt")
        wo_dict["work_order_type"] = wo.get("_work_order_type", "")

        # Tech's own description of work performed (completed cards only
        # in the template, but carried on all WOs for snapshot consistency).
        work_note = wo.get("_work_performed_note")
        if work_note:
            wo_dict["work_performed_note"] = work_note

        bucket = wo["_mapped_bucket"]

        # Approved estimate amount from Mason — Open/Scheduled only.
        # Completed/Cancelled leave estimated_amount from Rentvine
        # unchanged ("Final costs on completed items stay on Rentvine").
        if bucket in ("open", "scheduled"):
            mason_estimate = wo.get("_approved_estimate_amount")
            if mason_estimate is not None:
                wo_dict["estimated_amount"] = mason_estimate

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

    turns = _build_turn_groups(open_dicts, scheduled_dicts, completed_dicts)

    result = {
        "open": _group_by_address(open_dicts),
        "scheduled": _group_by_address(scheduled_dicts),
        "completed": _group_by_address(completed_dicts),
        "cancelled": _group_by_address(cancelled_dicts),
        "_has_data": bool(
            open_dicts or scheduled_dicts or completed_dicts or cancelled_dicts
        ),
    }
    if turns:
        result["turns"] = turns
    return result

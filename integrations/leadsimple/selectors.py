"""
Data selectors for LeadSimple pipeline data.

Thin orchestrator: calls client, classifies, filters, matches, groups.
"""

import logging

from core.models import Unit

from .client import DEGRADED, fetch_processes
from .services import (
    TASK_THRESHOLD,
    apply_milestone_gates,
    build_owner_data,
    build_unit_lookup,
    classify_processes,
    count_tasks_completed_in_period,
    filter_excluded_stages,
    filter_for_monthly,
    group_by_owner,
    match_to_units,
    _get_process_type_id,
)
from .stage_map import get_stage_framing
from .step_map import get_surfaced_fragments

logger = logging.getLogger(__name__)


def get_portfolio_pipeline_data(
    portfolio, period_start, period_end, *,
    prefetched_processes=None, prefetched_tasks_index=None,
):
    """
    Gather LeadSimple pipeline data for a single portfolio's monthly section.

    Same return shape as get_owner_pipeline_data minus the owner-grain pivot.
    Queries units via property__portfolio=portfolio.

    When prefetched_processes/prefetched_tasks_index are provided, skips
    the API call and uses the cached data instead (fetch-once pattern).

    Used by monthly owner notes — does NOT replace get_owner_pipeline_data().
    """
    if prefetched_processes is not None:
        raw = prefetched_processes
    else:
        raw = fetch_processes()
        if raw is DEGRADED:
            return {
                "processes_by_category": {},
                "total_count": 0,
                "_has_data": False,
                "_degraded": True,
            }

    classified = classify_processes(raw)
    monthly = filter_for_monthly(
        classified, period_start, period_end,
        tasks_index=prefetched_tasks_index,
    )
    monthly = filter_excluded_stages(monthly)
    monthly = apply_milestone_gates(monthly)

    units_qs = Unit.objects.filter(
        property__portfolio=portfolio, is_active=True
    )
    if not units_qs.exists():
        return {
            "processes_by_category": {},
            "total_count": 0,
            "_has_data": False,
            "_degraded": False,
        }

    unit_lookup = build_unit_lookup(units_qs)
    matched = match_to_units(monthly, unit_lookup)

    # Filter to only processes matched to this portfolio's units
    portfolio_processes = []
    unmatched_ids = []
    for proc, unit in matched:
        if unit is None:
            unmatched_ids.append(proc.get("id", ""))
            continue
        if unit.property.portfolio_id == portfolio.pk:
            portfolio_processes.append(proc)

    if unmatched_ids:
        logger.info(
            "leadsimple_unmatched_summary",
            extra={
                "count": len(unmatched_ids),
                "process_ids": unmatched_ids[:20],
                "portfolio": portfolio.name,
            },
        )

    if not portfolio_processes:
        return {
            "processes_by_category": {},
            "total_count": 0,
            "_has_data": False,
            "_degraded": False,
        }

    # Build by-category grouping with structured facts
    # Stage framing gate: if no framing exists, the process is omitted entirely
    from datetime import datetime, time
    from .services import ET

    window_start = datetime.combine(period_start, time.min, tzinfo=ET)
    window_end = datetime.combine(period_end, time(23, 59, 59, 999999), tzinfo=ET)

    tasks_idx = prefetched_tasks_index or {}
    by_category = {}
    for proc in portfolio_processes:
        cat = proc.get("category", "unknown")
        pt_id = _get_process_type_id(proc)
        stage_name = (proc.get("stage") or {}).get("name", "")

        # Stage framing gate — no framing = omit from note
        status_line = get_stage_framing(pt_id, stage_name)
        if status_line is None:
            continue

        # Activity band — coarse label, never raw counts
        proc_id = proc.get("id", "")
        proc_tasks = tasks_idx.get(proc_id, [])
        completed_count = count_tasks_completed_in_period(
            proc_tasks, window_start, window_end,
        )
        if completed_count >= TASK_THRESHOLD:
            activity_band = "actively worked"
        elif completed_count >= 1:
            activity_band = "lightly worked"
        else:
            activity_band = ""

        # Surfaced fragments from step map
        fragments = get_surfaced_fragments(
            proc, proc_tasks, window_start, window_end,
        )

        address = ((proc.get("properties") or [{}])[0]).get("address", "")
        unit_number = (
            ((proc.get("properties") or [{}])[0]).get("unit", {}) or {}
        ).get("unit_number", "")

        by_category.setdefault(cat, []).append({
            "process_id": proc_id,
            "name": proc.get("name", ""),
            "category": cat,
            "stage_name": stage_name,
            "stage_status": (proc.get("stage") or {}).get("status", ""),
            "created_at": proc.get("created_at"),
            "closed_at": proc.get("closed_at"),
            "address": address,
            "unit_number": unit_number,
            # Structured facts for prompt
            "status_line": status_line,
            "activity_band": activity_band,
            "surfaced_fragments": fragments,
        })

    return {
        "processes_by_category": by_category,
        "total_count": len(portfolio_processes),
        "_has_data": bool(by_category),
        "_degraded": False,
    }


def get_owner_pipeline_data(owner, period_start, period_end):
    """
    Gather LeadSimple pipeline data for a single owner's monthly report.

    Returns dict with keys:
        owner_first_name, processes_by_category, total_count,
        _has_data, _degraded
    """
    raw = fetch_processes()
    if raw is DEGRADED:
        return {"_degraded": True, "_has_data": False}

    classified = classify_processes(raw)
    monthly = filter_for_monthly(classified, period_start, period_end)

    # Build unit lookup scoped to this owner's portfolios
    portfolio_ids = owner.portfolios.values_list("id", flat=True)
    if not portfolio_ids:
        return {
            "owner_first_name": owner.first_name or (owner.name or "Owner").split()[0],
            "processes_by_category": {},
            "total_count": 0,
            "_has_data": False,
            "_degraded": False,
        }

    units_qs = Unit.objects.filter(
        property__portfolio_id__in=portfolio_ids, is_active=True
    )
    unit_lookup = build_unit_lookup(units_qs)

    matched = match_to_units(monthly, unit_lookup)

    # Filter to only processes matched to this owner's units
    owner_processes = []
    for proc, unit in matched:
        if unit is None:
            continue
        if unit.property.portfolio_id in set(portfolio_ids):
            owner_processes.append(proc)

    if not owner_processes:
        return {
            "owner_first_name": owner.first_name or (owner.name or "Owner").split()[0],
            "processes_by_category": {},
            "total_count": 0,
            "_has_data": False,
            "_degraded": False,
        }

    data = build_owner_data(owner, owner_processes)
    data["_has_data"] = True
    data["_degraded"] = False
    return data

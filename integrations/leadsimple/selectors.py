"""
Data selectors for LeadSimple pipeline data.

Thin orchestrator: calls client, classifies, filters, matches, groups.
"""

import logging

from core.models import Unit

from .client import DEGRADED, fetch_processes
from .services import (
    build_owner_data,
    build_unit_lookup,
    classify_processes,
    filter_for_monthly,
    group_by_owner,
    match_to_units,
)

logger = logging.getLogger(__name__)


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

"""
Snapshot builder for weekly leasing performance.

Combines locally computed metrics (from LeasingEvent / Prospect rows) with
RentEngine's reporting endpoint to produce a single UnitLeasingSnapshot row
per unit per period.
"""

import logging

from integrations.rentengine.client import RentEngineClient

from .models import UnitLeasingSnapshot
from .selectors import compute_leasing_metrics_bulk

logger = logging.getLogger(__name__)


def build_unit_snapshot(unit, period_start, period_end, client=None):
    """
    Build or update a UnitLeasingSnapshot for a single unit and period.

    Args:
        unit: a core.Unit instance.
        period_start: date object, inclusive start.
        period_end: date object, inclusive end.
        client: optional RentEngineClient instance (created if not provided).

    Returns:
        (snapshot, created) — the UnitLeasingSnapshot and whether it was
        newly created.
    """
    # --- Local metrics from our own event / prospect rows ---
    metrics = compute_leasing_metrics_bulk([unit], period_start, period_end)
    unit_metrics = metrics[unit.id]

    # --- RentEngine reporting endpoint ---
    reporting_data = {}
    reporting_fetch_ok = False
    reporting_error = ""
    touchpoints_calls = None
    touchpoints_texts = None
    upcoming_showings = None
    days_on_market = None
    property_health = ""

    if unit.rentengine_id is not None:
        if client is None:
            client = RentEngineClient()
        try:
            reporting_data = client.get_unit_leasing_performance(
                unit.rentengine_id, period_start, period_end,
            )
            if not isinstance(reporting_data, dict):
                reporting_error = (
                    f"Expected dict, got {type(reporting_data).__name__}"
                )
            elif "unit_id" not in reporting_data:
                reporting_error = (
                    f"Response missing unit_id; keys: {sorted(reporting_data.keys())}"
                )
            else:
                reporting_fetch_ok = True
                touchpoints_calls = reporting_data.get("total_calls")
                touchpoints_texts = reporting_data.get("outbound_texts")
                upcoming_showings = reporting_data.get("upcoming_showings")
                days_on_market = reporting_data.get("days_on_market")
                property_health = str(
                    reporting_data.get("property_health") or ""
                )
        except Exception as exc:
            reporting_error = f"{type(exc).__name__}: {exc}"
            reporting_data = {}
    else:
        reporting_error = "Unit has no rentengine_id"

    logger.info(
        "leasing_snapshot_built",
        extra={
            "unit_id": unit.id,
            "rentengine_id": unit.rentengine_id,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "reporting_ok": reporting_fetch_ok,
        },
    )

    snapshot, created = UnitLeasingSnapshot.objects.update_or_create(
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        defaults={
            "new_leads": unit_metrics["new_leads"],
            "showings_scheduled": unit_metrics["showings_scheduled"],
            "showings_completed": unit_metrics["showings_completed"],
            "showings_canceled": unit_metrics["showings_canceled"],
            "showings_missed": unit_metrics["showings_missed"],
            "applications_received": unit_metrics["applications_received"],
            "touchpoints_calls": touchpoints_calls,
            "touchpoints_texts": touchpoints_texts,
            "upcoming_showings": upcoming_showings,
            "days_on_market": days_on_market,
            "property_health": property_health,
            "reporting_fetch_ok": reporting_fetch_ok,
            "reporting_error": reporting_error,
            "reporting_raw": reporting_data,
        },
    )

    return snapshot, created

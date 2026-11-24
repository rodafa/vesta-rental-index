"""
Snapshot builder for weekly leasing performance.

Combines locally computed metrics (from LeasingEvent / Prospect rows) with
RentEngine's reporting endpoint to produce a single UnitLeasingSnapshot row
per unit per period.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from integrations.rentengine.client import RentEngineClient

from .market_comps import reduce_comps
from .models import UnitLeasingSnapshot, UnitPriceChange
from .note_services import ET
from .selectors import compute_leasing_metrics_bulk

logger = logging.getLogger(__name__)


def build_unit_snapshot(
    unit, period_start, period_end, client=None,
    rv_advertised_dates=None, re_marked_available_dates=None,
    applications=None, applications_received=0,
    segment_benchmark=None, since_listed=None,
    *, comps_budget=None,
):
    """
    Build or update a UnitLeasingSnapshot for a single unit and period.

    Args:
        unit: a core.Unit instance.
        period_start: date object, inclusive start.
        period_end: date object, inclusive end.
        client: optional RentEngineClient instance (created if not provided).
        rv_advertised_dates: optional dict mapping rentvine unit ID -> date,
            from build_unit_advertised_dates(). Looked up once by the caller,
            not once per unit.
        re_marked_available_dates: optional dict mapping rentengine unit ID
            -> date, from build_unit_marked_available_dates(). Looked up
            once by the caller, not once per unit.
        applications: optional list of application dicts for this unit,
            from build_unit_applications(). Defaults to [] when None.
        applications_received: int, count of paid application groups whose
            submitted_at falls in [period_start, period_end]. Comes from
            build_unit_applications() period_counts, not len(applications).
        segment_benchmark: optional dict from compute_segment_benchmarks()
            for this unit's bedroom segment. Defaults to {} when None.
        since_listed: optional dict from compute_since_listed_totals()
            for this unit. Defaults to {} when None.
        comps_budget: optional mutable dict {"remaining": int} owned by
            the caller. Decremented before each metered comps call.
            When None, allows at most 1 fetch (single-unit mode).

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

    # --- Price changes from UnitPriceChange rows (local DB, not API) ---
    price_change_rows = list(
        UnitPriceChange.objects.filter(
            unit=unit,
            detected_date__range=(period_start, period_end),
        ).order_by("detected_at")
    )
    price_changes_json = [
        {
            "old_rent": str(pc.old_rent),
            "new_rent": str(pc.new_rent),
            "detected_date": pc.detected_date.isoformat(),
        }
        for pc in price_change_rows
    ]

    # --- Market comps (metered — guarded by budget and idempotency) ---
    market_snapshot_data = _fetch_market_comps(
        unit, period_start, period_end, client, comps_budget,
    )

    logger.info(
        "leasing_snapshot_built",
        extra={
            "unit_id": unit.id,
            "rentengine_id": unit.rentengine_id,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "reporting_ok": reporting_fetch_ok,
            "has_market_comps": bool(market_snapshot_data),
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
            "applications_received": applications_received,
            "touchpoints_calls": touchpoints_calls,
            "touchpoints_texts": touchpoints_texts,
            "upcoming_showings": upcoming_showings,
            "days_on_market": days_on_market,
            "advertised_date": (
                (rv_advertised_dates or {}).get(unit.rentvine_id)
            ),
            "date_marked_available": (
                (re_marked_available_dates or {}).get(unit.rentengine_id)
            ),
            "property_health": property_health,
            "reporting_fetch_ok": reporting_fetch_ok,
            "reporting_error": reporting_error,
            "reporting_raw": reporting_data,
            "applications": applications if applications is not None else [],
            "price_changes": price_changes_json,
            "segment_benchmark": segment_benchmark if segment_benchmark is not None else {},
            "since_listed": since_listed if since_listed is not None else {},
            "market_snapshot": market_snapshot_data,
        },
    )

    return snapshot, created


def _fetch_market_comps(unit, period_start, period_end, client, comps_budget):
    """
    Conditionally fetch and reduce market comps for a unit.

    Returns a reduced dict or {} if skipped/failed.

    Guard logic:
    1. Unit must have a rentengine_id.
    2. Existing snapshot with non-empty market_snapshot → skip (idempotent).
    3. Budget must have remaining > 0 (or be None for single-unit mode).
    4. Budget is decremented BEFORE the API call (blast-radius limit on
       attempts, not counter of successes — a failed call still costs $0.50).
    """
    if unit.latitude is None or unit.longitude is None:
        return {}
    if unit.bedrooms is None:
        return {}

    # Idempotency: if a snapshot already exists with market_snapshot data,
    # don't re-fetch.
    existing = UnitLeasingSnapshot.objects.filter(
        unit=unit,
        period_start=period_start,
        period_end=period_end,
    ).values_list("market_snapshot", flat=True).first()

    if existing is not None and existing:
        logger.info(
            "market_comps_skip_existing",
            extra={"unit_id": unit.id},
        )
        return existing

    # Budget gate
    if comps_budget is not None:
        if comps_budget.get("remaining", 0) <= 0:
            return {}
        # Decrement BEFORE the call (Fix 4: budget limits attempts, not successes)
        comps_budget["remaining"] -= 1
    else:
        # None budget = single-unit mode, allow exactly 1 fetch (no guard)
        pass

    try:
        raw_comps = client.get_market_comps(
            unit.latitude, unit.longitude,
            min_beds=unit.bedrooms, max_beds=unit.bedrooms,
            radius_miles=2,
        )
    except Exception as exc:
        logger.warning(
            "market_comps_fetch_failed",
            extra={"unit_id": unit.id, "error": str(exc)},
        )
        return {}

    today = datetime.now(ET).date()

    return reduce_comps(
        raw_comps=raw_comps,
        our_address=unit.display_address or "",
        our_rent=unit.target_rental_rate,
        our_sqft=unit.square_feet,
        beds=unit.bedrooms,
        radius_miles=2,
        period_start=period_start,
        period_end=period_end,
        today=today,
    )

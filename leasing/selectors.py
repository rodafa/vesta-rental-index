"""
Leasing metrics selectors.

new_leads is counted from Prospect.source_created_at because the "New" event
type does not fire reliably at prospect creation — verified against real data
where units had prospects created with zero corresponding "New" events.
All other metrics are computed from raw LeasingEvent rows. Nothing comes from
RentEngine's reporting endpoint, so every number is auditable against the
underlying records.
"""

from django.db.models import Count

from .models import LeasingEvent, Prospect

# Event type strings exactly as RentEngine stores them.
# Observed counts across 21,749 rows of full history:
# NOT tracked as a metric — does not fire reliably at prospect creation.
# Leads are counted from Prospect.source_created_at instead.
EVENT_TYPE_NEW = "New"                                # 1404
EVENT_TYPE_SHOWING_SCHEDULED = "Showing Scheduled"    # 1256
EVENT_TYPE_SHOWING_COMPLETE = "Showing Complete"      # 574
EVENT_TYPE_SHOWING_CANCELED = "Showing Canceled"      # 1280
EVENT_TYPE_MISSED_SHOWING = "Missed Showing"          # 30
EVENT_TYPE_APPLICATION_RECEIVED = "Application Received"  # 179

_EVENT_TYPE_TO_KEY = {
    EVENT_TYPE_SHOWING_SCHEDULED: "showings_scheduled",
    EVENT_TYPE_SHOWING_COMPLETE: "showings_completed",
    EVENT_TYPE_SHOWING_CANCELED: "showings_canceled",
    EVENT_TYPE_MISSED_SHOWING: "showings_missed",
    EVENT_TYPE_APPLICATION_RECEIVED: "applications_received",
}

_TRACKED_EVENT_TYPES = tuple(_EVENT_TYPE_TO_KEY.keys())

_ZERO_METRICS = {"new_leads": 0, **{key: 0 for key in _EVENT_TYPE_TO_KEY.values()}}


def compute_leasing_metrics(unit, start_date, end_date):
    """
    Compute leasing metrics for a single unit over an inclusive date range.

    Args:
        unit: a core.Unit instance (must not be None).
        start_date: date object, inclusive.
        end_date: date object, inclusive.

    Returns:
        dict with keys: new_leads, showings_scheduled, showings_completed,
        showings_canceled, showings_missed, applications_received.
        All values are integers.

    Raises:
        ValueError: if unit is None or start_date > end_date.
    """
    if unit is None:
        raise ValueError("unit must not be None")
    if start_date > end_date:
        raise ValueError(
            f"start_date ({start_date}) must not be after end_date ({end_date})"
        )
    result = compute_leasing_metrics_bulk([unit], start_date, end_date)
    return result[unit.id]


def compute_leasing_metrics_bulk(units, start_date, end_date):
    """
    Compute leasing metrics for multiple units in two database queries.

    Args:
        units: iterable of core.Unit instances.
        start_date: date object, inclusive.
        end_date: date object, inclusive.

    Returns:
        dict mapping unit.id -> metrics dict. Units with no events in the
        period still appear with all zeros.

    Raises:
        ValueError: if start_date > end_date.
    """
    if start_date > end_date:
        raise ValueError(
            f"start_date ({start_date}) must not be after end_date ({end_date})"
        )

    # Materialize once so generators/iterators are safe for both passes.
    units = list(units)

    # Seed every requested unit with zeros so missing types return 0.
    result = {u.id: dict(_ZERO_METRICS) for u in units}

    rows = (
        LeasingEvent.objects.filter(
            unit__in=units,
            event_type__in=_TRACKED_EVENT_TYPES,
            event_date__range=(start_date, end_date),
        )
        .values("unit_id", "event_type")
        .annotate(count=Count("id"))
    )

    for row in rows:
        unit_id = row["unit_id"]
        key = _EVENT_TYPE_TO_KEY[row["event_type"]]
        result[unit_id][key] = row["count"]

    # new_leads: count prospects by source_created_at, not events.
    prospect_rows = (
        Prospect.objects.filter(
            unit__in=units,
            source_created_at__date__range=(start_date, end_date),
        )
        .values("unit_id")
        .annotate(count=Count("id"))
    )

    for row in prospect_rows:
        unit_id = row["unit_id"]
        if unit_id in result:
            result[unit_id]["new_leads"] = row["count"]

    return result

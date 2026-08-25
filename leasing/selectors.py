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
    EVENT_TYPE_SHOWING_CANCELED: "showings_canceled",
    EVENT_TYPE_MISSED_SHOWING: "showings_missed",
    EVENT_TYPE_APPLICATION_RECEIVED: "applications_received",
}

_TRACKED_EVENT_TYPES = tuple(_EVENT_TYPE_TO_KEY.keys())

_ZERO_METRICS = {
    "new_leads": 0,
    "showings_completed": 0,
    **{key: 0 for key in _EVENT_TYPE_TO_KEY.values()},
}


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
    Compute leasing metrics for multiple units in three database queries.

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

    # showings_completed: deduplicate on (unit, prospect, event_date).
    # Unit 112, prospect 3040, 2026-06-30 had two "Showing Complete" events
    # 29 seconds apart (different rentengine_ids). Raw count reports 5
    # completions when 4 occurred. Dedupe in Python rather than DB-level
    # DISTINCT because SQL DISTINCT collapses NULLs — a NULL prospect is
    # unknown, not the same prospect, so each must count as 1.
    completion_rows = (
        LeasingEvent.objects.filter(
            unit__in=units,
            event_type=EVENT_TYPE_SHOWING_COMPLETE,
            event_date__range=(start_date, end_date),
        )
        .values_list("unit_id", "prospect_id", "event_date")
    )

    seen_completions = set()
    for unit_id, prospect_id, event_date in completion_rows:
        if unit_id not in result:
            continue
        if prospect_id is None:
            result[unit_id]["showings_completed"] += 1
        else:
            key = (unit_id, prospect_id, event_date)
            if key not in seen_completions:
                seen_completions.add(key)
                result[unit_id]["showings_completed"] += 1

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


# ---------------------------------------------------------------------------
# Segment benchmarks
# ---------------------------------------------------------------------------

def compute_segment_benchmarks(period_end, window_days=90, min_segment=5):
    """
    Compute exposure-weighted leasing benchmarks segmented by bedroom count.

    Uses a trailing window of UnitLeasingSnapshot rows ending at period_end.
    Each snapshot row contributes its ACTUAL exposure days, derived from
    date_marked_available on that row:

        listed_from = max(row.period_start, row.date_marked_available)
        exposure_days = (row.period_end - listed_from).days + 1, floored at 0

    Rows with no date_marked_available are skipped entirely — exposure is
    unknown and including them would dilute the benchmark.

    Rates are computed as:
        leads_per_week = total_leads / total_exposure_days * 7
        showings_per_week = total_showings / total_exposure_days * 7
        avg_days_on_market = mean of non-null days_on_market values

    Args:
        period_end: date object. Window is [period_end - window_days + 1, period_end].
        window_days: int, trailing window size in days.
        min_segment: int, minimum number of DISTINCT UNITS (not snapshot rows)
            for a segment to qualify. Segments below this are omitted.

    Returns:
        dict mapping bedroom count (int) -> {
            "unit_count": int,
            "leads_per_week": float,
            "showings_per_week": float,
            "avg_days_on_market": float or None,
        }
        Segments below min_segment are omitted entirely.

    Query count: 1
        UnitLeasingSnapshot rows in the window filtered to
        date_marked_available__isnull=False, with select_related("unit")
        to get bedrooms in a single joined query. Aggregation happens in
        Python because exposure_days must be computed per-row from period
        bounds and date_marked_available.
    """
    from collections import defaultdict
    from datetime import timedelta

    from .models import UnitLeasingSnapshot

    window_start = period_end - timedelta(days=window_days - 1)

    # Single query: all snapshot rows in the window that have a known
    # listing start date, joined to core.Unit for bedrooms.
    rows = (
        UnitLeasingSnapshot.objects
        .filter(
            period_end__range=(window_start, period_end),
            date_marked_available__isnull=False,
        )
        .select_related("unit")
        .values_list(
            "unit_id",
            "unit__bedrooms",
            "period_start",
            "period_end",
            "date_marked_available",
            "new_leads",
            "showings_completed",
            "days_on_market",
        )
    )

    # Accumulate per-segment totals in Python.
    # Track distinct unit IDs per segment for the min_segment check.
    class _Seg:
        __slots__ = (
            "unit_ids", "total_leads", "total_showings",
            "total_exposure_days", "dom_values",
        )

        def __init__(self):
            self.unit_ids = set()
            self.total_leads = 0
            self.total_showings = 0
            self.total_exposure_days = 0
            self.dom_values = []

    segments = defaultdict(_Seg)

    for (
        unit_id, bedrooms, p_start, p_end,
        dma, new_leads, showings_completed, dom,
    ) in rows:
        if bedrooms is None:
            continue

        listed_from = max(p_start, dma)
        exposure_days = max((p_end - listed_from).days + 1, 0)
        if exposure_days == 0:
            continue

        seg = segments[bedrooms]
        seg.unit_ids.add(unit_id)
        seg.total_leads += new_leads or 0
        seg.total_showings += showings_completed or 0
        seg.total_exposure_days += exposure_days
        if dom is not None:
            seg.dom_values.append(dom)

    # Build result, suppressing thin segments.
    result = {}
    for bedrooms, seg in segments.items():
        unit_count = len(seg.unit_ids)
        if unit_count < min_segment:
            continue
        if seg.total_exposure_days == 0:
            continue

        avg_dom = None
        if seg.dom_values:
            avg_dom = sum(seg.dom_values) / len(seg.dom_values)

        result[bedrooms] = {
            "unit_count": unit_count,
            "leads_per_week": seg.total_leads / seg.total_exposure_days * 7,
            "showings_per_week": seg.total_showings / seg.total_exposure_days * 7,
            "avg_days_on_market": avg_dom,
        }

    return result

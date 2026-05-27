"""
Data-gathering functions for weekly reports.

Wraps shared services from market.services.leasing_queries and adds
weekly_reports-specific queries (portfolio benchmark, recent history).
"""

import logging
from datetime import timedelta

from django.db.models import Sum
from django.db.models.functions import Coalesce

from dashboard.models import PropertyWeeklyNote
from leasing.models import DailyLeasingMetric
from market.services.leasing_queries import (
    get_latest_snapshots,
    get_showing_feedback,
    showing_outcome_counts,
)

logger = logging.getLogger(__name__)


def _dlm_sums(unit_ids, date_gte=None, date_lte=None):
    """SUM per-day raw counts from DailyLeasingMetric, keyed by unit_id.

    Returns dict[unit_id] -> {leads, showings, apps, missed}.
    """
    qs = DailyLeasingMetric.objects.filter(unit_id__in=unit_ids)
    if date_gte is not None:
        qs = qs.filter(date__gte=date_gte)
    if date_lte is not None:
        qs = qs.filter(date__lte=date_lte)

    result = {}
    for row in qs.values("unit_id").annotate(
        leads=Coalesce(Sum("new_prospects"), 0),
        showings=Coalesce(Sum("showings_completed"), 0),
        apps=Coalesce(Sum("applications_submitted"), 0),
        missed=Coalesce(Sum("showings_missed_or_failed"), 0),
    ):
        result[row["unit_id"]] = row
    return result


def get_weekly_metrics(unit_ids, start_date, end_date):
    """Get weekly leasing metrics for units in a date range.

    Returns dict keyed by unit_id: {leads, showings, apps, missed}.
    """
    return _dlm_sums(unit_ids, start_date, end_date)


def get_snapshot_data(unit_ids):
    """Get latest DailyUnitSnapshot data for units.

    Returns (dict keyed by unit_id: {days_on_market, listed_price, status}, snapshot_date).
    """
    return get_latest_snapshots(unit_ids)


def get_showing_feedback_for_unit(unit_id, start_date, end_date):
    """Get showing feedback for a unit in a date range.

    Returns list of feedback text strings.
    """
    return get_showing_feedback(unit_id, start_date, end_date)


def get_recent_history(unit_ids, before_date, num_weeks=3):
    """Get recent PropertyWeeklyNote records for context.

    Args:
        unit_ids: List of unit IDs.
        before_date: Only include notes before this date.
        num_weeks: Max notes per unit to return.

    Returns dict keyed by unit_id with list of {week_date, note_text, author}.
    """
    history_map = {}
    for note in PropertyWeeklyNote.objects.filter(
        unit_id__in=unit_ids,
        week_date__lt=before_date,
    ).order_by("unit_id", "-week_date"):
        bucket = history_map.setdefault(note.unit_id, [])
        if len(bucket) < num_weeks:
            bucket.append({
                "week_date": note.week_date.isoformat(),
                "note_text": note.note_text,
                "author": note.author,
            })
    return history_map


def get_portfolio_benchmark(unit_ids, start_date, end_date):
    """Compute portfolio-level benchmarks across active units for the period.

    Returns dict: {avg_leads, avg_showings, avg_apps, avg_dom,
                   active_vacancy_count, lead_to_showing_pct,
                   showing_to_app_pct, unit_count,
                   total_leads, total_showings, total_apps,
                   total_canceled, total_missed,
                   avg_leads_per_week, avg_showings_per_week}.
    """
    base = {
        "avg_leads": 0, "avg_showings": 0, "avg_apps": 0, "avg_dom": 0,
        "active_vacancy_count": 0, "lead_to_showing_pct": 0,
        "showing_to_app_pct": 0, "unit_count": 0,
        "total_leads": 0, "total_showings": 0, "total_apps": 0,
        "total_canceled": 0, "total_missed": 0,
        "avg_leads_per_week": 0, "avg_showings_per_week": 0,
    }
    if not unit_ids:
        return base

    period_sums = _dlm_sums(unit_ids, start_date, end_date)
    snapshots, _ = get_latest_snapshots(unit_ids, status="active")

    n = len(period_sums) or len(unit_ids)
    total_leads = sum(d.get("leads", 0) for d in period_sums.values())
    total_showings = sum(d.get("showings", 0) for d in period_sums.values())
    total_apps = sum(d.get("apps", 0) for d in period_sums.values())

    dom_values = [s.get("days_on_market", 0) for s in snapshots.values() if s.get("days_on_market")]
    avg_dom = round(sum(dom_values) / len(dom_values), 1) if dom_values else 0

    # All-time totals (no date filter)
    alltime = _dlm_sums(unit_ids)
    at_total_leads = sum(a.get("leads", 0) for a in alltime.values())
    at_total_showings = sum(a.get("showings", 0) for a in alltime.values())
    at_total_apps = sum(a.get("apps", 0) for a in alltime.values())

    # All-time cancelled/missed from Showing model
    at_outcomes = showing_outcome_counts(unit_ids)
    at_total_canceled = sum(o.get("canceled", 0) for o in at_outcomes.values())
    at_total_missed = sum(o.get("missed", 0) for o in at_outcomes.values())

    # Per-unit avg rates: leads_per_week and showings_per_week using DOM → weeks_listed
    leads_per_week_vals = []
    showings_per_week_vals = []
    for uid in unit_ids:
        at = alltime.get(uid, {})
        snap = snapshots.get(uid, {})
        dom = snap.get("days_on_market") or 0
        weeks_listed = max(dom / 7, 1)
        leads_per_week_vals.append(at.get("leads", 0) / weeks_listed)
        showings_per_week_vals.append(at.get("showings", 0) / weeks_listed)

    avg_leads_pw = round(sum(leads_per_week_vals) / len(leads_per_week_vals), 1) if leads_per_week_vals else 0
    avg_showings_pw = round(sum(showings_per_week_vals) / len(showings_per_week_vals), 1) if showings_per_week_vals else 0

    return {
        "avg_leads": round(total_leads / n, 1) if n else 0,
        "avg_showings": round(total_showings / n, 1) if n else 0,
        "avg_apps": round(total_apps / n, 1) if n else 0,
        "avg_dom": avg_dom,
        "active_vacancy_count": len(snapshots),
        "lead_to_showing_pct": round(total_showings / total_leads * 100, 1) if total_leads else 0,
        "showing_to_app_pct": round(total_apps / total_showings * 100, 1) if total_showings else 0,
        "unit_count": n,
        "total_leads": at_total_leads,
        "total_showings": at_total_showings,
        "total_apps": at_total_apps,
        "total_canceled": at_total_canceled,
        "total_missed": at_total_missed,
        "avg_leads_per_week": avg_leads_pw,
        "avg_showings_per_week": avg_showings_pw,
    }

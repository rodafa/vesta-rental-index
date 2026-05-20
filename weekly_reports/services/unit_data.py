"""
Data-gathering functions for weekly reports.

Wraps shared services from market.services.leasing_queries and adds
weekly_reports-specific queries (portfolio benchmark, recent history).
"""

import logging
from datetime import timedelta

from dashboard.models import PropertyWeeklyNote
from market.services.leasing_queries import (
    dls_delta,
    get_latest_snapshots,
    get_showing_feedback,
)

logger = logging.getLogger(__name__)


def get_weekly_metrics(unit_ids, start_date, end_date):
    """Get weekly leasing metrics for units in a date range.

    Returns dict keyed by unit_id: {leads, showings, apps}.
    """
    return dls_delta(unit_ids, start_date, end_date)


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
                   showing_to_app_pct, unit_count}.
    """
    base = {
        "avg_leads": 0, "avg_showings": 0, "avg_apps": 0, "avg_dom": 0,
        "active_vacancy_count": 0, "lead_to_showing_pct": 0,
        "showing_to_app_pct": 0, "unit_count": 0,
    }
    if not unit_ids:
        return base

    deltas = dls_delta(unit_ids, start_date, end_date)
    snapshots, _ = get_latest_snapshots(unit_ids, status="active")

    n = len(deltas) or len(unit_ids)
    total_leads = sum(d.get("leads", 0) for d in deltas.values())
    total_showings = sum(d.get("showings", 0) for d in deltas.values())
    total_apps = sum(d.get("apps", 0) for d in deltas.values())

    dom_values = [s.get("days_on_market", 0) for s in snapshots.values() if s.get("days_on_market")]
    avg_dom = round(sum(dom_values) / len(dom_values), 1) if dom_values else 0

    return {
        "avg_leads": round(total_leads / n, 1) if n else 0,
        "avg_showings": round(total_showings / n, 1) if n else 0,
        "avg_apps": round(total_apps / n, 1) if n else 0,
        "avg_dom": avg_dom,
        "active_vacancy_count": len(snapshots),
        "lead_to_showing_pct": round(total_showings / total_leads * 100, 1) if total_leads else 0,
        "showing_to_app_pct": round(total_apps / total_showings * 100, 1) if total_showings else 0,
        "unit_count": n,
    }

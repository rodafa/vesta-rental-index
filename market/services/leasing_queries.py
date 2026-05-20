"""
Shared leasing data queries used by dashboard, analytics, and weekly_reports.

All functions query Django ORM models and return plain dicts.
"""

import logging

from django.db.models import Max, Min
from django.db.models.functions import Coalesce

from leasing.models import Showing
from market.models import DailyLeasingSummary, DailyUnitSnapshot

logger = logging.getLogger(__name__)


def dls_delta(unit_ids, date_gte, date_lte):
    """Compute DailyLeasingSummary cumulative delta for a period, per unit.

    Returns dict keyed by unit_id with keys: leads, showings, missed, apps.
    Callers that don't need 'missed' can simply ignore it.
    """
    cur_qs = DailyLeasingSummary.objects.filter(
        unit_id__in=unit_ids,
        summary_date__gte=date_gte,
        summary_date__lte=date_lte,
    )
    current = {}
    for row in cur_qs.values("unit_id").annotate(
        leads=Coalesce(Max("leads_count"), 0),
        showings=Coalesce(Max("showings_completed_count"), 0),
        missed=Coalesce(Max("showings_missed_count"), 0),
        apps=Coalesce(Max("applications_count"), 0),
    ):
        current[row["unit_id"]] = row

    prior = {}
    for row in DailyLeasingSummary.objects.filter(
        unit_id__in=unit_ids,
        summary_date__lt=date_gte,
    ).values("unit_id").annotate(
        leads=Coalesce(Max("leads_count"), 0),
        showings=Coalesce(Max("showings_completed_count"), 0),
        missed=Coalesce(Max("showings_missed_count"), 0),
        apps=Coalesce(Max("applications_count"), 0),
    ):
        prior[row["unit_id"]] = row

    # For units with no prior data, use min-in-window as baseline
    min_in_window = {}
    for row in cur_qs.values("unit_id").annotate(
        leads=Coalesce(Min("leads_count"), 0),
        showings=Coalesce(Min("showings_completed_count"), 0),
        missed=Coalesce(Min("showings_missed_count"), 0),
        apps=Coalesce(Min("applications_count"), 0),
    ):
        min_in_window[row["unit_id"]] = row

    result = {}
    for uid, cur in current.items():
        prv = prior.get(uid) or min_in_window.get(uid, {})
        result[uid] = {
            "unit_id": uid,
            "leads": max(cur["leads"] - prv.get("leads", 0), 0),
            "showings": max(cur["showings"] - prv.get("showings", 0), 0),
            "missed": max(cur["missed"] - prv.get("missed", 0), 0),
            "apps": max(cur["apps"] - prv.get("apps", 0), 0),
        }
    return result


def get_showing_feedback(unit_id, week_start, week_end):
    """Collect showing feedback text for completed showings in the date range.

    Returns a list of feedback strings.
    """
    showings = Showing.objects.filter(
        unit_id=unit_id,
        status="completed",
        completed_at__date__range=[week_start, week_end],
    ).exclude(feedback={})

    texts = []
    for s in showings:
        fb = s.feedback or {}
        text = (
            fb.get("comments")
            or fb.get("overall_comments")
            or fb.get("summary")
            or ""
        )
        if text:
            texts.append(str(text).strip())
    return texts


def get_latest_snapshots(unit_ids=None, status=None):
    """Return the latest DailyUnitSnapshot for each unit, optionally filtered.

    Args:
        unit_ids: Optional list of unit IDs to filter.
        status: Optional status string (e.g. "active") to filter.

    Returns:
        dict keyed by unit_id with snapshot values (days_on_market, listed_price, status).
        Also returns the snapshot_date as a second value.
    """
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    if not latest_date:
        return {}, None

    qs = DailyUnitSnapshot.objects.filter(snapshot_date=latest_date)
    if unit_ids is not None:
        qs = qs.filter(unit_id__in=unit_ids)
    if status:
        qs = qs.filter(status=status)

    snapshots = {}
    for s in qs.values("unit_id", "days_on_market", "listed_price", "status"):
        snapshots[s["unit_id"]] = s

    return snapshots, latest_date

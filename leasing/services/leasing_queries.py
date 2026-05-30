"""
Query layer for Leasing Intelligence.

Provides window-aggregated delta sums and point-in-time state fields
for DailyLeasingMetric data.
"""

import datetime
import logging

from django.db.models import Avg, Count, OuterRef, Q, Subquery, Sum

from leasing.models import DailyLeasingMetric
from leasing.models import Showing

logger = logging.getLogger(__name__)


def get_unit_metrics(date_from, date_to, portfolio=None):
    """
    Return one dict per unit with summed deltas over the window and
    state fields from the nearest row to *date_to*.

    Each dict contains:
        unit_id, address, leads, showings, apps_submitted,
        apps_requested, missed_failed, outbound_texts, total_calls,
        dom, health
    """
    qs = DailyLeasingMetric.objects.filter(date__range=(date_from, date_to))
    if portfolio:
        qs = qs.filter(unit__property__portfolio=portfolio)

    # Subquery: nearest row <= date_to for each unit (state fields)
    nearest = (
        DailyLeasingMetric.objects.filter(
            unit=OuterRef("unit"),
            date__lte=date_to,
        )
        .order_by("-date")
    )

    rows = (
        qs.values("unit", "unit__property__address_line_1", "unit__name", "unit__address_line_2")
        .annotate(
            leads=Sum("new_prospects"),
            showings=Sum("showings_completed"),
            apps_submitted=Sum("applications_submitted"),
            apps_requested=Sum("applications_requested"),
            missed_failed=Sum("showings_missed_or_failed"),
            outbound_texts=Sum("outbound_texts"),
            total_calls=Sum("total_calls"),
            dom=Subquery(nearest.values("days_on_market")[:1]),
            health=Subquery(nearest.values("property_health")[:1]),
        )
        .order_by("unit__property__address_line_1", "unit__address_line_2", "unit__name")
    )

    # Cancelled showings from Showing model for the window
    unit_ids_in_window = [r["unit"] for r in qs.values("unit").distinct()]
    canceled_qs = (
        Showing.objects.filter(
            unit_id__in=unit_ids_in_window,
            status="canceled",
            scheduled_at__date__range=(date_from, date_to),
        )
        .values("unit_id")
        .annotate(canceled=Count("id"))
    )
    canceled_by_unit = {r["unit_id"]: r["canceled"] for r in canceled_qs}

    results = []
    for row in rows:
        base = row["unit__property__address_line_1"] or ""
        line2 = (row["unit__address_line_2"] or "").strip()
        unit_name = (row["unit__name"] or "").strip()

        # Mirror Unit.display_address logic:
        # prefer address_line_2 as suffix, fall back to name
        suffix = line2
        if not suffix and unit_name:
            name_lower = unit_name.casefold()
            base_lower = base.casefold()
            # suppress if name duplicates line2 or is a word-subset of base
            if name_lower == base_lower:
                pass
            elif set(name_lower.split()) <= set(base_lower.split()):
                pass
            else:
                suffix = unit_name
        address = f"{base} - {suffix}" if suffix else base

        results.append({
            "unit_id": row["unit"],
            "address": address,
            "leads": row["leads"] or 0,
            "showings": row["showings"] or 0,
            "apps_submitted": row["apps_submitted"] or 0,
            "apps_requested": row["apps_requested"] or 0,
            "missed_failed": row["missed_failed"] or 0,
            "canceled": canceled_by_unit.get(row["unit"], 0),
            "outbound_texts": row["outbound_texts"] or 0,
            "total_calls": row["total_calls"] or 0,
            "dom": row["dom"],
            "health": row["health"] or "",
        })

    logger.info(
        "get_unit_metrics: %s–%s, portfolio=%s, units=%d",
        date_from, date_to, portfolio, len(results),
    )
    return results


def _aggregate_window(date_from, date_to, portfolio=None):
    """Sum deltas and compute aggregate stats for a single window."""
    qs = DailyLeasingMetric.objects.filter(date__range=(date_from, date_to))
    if portfolio:
        qs = qs.filter(unit__property__portfolio=portfolio)

    agg = qs.aggregate(
        total_leads=Sum("new_prospects"),
        total_showings=Sum("showings_completed"),
        total_apps=Sum("applications_submitted"),
        total_apps_requested=Sum("applications_requested"),
        total_missed=Sum("showings_missed_or_failed"),
        active_units=Count("unit", distinct=True),
        avg_dom=Avg("days_on_market"),
    )

    # Cancelled showings from Showing model
    showing_qs = Showing.objects.filter(
        status="canceled",
        scheduled_at__date__range=(date_from, date_to),
    )
    if portfolio:
        showing_qs = showing_qs.filter(unit__property__portfolio=portfolio)
    total_canceled = showing_qs.count()

    return {
        "total_leads": agg["total_leads"] or 0,
        "total_showings": agg["total_showings"] or 0,
        "total_apps": agg["total_apps"] or 0,
        "total_apps_requested": agg["total_apps_requested"] or 0,
        "total_missed": agg["total_missed"] or 0,
        "total_canceled": total_canceled,
        "active_units": agg["active_units"] or 0,
        "avg_dom": round(agg["avg_dom"], 1) if agg["avg_dom"] is not None else None,
    }


def get_portfolio_kpis(date_from, date_to, portfolio=None):
    """
    Return KPIs for the current window and the prior window of equal length.

    Returns ``{"current": {...}, "prior": {...}, "deltas": {...}}``.
    """
    current = _aggregate_window(date_from, date_to, portfolio)

    length = (date_to - date_from).days + 1
    prior_to = date_from - datetime.timedelta(days=1)
    prior_from = prior_to - datetime.timedelta(days=length - 1)
    prior = _aggregate_window(prior_from, prior_to, portfolio)

    deltas = {}
    for key in current:
        cur_val = current[key]
        pri_val = prior[key]
        if cur_val is not None and pri_val is not None:
            deltas[key] = cur_val - pri_val
        else:
            deltas[key] = None

    logger.info(
        "get_portfolio_kpis: %s–%s, portfolio=%s", date_from, date_to, portfolio,
    )
    return {"current": current, "prior": prior, "deltas": deltas}

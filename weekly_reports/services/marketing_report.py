"""
Marketing report data assembly.

Builds the full dataset for the Marketing Report page: per-unit rows with
this-week metrics, prior-week metrics, all-time totals, averages, notes,
and owner information.
"""

import logging
from datetime import timedelta

from dashboard.models import PropertyWeeklyNote
from market.services.leasing_queries import (
    dls_alltime,
    dls_delta,
    get_latest_snapshots,
)
from properties.models import Owner, Unit

from weekly_reports.services.unit_data import get_portfolio_benchmark

logger = logging.getLogger(__name__)


def build_marketing_report(week_start, week_end):
    """Assemble marketing report data for the given week.

    Args:
        week_start: Inclusive start date of the reporting period.
        week_end: Inclusive end date of the reporting period.

    Returns:
        dict with keys ``rows`` (list of per-unit dicts) and ``benchmarks``.
    """
    # Monday anchor for notes — must match PropertyWeeklyNote.week_date convention
    monday = week_start - timedelta(days=week_start.weekday())

    # Prior week range
    prev_end = week_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    # 1. Active units via latest snapshots
    all_units = list(
        Unit.objects.filter(is_active=True)
        .select_related("property__portfolio")
    )
    all_ids = [u.id for u in all_units]
    snapshots, _ = get_latest_snapshots(all_ids, status="active")
    listed_units = [u for u in all_units if u.id in snapshots]
    listed_ids = [u.id for u in listed_units]

    if not listed_ids:
        return {"rows": [], "benchmarks": get_portfolio_benchmark([], week_start, week_end)}

    # 2. This-week metrics
    this_week = dls_delta(listed_ids, week_start, week_end)

    # 3. Prior-week metrics
    prev_week = dls_delta(listed_ids, prev_start, prev_end)

    # 4. All-time totals
    alltime = dls_alltime(listed_ids)

    # 5. Notes for this week
    notes_map = {}
    for n in PropertyWeeklyNote.objects.filter(unit_id__in=listed_ids, week_date=monday):
        notes_map[n.unit_id] = n

    # 6. Owner lookup: Unit → Property → Portfolio ← M2M → Owner
    portfolio_ids = set()
    unit_portfolio = {}
    for u in listed_units:
        pid = u.property.portfolio_id if u.property and u.property.portfolio_id else None
        unit_portfolio[u.id] = pid
        if pid:
            portfolio_ids.add(pid)

    # Build portfolio_id → [owners] map
    portfolio_owners = {}
    if portfolio_ids:
        for owner in Owner.objects.filter(portfolios__id__in=portfolio_ids).prefetch_related("portfolios"):
            for p in owner.portfolios.all():
                portfolio_owners.setdefault(p.id, []).append(owner)

    # 7. Benchmarks
    benchmarks = get_portfolio_benchmark(listed_ids, week_start, week_end)

    # 8. Build rows
    rows = []
    for u in listed_units:
        snap = snapshots.get(u.id, {})
        tw = this_week.get(u.id, {})
        pw = prev_week.get(u.id, {})
        at = alltime.get(u.id, {})
        note = notes_map.get(u.id)

        dom = snap.get("days_on_market") or 0
        weeks_listed = max(dom / 7, 1)

        at_leads = at.get("leads", 0)
        at_showings = at.get("showings", 0)
        at_apps = at.get("apps", 0)

        # Owner info
        pid = unit_portfolio.get(u.id)
        owners = portfolio_owners.get(pid, []) if pid else []
        owner_names = ", ".join(o.name for o in owners) if owners else ""
        owner_emails = ", ".join(o.email for o in owners if o.email) if owners else ""

        portfolio_name = ""
        if u.property and u.property.portfolio:
            portfolio_name = u.property.portfolio.name

        rows.append({
            "unit_id": u.id,
            "address": u.display_address,
            "beds": u.bedrooms or 0,
            "baths": (u.full_bathrooms or 0) + (u.half_bathrooms or 0) * 0.5,
            "owner_names": owner_names,
            "owner_emails": owner_emails,
            "portfolio_name": portfolio_name,
            "dom": dom,
            "listed_price": float(snap.get("listed_price") or 0),
            "zillow_url": u.zillow_url,
            # This week
            "leads": tw.get("leads", 0),
            "showings": tw.get("showings", 0),
            "apps": tw.get("apps", 0),
            # Prior week
            "prev_leads": pw.get("leads", 0),
            "prev_showings": pw.get("showings", 0),
            "prev_apps": pw.get("apps", 0),
            # All-time
            "alltime_leads": at_leads,
            "alltime_showings": at_showings,
            "alltime_apps": at_apps,
            # Avg per week
            "avg_leads_per_week": round(at_leads / weeks_listed, 1),
            "avg_showings_per_week": round(at_showings / weeks_listed, 1),
            # Note
            "note_id": note.id if note else None,
            "note_text": note.note_text if note else "",
            "note_approved": note.approved if note else False,
            "note_author": note.author if note else "",
        })

    return {"rows": rows, "benchmarks": benchmarks}

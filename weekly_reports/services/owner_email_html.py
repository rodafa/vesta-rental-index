"""
Build rendered HTML for owner weekly leasing emails.
"""

from django.conf import settings
from django.template.loader import render_to_string


def build_owner_email_html(owner, units_data, benchmarks, week_start, week_end):
    """Render the owner email HTML template.

    Args:
        owner: Owner model instance.
        units_data: List of dicts with unit info (from marketing report rows).
        benchmarks: Portfolio benchmark dict.
        week_start: Start date of reporting week.
        week_end: End date of reporting week.

    Returns:
        Rendered HTML string.
    """
    # Prepare per-unit template data with WoW deltas
    units = []

    for u in units_data:
        units.append({
            "address": u["address"],
            "beds": u.get("beds", 0),
            "baths": u.get("baths", 0),
            "listed_price": u.get("listed_price", 0),
            "dom": u.get("dom", 0),
            "leads": u.get("leads", 0),
            "showings": u.get("showings", 0),
            "apps": u.get("apps", 0),
            "canceled": u.get("canceled", 0),
            "missed": u.get("missed", 0),
            "lead_delta": u.get("leads", 0) - u.get("prev_leads", 0),
            "showing_delta": u.get("showings", 0) - u.get("prev_showings", 0),
            "app_delta": u.get("apps", 0) - u.get("prev_apps", 0),
            "alltime_leads": u.get("alltime_leads", 0),
            "alltime_showings": u.get("alltime_showings", 0),
            "alltime_apps": u.get("alltime_apps", 0),
            "avg_leads_per_week": u.get("avg_leads_per_week", 0),
            "avg_showings_per_week": u.get("avg_showings_per_week", 0),
            "zillow_url": u.get("zillow_url", ""),
            "note_text": u.get("note_text", ""),
        })

    context = {
        "owner_name": owner.first_name or owner.name,
        "units": units,
        "unit_count": len(units),
        "benchmarks": benchmarks,
        "week_start": week_start,
        "week_end": week_end,
        "logo_url": getattr(settings, "VESTA_LOGO_URL", ""),
    }

    return render_to_string("weekly_reports/owner_email.html", context)

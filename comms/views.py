"""Dashboard views for the comms app."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render


@login_required
def monthly_notes_page(request):
    """Serve the Monthly Portfolio Notes dashboard page."""
    if not request.user.can_access("monthly_owner_notes"):
        return HttpResponseForbidden("Access denied.")

    from accounting.models import PortfolioStatement

    return render(request, "comms/dashboard/monthly_notes.html", {
        "statement_count": PortfolioStatement.objects.count(),
    })


@login_required
def maintenance_notes_page(request):
    """Serve the Weekly Maintenance Notes dashboard page."""
    if not request.user.can_access("maintenance"):
        return HttpResponseForbidden("Access denied.")

    from datetime import date, timedelta

    from .maintenance_services import default_maintenance_week_start

    week_param = request.GET.get("week")
    if week_param:
        try:
            anchor = date.fromisoformat(week_param)
            week_start = anchor - timedelta(days=anchor.weekday())
        except ValueError:
            week_start = default_maintenance_week_start()
    else:
        week_start = default_maintenance_week_start()

    week_end = week_start + timedelta(days=6)

    return render(request, "comms/dashboard/maintenance_notes.html", {
        "default_week_start": week_start.isoformat(),
        "default_week_end": week_end.isoformat(),
    })


@login_required
def distribution_page(request):
    """Serve the Owner Distributions dashboard page."""
    if not request.user.can_access("owner_distributions"):
        return HttpResponseForbidden("Access denied.")

    return render(request, "comms/dashboard/distributions.html")

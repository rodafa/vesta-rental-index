"""Dashboard views for the comms app."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render


@login_required
def monthly_notes_page(request):
    """Serve the Monthly Portfolio Notes dashboard page."""
    if not request.user.can_access("monthly_owner_notes"):
        return HttpResponseForbidden("Access denied.")
    return render(request, "comms/dashboard/monthly_notes.html")

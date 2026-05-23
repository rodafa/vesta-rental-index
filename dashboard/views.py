from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dashboard.models import UnitNote
from leasing.models import Lease
from properties.models import Portfolio, Property, Unit


@login_required
def daily_pulse(request):
    return render(request, "dashboard/daily_pulse.html")


@login_required
def property_detail(request, unit_id):
    return render(request, "dashboard/property_detail.html", {"unit_id": unit_id})


@login_required
def portfolio_analytics(request):
    return render(request, "dashboard/portfolio_analytics.html")


@login_required
def owner_reports(request):
    return render(request, "dashboard/owner_reports.html")


@login_required
def renewal_pipeline(request):
    return render(request, "dashboard/renewal_pipeline.html")


@login_required
def renewal_month_detail(request, month):
    return render(request, "dashboard/renewal_month_detail.html", {"month": month})


@login_required
def leasing_pipeline(request):
    return render(request, "dashboard/leasing_pipeline.html")


@login_required
def maintenance_emails(request):
    return render(request, "dashboard/maintenance_emails.html")


@login_required
def owner_notes(request):
    return render(request, "dashboard/owner_notes.html")


@login_required
def monthly_notes(request):
    return render(request, "dashboard/monthly_notes.html")


@login_required
def weekly_reports(request):
    from django.conf import settings as django_settings

    return render(request, "dashboard/weekly_reports.html", {
        "show_showing_outcomes": getattr(django_settings, "SHOW_SHOWING_OUTCOMES", True),
        "user_email": request.user.email if request.user.is_authenticated else "",
    })


@login_required
def weekly_report_detail(request, unit_id):
    return render(request, "dashboard/weekly_report_detail.html", {"unit_id": unit_id})


class PasswordChangeCompleteView(View):
    """Clear the must_change_password flag and redirect to dashboard."""

    def get(self, request):
        try:
            profile = request.user.staff_profile
            profile.must_change_password = False
            profile.save(update_fields=["must_change_password"])
        except Exception:
            pass
        return redirect("dashboard:daily_pulse")


def owner_dashboard(request, portfolio_slug):
    """Public-facing read-only owner dashboard. No auth required."""
    portfolio = get_object_or_404(Portfolio, slug=portfolio_slug)
    properties = (
        Property.objects.filter(portfolio=portfolio, is_active=True)
        .prefetch_related("units")
        .order_by("city", "address_line_1")
    )

    # Active leases keyed by unit_id → lease
    active_leases = {}
    for lease in Lease.objects.filter(
        primary_lease_status=2,
        unit__property__portfolio=portfolio,
    ).select_related("unit"):
        active_leases[lease.unit_id] = lease

    active_lease_unit_ids = set(active_leases.keys())

    property_data = []
    total_units = 0
    total_occupied = 0
    rent_sum = Decimal("0")
    rent_count = 0

    # IDs of non-revenue units to exclude
    non_revenue_ids = set(
        Unit.objects.filter(
            raw_data__unit__isNonRevenue="1"
        ).values_list("id", flat=True)
    )

    for prop in properties:
        prop_units = [
            u for u in prop.units.filter(is_active=True)
            if u.id not in non_revenue_ids
        ]
        units_with_rent = []
        occupied = 0
        for u in prop_units:
            lease = active_leases.get(u.id)
            is_occupied = u.id in active_lease_unit_ids
            if is_occupied:
                occupied += 1
            current_rent = lease.rent_amount if lease else None
            if current_rent:
                rent_sum += current_rent
                rent_count += 1
            units_with_rent.append(
                {
                    "unit": u,
                    "is_occupied": is_occupied,
                    "current_rent": current_rent,
                    "lease_end": lease.end_date if lease else None,
                }
            )

        total_units += len(prop_units)
        total_occupied += occupied
        property_data.append(
            {
                "property": prop,
                "units": units_with_rent,
                "total_units": len(prop_units),
                "occupied": occupied,
                "vacant": len(prop_units) - occupied,
            }
        )

    total_vacant = total_units - total_occupied
    occupancy_rate = (
        round(total_occupied / total_units * 100, 1) if total_units else 0
    )
    avg_rent = round(rent_sum / rent_count, 0) if rent_count else None

    # Staff notes grouped by unit_id for this portfolio
    notes_by_unit = defaultdict(list)
    for note in UnitNote.objects.filter(
        unit__property__portfolio=portfolio
    ).order_by("-created_at"):
        notes_by_unit[note.unit_id].append(note)

    return render(
        request,
        "dashboard/owner_dashboard.html",
        {
            "portfolio": portfolio,
            "properties": property_data,
            "total_units": total_units,
            "total_occupied": total_occupied,
            "total_vacant": total_vacant,
            "occupancy_rate": occupancy_rate,
            "avg_rent": avg_rent,
            "active_lease_unit_ids": active_lease_unit_ids,
            "notes_by_unit": dict(notes_by_unit),
        },
    )

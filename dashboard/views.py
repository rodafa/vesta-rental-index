from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Avg
from django.shortcuts import get_object_or_404, render

from dashboard.models import UnitNote
from leasing.models import Lease
from properties.models import Portfolio, Property, Unit


def daily_pulse(request):
    return render(request, "dashboard/daily_pulse.html", {"today": date.today()})


def property_detail(request, unit_id):
    return render(request, "dashboard/property_detail.html", {"unit_id": unit_id})


def portfolio_analytics(request):
    return render(request, "dashboard/portfolio_analytics.html")


def owner_reports(request):
    return render(request, "dashboard/owner_reports.html")


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

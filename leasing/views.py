from django.db.models import Q
from django.shortcuts import render

from properties.models import Unit
from .models import Lease


def show_me_leases(request):
    leases = (
        Lease.objects.select_related("unit", "property")
        .prefetch_related("tenants")
        .order_by("-start_date")
    )

    # Units with no active lease are vacant / available
    active_lease_unit_ids = (
        Lease.objects.filter(primary_lease_status=2)
        .values_list("unit_id", flat=True)
    )
    vacant_units = (
        Unit.objects.filter(is_active=True)
        .exclude(id__in=active_lease_unit_ids)
        .select_related("property")
        .order_by("property__city", "address_line_1")
    )

    return render(request, "leasing/dashboard.html", {
        "leases": leases,
        "vacant_units": vacant_units,
        "vacant_count": vacant_units.count(),
        "active_count": leases.filter(primary_lease_status=2).count(),
        "pending_count": leases.filter(primary_lease_status=1).count(),
        "closed_count": leases.filter(primary_lease_status=3).count(),
    })

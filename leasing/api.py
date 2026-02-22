from django.db.models import Count
from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import LimitOffsetPagination, paginate

from leasing.models import (
    Application,
    Lease,
    LeasingEvent,
    Prospect,
    Showing,
    Tenant,
)
from leasing.schemas import (
    ApplicationFilterSchema,
    ApplicationListSchema,
    ApplicationSchema,
    LeaseFilterSchema,
    LeaseListSchema,
    LeaseSchema,
    LeasingEventFilterSchema,
    LeasingEventSchema,
    ProspectFilterSchema,
    ProspectListSchema,
    ProspectSchema,
    ShowingFilterSchema,
    ShowingSchema,
    TenantFilterSchema,
    TenantListSchema,
    TenantSchema,
)

router = Router(tags=["Leasing"])


# --- Tenants ---


@router.get("/tenants", response=list[TenantListSchema])
@paginate(LimitOffsetPagination)
def list_tenants(request, filters: Query[TenantFilterSchema]):
    qs = Tenant.objects.all()
    return filters.filter(qs)


@router.get("/tenants/{tenant_id}", response=TenantSchema)
def get_tenant(request, tenant_id: int):
    return get_object_or_404(Tenant, id=tenant_id)


# --- Leases ---


@router.get("/leases", response=list[LeaseListSchema])
@paginate(LimitOffsetPagination)
def list_leases(request, filters: Query[LeaseFilterSchema]):
    qs = Lease.objects.select_related("unit", "property").prefetch_related(
        "tenants"
    )
    return filters.filter(qs)


@router.get("/leases/{lease_id}", response=LeaseSchema)
def get_lease(request, lease_id: int):
    return get_object_or_404(
        Lease.objects.select_related("unit", "property").prefetch_related(
            "tenants"
        ),
        id=lease_id,
    )


# --- Prospects ---


@router.get("/prospects", response=list[ProspectListSchema])
@paginate(LimitOffsetPagination)
def list_prospects(request, filters: Query[ProspectFilterSchema]):
    qs = Prospect.objects.select_related("unit_of_interest")
    return filters.filter(qs)


@router.get("/prospects/{prospect_id}", response=ProspectSchema)
def get_prospect(request, prospect_id: int):
    return get_object_or_404(
        Prospect.objects.select_related("unit_of_interest"),
        id=prospect_id,
    )


# --- LeasingEvents ---


@router.get("/leasing-events", response=list[LeasingEventSchema])
@paginate(LimitOffsetPagination)
def list_leasing_events(request, filters: Query[LeasingEventFilterSchema]):
    qs = LeasingEvent.objects.all()
    return filters.filter(qs)


@router.get("/leasing-events/{event_id}", response=LeasingEventSchema)
def get_leasing_event(request, event_id: int):
    return get_object_or_404(LeasingEvent, id=event_id)


# --- Showings ---


@router.get("/showings", response=list[ShowingSchema])
@paginate(LimitOffsetPagination)
def list_showings(request, filters: Query[ShowingFilterSchema]):
    qs = Showing.objects.all()
    return filters.filter(qs)


@router.get("/showings/{showing_id}", response=ShowingSchema)
def get_showing(request, showing_id: int):
    return get_object_or_404(Showing, id=showing_id)


# --- Applications ---


@router.get("/applications", response=list[ApplicationListSchema])
@paginate(LimitOffsetPagination)
def list_applications(request, filters: Query[ApplicationFilterSchema]):
    qs = Application.objects.select_related("unit").annotate(
        applicant_count=Count("applicants")
    )
    return filters.filter(qs)


@router.get("/applications/{application_id}", response=ApplicationSchema)
def get_application(request, application_id: int):
    return get_object_or_404(
        Application.objects.select_related("unit").prefetch_related(
            "applicants"
        ),
        id=application_id,
    )

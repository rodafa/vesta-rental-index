import calendar
from collections import defaultdict
from datetime import date
from typing import Optional

from django.db.models import (
    Avg,
    Count,
    DateField,
    DecimalField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from ninja import Router
from ninja.pagination import LimitOffsetPagination, paginate

from leasing.models import Application, Lease, Prospect, Showing
from properties.models import Property, Unit

from django.db.models.functions import Coalesce

from market.models import DailyLeasingSummary, DailyUnitSnapshot
from properties.models import Owner, Portfolio

from .schemas import (
    ActiveListingSchema,
    LeaseExpirationDetailSchema,
    LeaseExpirationMonthSchema,
    LeasingFunnelSchema,
    OwnerVacancySchema,
    PortfolioSummarySchema,
    PropertyPerformanceSchema,
    ProspectSourceSchema,
    RentSegmentSchema,
    VacantUnitSchema,
)

router = Router(tags=["Analytics"])


def _pct(numerator: int, denominator: int) -> float:
    """Safe percentage: (numerator / denominator) * 100, rounded to 2 decimals."""
    return round(numerator / denominator * 100, 2) if denominator else 0.0


# ---------------------------------------------------------------------------
# 1. Portfolio Summary
# ---------------------------------------------------------------------------


@router.get("/portfolio-summary", response=PortfolioSummarySchema)
def portfolio_summary(
    request,
    postal_code: Optional[str] = None,
    bedrooms: Optional[int] = None,
):
    unit_qs = Unit.revenue_units()
    if postal_code:
        unit_qs = unit_qs.filter(postal_code=postal_code)
    if bedrooms is not None:
        unit_qs = unit_qs.filter(bedrooms=bedrooms)

    # Properties that own at least one matching revenue unit
    property_ids = unit_qs.values_list("property_id", flat=True).distinct()
    total_properties = Property.objects.filter(id__in=property_ids).count()

    total_units = unit_qs.count()
    occupied_units = (
        unit_qs.filter(leases__primary_lease_status=2).distinct().count()
    )
    vacant_units = total_units - occupied_units

    avg_target = unit_qs.aggregate(avg=Avg("target_rental_rate"))["avg"]

    lease_filter = Q(primary_lease_status=2, unit__in=unit_qs)
    avg_lease_rent = Lease.objects.filter(lease_filter).aggregate(
        avg=Avg("rent_amount")
    )["avg"]

    lease_qs = Lease.objects.filter(unit__in=unit_qs)
    lease_counts = lease_qs.aggregate(
        active=Count("id", filter=Q(primary_lease_status=2)),
        pending=Count("id", filter=Q(primary_lease_status=1)),
        closed=Count("id", filter=Q(primary_lease_status=3)),
    )

    return {
        "total_properties": total_properties,
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "vacancy_rate": _pct(vacant_units, total_units),
        "avg_target_rent": avg_target,
        "avg_active_lease_rent": avg_lease_rent,
        "active_leases": lease_counts["active"],
        "pending_leases": lease_counts["pending"],
        "closed_leases": lease_counts["closed"],
        "total_prospects": Prospect.objects.count(),
        "total_showings": Showing.objects.count(),
        "total_applications": Application.objects.count(),
    }


# ---------------------------------------------------------------------------
# 2. Leasing Funnel
# ---------------------------------------------------------------------------


@router.get("/leasing-funnel", response=LeasingFunnelSchema)
def leasing_funnel(
    request,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    property_id: Optional[int] = None,
    portfolio_id: Optional[int] = None,
):
    prospect_f = Q()
    showing_f = Q()
    app_f = Q()
    lease_f = Q(is_renewal=False)

    if date_from:
        prospect_f &= Q(created_at__date__gte=date_from)
        showing_f &= Q(created_at__date__gte=date_from)
        app_f &= Q(created_at__date__gte=date_from)
        lease_f &= Q(start_date__gte=date_from)
    if date_to:
        prospect_f &= Q(created_at__date__lte=date_to)
        showing_f &= Q(created_at__date__lte=date_to)
        app_f &= Q(created_at__date__lte=date_to)
        lease_f &= Q(start_date__lte=date_to)

    if property_id:
        prospect_f &= Q(unit_of_interest__property_id=property_id)
        showing_f &= Q(unit__property_id=property_id)
        app_f &= Q(unit__property_id=property_id)
        lease_f &= Q(property_id=property_id)
    if portfolio_id:
        prospect_f &= Q(unit_of_interest__property__portfolio_id=portfolio_id)
        showing_f &= Q(unit__property__portfolio_id=portfolio_id)
        app_f &= Q(unit__property__portfolio_id=portfolio_id)
        lease_f &= Q(property__portfolio_id=portfolio_id)

    total_prospects = Prospect.objects.filter(prospect_f).count()
    total_showings_completed = Showing.objects.filter(
        showing_f, status="completed"
    ).count()
    total_showings_missed = Showing.objects.filter(
        showing_f, status="missed"
    ).count()
    total_applications = Application.objects.filter(app_f).count()
    total_approved = Application.objects.filter(app_f, primary_status=6).count()
    total_declined = Application.objects.filter(app_f, primary_status=7).count()
    total_leases_signed = Lease.objects.filter(lease_f).count()

    return {
        "total_prospects": total_prospects,
        "total_showings_completed": total_showings_completed,
        "total_showings_missed": total_showings_missed,
        "total_applications": total_applications,
        "total_approved": total_approved,
        "total_declined": total_declined,
        "total_leases_signed": total_leases_signed,
        "lead_to_show_rate": _pct(total_showings_completed, total_prospects),
        "show_to_app_rate": _pct(total_applications, total_showings_completed),
        "app_to_lease_rate": _pct(total_leases_signed, total_applications),
    }


# ---------------------------------------------------------------------------
# 3. Prospect Sources
# ---------------------------------------------------------------------------


@router.get("/prospect-sources", response=list[ProspectSourceSchema])
def prospect_sources(
    request,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    prospect_qs = Prospect.objects.exclude(source="")
    if date_from:
        prospect_qs = prospect_qs.filter(created_at__date__gte=date_from)
    if date_to:
        prospect_qs = prospect_qs.filter(created_at__date__lte=date_to)

    # Showings date filter applied within the annotation
    showing_filter = Q(showings__status="completed")
    if date_from:
        showing_filter &= Q(showings__created_at__date__gte=date_from)
    if date_to:
        showing_filter &= Q(showings__created_at__date__lte=date_to)

    sources = (
        prospect_qs.values("source")
        .annotate(
            prospect_count=Count("id", distinct=True),
            showing_count=Count("showings", filter=showing_filter, distinct=True),
        )
        .order_by("-prospect_count")
    )

    # Application counts joined through unit_of_interest (no direct FK)
    app_qs = Application.objects.all()
    if date_from:
        app_qs = app_qs.filter(created_at__date__gte=date_from)
    if date_to:
        app_qs = app_qs.filter(created_at__date__lte=date_to)

    source_units = (
        prospect_qs.exclude(unit_of_interest__isnull=True)
        .values_list("source", "unit_of_interest")
        .distinct()
    )
    source_unit_map = defaultdict(set)
    for src, unit_id in source_units:
        source_unit_map[src].add(unit_id)

    unit_app_counts = dict(
        app_qs.values_list("unit_id")
        .annotate(c=Count("id"))
        .values_list("unit_id", "c")
    )

    results = []
    for src in sources:
        name = src["source"]
        p_count = src["prospect_count"]
        s_count = src["showing_count"]
        a_count = sum(
            unit_app_counts.get(uid, 0) for uid in source_unit_map.get(name, set())
        )
        results.append(
            {
                "source": name,
                "prospect_count": p_count,
                "showing_count": s_count,
                "application_count": a_count,
                "lead_to_show_rate": _pct(s_count, p_count),
                "show_to_app_rate": _pct(a_count, s_count),
            }
        )

    return results


# ---------------------------------------------------------------------------
# 4. Vacancy Report
# ---------------------------------------------------------------------------


@router.get("/vacancy", response=list[VacantUnitSchema])
@paginate(LimitOffsetPagination)
def vacancy_report(
    request,
    city: Optional[str] = None,
    state: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    bedrooms: Optional[int] = None,
    property_type: Optional[str] = None,
):
    qs = (
        Unit.objects.exclude(leases__primary_lease_status=2)
        .select_related("property", "property__portfolio")
        .annotate(
            last_lease_end=Subquery(
                Lease.objects.filter(unit=OuterRef("pk"))
                .order_by("-end_date")
                .values("end_date")[:1],
                output_field=DateField(),
            ),
            prospect_count_ann=Count("prospects", distinct=True),
        )
    )

    if city:
        qs = qs.filter(Q(city__iexact=city) | Q(property__city__iexact=city))
    if state:
        qs = qs.filter(Q(state__iexact=state) | Q(property__state__iexact=state))
    if portfolio_id:
        qs = qs.filter(property__portfolio_id=portfolio_id)
    if bedrooms is not None:
        qs = qs.filter(bedrooms=bedrooms)
    if property_type:
        qs = qs.filter(property__property_type=property_type)

    return qs


# ---------------------------------------------------------------------------
# 5. Rent Analysis
# ---------------------------------------------------------------------------


@router.get("/rent-analysis", response=list[RentSegmentSchema])
def rent_analysis(
    request,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    bedrooms: Optional[int] = None,
    property_type: Optional[str] = None,
):
    qs = Unit.revenue_units()

    if city:
        qs = qs.filter(Q(city__iexact=city) | Q(property__city__iexact=city))
    if state:
        qs = qs.filter(Q(state__iexact=state) | Q(property__state__iexact=state))
    if postal_code:
        qs = qs.filter(postal_code=postal_code)
    if bedrooms is not None:
        qs = qs.filter(bedrooms=bedrooms)
    if property_type:
        qs = qs.filter(property__property_type=property_type)

    segments = (
        qs.values("postal_code", "bedrooms")
        .annotate(
            unit_count=Count("id", distinct=True),
            occupied_count=Count(
                "id",
                filter=Q(leases__primary_lease_status=2),
                distinct=True,
            ),
            avg_target_rent=Avg("target_rental_rate"),
            min_target_rent=Min("target_rental_rate"),
            max_target_rent=Max("target_rental_rate"),
            avg_active_lease_rent=Avg(
                "leases__rent_amount",
                filter=Q(leases__primary_lease_status=2),
            ),
        )
        .order_by("postal_code", "bedrooms")
    )

    # Build property names lookup per (postal_code, bedrooms) segment
    prop_names_qs = (
        qs.values("postal_code", "bedrooms")
        .annotate(prop_name=Min("property__name"))
        .values_list("postal_code", "bedrooms", "property__name")
    )
    seg_prop_names = defaultdict(set)
    for pc, br, pname in prop_names_qs:
        if pname:
            seg_prop_names[(pc, br)].add(pname)

    results = []
    for seg in segments:
        pc = seg["postal_code"] or "Unknown"
        br = seg["bedrooms"]
        br_label = f"{br}BR" if br is not None else "N/A"
        vacant = seg["unit_count"] - seg["occupied_count"]
        names = sorted(seg_prop_names.get((seg["postal_code"], br), []))
        results.append(
            {
                "segment_label": f"{pc} / {br_label}",
                "unit_count": seg["unit_count"],
                "occupied_count": seg["occupied_count"],
                "vacant_count": vacant,
                "avg_target_rent": seg["avg_target_rent"],
                "min_target_rent": seg["min_target_rent"],
                "max_target_rent": seg["max_target_rent"],
                "avg_active_lease_rent": seg["avg_active_lease_rent"],
                "vacancy_rate": _pct(vacant, seg["unit_count"]),
                "property_names": names,
            }
        )

    return results


# ---------------------------------------------------------------------------
# 6. Property Performance
# ---------------------------------------------------------------------------


@router.get("/property-performance", response=list[PropertyPerformanceSchema])
@paginate(LimitOffsetPagination)
def property_performance(
    request,
    portfolio_id: Optional[int] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    qs = Property.objects.select_related("portfolio").annotate(
        unit_count_ann=Count("units", distinct=True),
        occupied_count_ann=Count(
            "units",
            filter=Q(units__leases__primary_lease_status=2),
            distinct=True,
        ),
        # Subquery for Avg to avoid cross-join inflation
        avg_rent_ann=Subquery(
            Unit.objects.filter(property=OuterRef("pk"))
            .values("property")
            .annotate(avg=Avg("target_rental_rate"))
            .values("avg"),
            output_field=DecimalField(),
        ),
        lease_count_ann=Count(
            "leases",
            filter=Q(leases__primary_lease_status=2),
            distinct=True,
        ),
        prospect_count_ann=Count("units__prospects", distinct=True),
        showing_count_ann=Count("units__showings", distinct=True),
        application_count_ann=Count("units__applications", distinct=True),
    )

    if portfolio_id:
        qs = qs.filter(portfolio_id=portfolio_id)
    if city:
        qs = qs.filter(city__iexact=city)
    if state:
        qs = qs.filter(state__iexact=state)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)

    return qs


# ---------------------------------------------------------------------------
# 7. Lease Expirations
# ---------------------------------------------------------------------------


@router.get("/lease-expirations", response=list[LeaseExpirationMonthSchema])
def lease_expirations(
    request,
    months_ahead: int = 6,
    portfolio_id: Optional[int] = None,
):
    months_ahead = max(1, min(months_ahead, 24))

    today = date.today()
    target_month = today.month + months_ahead
    target_year = today.year + (target_month - 1) // 12
    target_month = (target_month - 1) % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    end_date_limit = date(target_year, target_month, last_day)

    leases = (
        Lease.objects.filter(
            primary_lease_status=2,
            end_date__gte=today,
            end_date__lte=end_date_limit,
        )
        .select_related("unit", "unit__property")
        .prefetch_related("tenants")
        .order_by("end_date")
    )

    if portfolio_id:
        leases = leases.filter(property__portfolio_id=portfolio_id)

    months_map = defaultdict(list)
    for lease in leases:
        month_key = lease.end_date.replace(day=1)
        months_map[month_key].append(lease)

    results = []
    for month in sorted(months_map.keys()):
        lease_list = months_map[month]
        results.append(
            {
                "month": month,
                "expiring_count": len(lease_list),
                "leases": [
                    {
                        "lease_id": l.id,
                        "unit_address": (
                            l.unit.address_line_1 if l.unit else ""
                        ),
                        "tenant_names": ", ".join(
                            t.name for t in l.tenants.all()
                        ),
                        "end_date": l.end_date,
                        "monthly_rent": l.lease_return_charge_amount,
                    }
                    for l in lease_list
                ],
            }
        )

    return results


# ---------------------------------------------------------------------------
# 8. Active Listings (Daily Pulse alert table)
# ---------------------------------------------------------------------------


@router.get("/active-listings", response=list[ActiveListingSchema])
def active_listings(request):
    """Active units with leads_per_active_day for the Daily Pulse alert table."""
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    if not latest_date:
        return []

    snapshots = DailyUnitSnapshot.objects.filter(
        snapshot_date=latest_date, status="active"
    ).select_related("unit", "unit__property", "unit__property__portfolio")

    # Batch-fetch leasing aggregates for all active units
    active_unit_ids = [s.unit_id for s in snapshots]
    leasing_agg = (
        DailyLeasingSummary.objects.filter(unit_id__in=active_unit_ids)
        .values("unit_id")
        .annotate(
            total_leads=Coalesce(Sum("leads_count"), 0),
            total_showings=Coalesce(Sum("showings_completed_count"), 0),
            total_missed=Coalesce(Sum("showings_missed_count"), 0),
            total_apps=Coalesce(Sum("applications_count"), 0),
        )
    )
    leasing_map = {row["unit_id"]: row for row in leasing_agg}

    results = []
    for snap in snapshots:
        unit = snap.unit
        prop = unit.property
        dom = snap.days_on_market or 0
        agg = leasing_map.get(unit.id, {})
        total_leads = agg.get("total_leads", 0)
        active_days = max(dom, 1)
        lpd = round(total_leads / active_days, 2)

        results.append({
            "unit_id": unit.id,
            "address": unit.address_line_1 or (prop.address_line_1 if prop else ""),
            "city": unit.city or (prop.city if prop else ""),
            "state": unit.state or (prop.state if prop else ""),
            "property_name": prop.name if prop else "",
            "portfolio_name": (
                prop.portfolio.name if prop and prop.portfolio else None
            ),
            "bedrooms": snap.bedrooms or unit.bedrooms,
            "bathrooms": snap.bathrooms or unit.full_bathrooms,
            "square_feet": snap.square_feet or unit.square_feet,
            "listed_price": snap.listed_price or unit.target_rental_rate,
            "days_on_market": dom,
            "date_listed": snap.date_listed or snap.snapshot_date,
            "total_leads": total_leads,
            "total_showings": agg.get("total_showings", 0),
            "total_missed": agg.get("total_missed", 0),
            "total_apps": agg.get("total_apps", 0),
            "leads_per_active_day": lpd,
            "is_flagged": lpd < 0.5,
        })

    results.sort(key=lambda x: x["leads_per_active_day"])
    return results


# ---------------------------------------------------------------------------
# 9. Owners with Vacancies (Owner Reports)
# ---------------------------------------------------------------------------


@router.get("/owners-with-vacancies", response=list[OwnerVacancySchema])
def owners_with_vacancies(request):
    """Owners who have at least one vacant unit across their portfolios."""
    active_lease_unit_ids = set(
        Lease.objects.filter(primary_lease_status=2).values_list("unit_id", flat=True)
    )

    owners = Owner.objects.filter(is_active=True).prefetch_related(
        "portfolios__properties__units"
    )

    results = []
    for owner in owners:
        vacant_units = []
        for portfolio in owner.portfolios.all():
            for prop in portfolio.properties.filter(is_active=True):
                for unit in prop.units.filter(is_active=True):
                    if unit.id not in active_lease_unit_ids:
                        vacant_units.append({
                            "unit_id": unit.id,
                            "address": unit.address_line_1 or prop.address_line_1,
                            "city": unit.city or prop.city,
                            "state": unit.state or prop.state,
                            "bedrooms": unit.bedrooms,
                            "target_rent": unit.target_rental_rate,
                            "portfolio_name": portfolio.name,
                            "property_name": prop.name or prop.address_line_1,
                        })

        if vacant_units:
            results.append({
                "owner_id": owner.id,
                "owner_name": owner.name,
                "owner_email": owner.email,
                "vacant_unit_count": len(vacant_units),
                "vacant_units": vacant_units,
            })

    results.sort(key=lambda x: -x["vacant_unit_count"])
    return results

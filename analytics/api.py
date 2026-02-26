import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
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
from django.db.models.functions import Coalesce
from ninja import Router
from ninja.pagination import LimitOffsetPagination, paginate

from leasing.models import Application, Lease, Prospect, Showing
from maintenance.models import WorkOrder
from market.models import DailyLeasingSummary, DailyUnitSnapshot, ListingCycle
from properties.models import Owner, Portfolio, Property, Unit

from .schemas import (
    ActiveListingSchema,
    ConcessionSchema,
    ExpirationClusterSchema,
    LeaseExpirationDetailSchema,
    LeaseExpirationMonthSchema,
    LeasingFunnelSchema,
    OwnerVacancySchema,
    PortfolioSummarySchema,
    PropertyPerformanceSchema,
    ProspectSourceSchema,
    RenewalPipelineLeaseSchema,
    RenewalPipelineSchema,
    RentGapSchema,
    RentSegmentSchema,
    RevenueLeakageSummarySchema,
    TurnCycleSchema,
    VacantUnitSchema,
)

router = Router(tags=["Analytics"])


def _pct(numerator: int, denominator: int) -> float:
    """Safe percentage: (numerator / denominator) * 100, rounded to 2 decimals."""
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _unit_address(unit) -> str:
    """Full address with unit name for disambiguation (e.g. '26 Reid St — Unit 4')."""
    addr = unit.address_line_1 or (unit.property.address_line_1 if unit.property else "")
    name = (unit.name or "").strip()
    if not name:
        return addr
    # Skip if name is a substring of address or vice versa
    if name.lower() in addr.lower() or addr.lower() in name.lower():
        return addr
    # Skip if most words in name overlap with the address (rearranged address)
    addr_words = set(addr.lower().split())
    name_words = set(name.lower().split())
    if name_words and len(name_words & addr_words) / len(name_words) > 0.5:
        return addr
    return f"{addr} — {name}"


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


# ---------------------------------------------------------------------------
# 10. Revenue Leakage Summary
# ---------------------------------------------------------------------------


@router.get("/revenue-leakage-summary", response=RevenueLeakageSummarySchema)
def revenue_leakage_summary(request):
    """Stat cards for the Revenue Intelligence page."""
    units = (
        Unit.revenue_units()
        .filter(
            leases__primary_lease_status=2,
            target_rental_rate__isnull=False,
        )
        .annotate(
            active_rent=Subquery(
                Lease.objects.filter(unit=OuterRef("pk"), primary_lease_status=2)
                .values("rent_amount")[:1],
                output_field=DecimalField(),
            )
        )
        .distinct()
    )

    total_gap = Decimal("0")
    gap_pcts = []
    below_target = 0
    total_occupied = 0

    for u in units:
        total_occupied += 1
        target = u.target_rental_rate or Decimal("0")
        rent = u.active_rent or Decimal("0")
        if target > rent and target > 0:
            gap = target - rent
            total_gap += gap
            gap_pcts.append(float(gap / target * 100))
            below_target += 1

    annual_gap = total_gap * 12
    avg_gap_pct = round(sum(gap_pcts) / len(gap_pcts), 2) if gap_pcts else 0.0

    cutoff = date.today() - timedelta(days=365)
    avg_turn = ListingCycle.objects.filter(
        leased_date__isnull=False, listed_date__gte=cutoff
    ).aggregate(avg=Avg("total_dom"))["avg"]

    avg_ratio = ListingCycle.objects.filter(
        leased_date__isnull=False, listed_date__gte=cutoff
    ).aggregate(avg=Avg("list_to_lease_ratio"))["avg"]
    avg_concession = round(float(1 - avg_ratio) * 100, 2) if avg_ratio else None

    return {
        "total_annual_rent_gap": annual_gap,
        "avg_rent_gap_pct": avg_gap_pct,
        "avg_turn_days": round(float(avg_turn), 1) if avg_turn else None,
        "avg_concession_rate": avg_concession,
        "units_below_target": below_target,
        "total_occupied_revenue_units": total_occupied,
    }


# ---------------------------------------------------------------------------
# 11. Rent Gap Analysis
# ---------------------------------------------------------------------------


@router.get("/rent-gap", response=list[RentGapSchema])
def rent_gap(request):
    """Per-unit rent gap for occupied revenue units."""
    units = (
        Unit.revenue_units()
        .filter(
            leases__primary_lease_status=2,
            target_rental_rate__isnull=False,
        )
        .select_related("property")
        .annotate(
            active_rent=Subquery(
                Lease.objects.filter(unit=OuterRef("pk"), primary_lease_status=2)
                .values("rent_amount")[:1],
                output_field=DecimalField(),
            )
        )
        .distinct()
    )

    results = []
    for u in units:
        target = u.target_rental_rate or Decimal("0")
        rent = u.active_rent or Decimal("0")
        gap = target - rent if target > rent else Decimal("0")
        gap_pct = float(gap / target * 100) if target > 0 else 0.0
        if gap <= 0:
            continue
        results.append({
            "unit_id": u.id,
            "address": _unit_address(u),
            "city": u.city or (u.property.city if u.property else ""),
            "bedrooms": u.bedrooms,
            "target_rent": target,
            "lease_rent": rent,
            "gap_amount": gap,
            "gap_pct": round(gap_pct, 1),
            "is_critical": gap_pct >= 10,
        })

    results.sort(key=lambda x: -float(x["gap_amount"]))
    return results


# ---------------------------------------------------------------------------
# 12. Turn Cycles
# ---------------------------------------------------------------------------


@router.get("/turn-cycles", response=list[TurnCycleSchema])
def turn_cycles(request, months_back: int = 12):
    """Recent turn cycles: closed lease → make ready → relist → new lease."""
    months_back = max(1, min(months_back, 36))
    cutoff = date.today() - timedelta(days=months_back * 30)

    closed_leases = (
        Lease.objects.filter(primary_lease_status=3, move_out_date__gte=cutoff)
        .select_related("unit", "unit__property")
        .order_by("-move_out_date")
    )

    results = []
    for lease in closed_leases:
        unit = lease.unit
        if not unit:
            continue

        # Find listing cycle on this unit after move_out
        cycle = (
            ListingCycle.objects.filter(
                unit=unit, listed_date__gte=lease.move_out_date
            )
            .order_by("listed_date")
            .first()
        )

        # Find next lease on this unit after move_out
        next_lease = (
            Lease.objects.filter(
                unit=unit,
                move_in_date__gt=lease.move_out_date,
                primary_lease_status__in=[1, 2],
            )
            .order_by("move_in_date")
            .first()
        )

        # Make-ready work orders
        make_ready_wos = WorkOrder.objects.filter(
            unit=unit,
            is_vacant=True,
            actual_start_date__gte=lease.move_out_date,
        )
        mr_days = None
        mr_cost = None
        if make_ready_wos.exists():
            first_start = make_ready_wos.order_by("actual_start_date").first().actual_start_date
            last_end = make_ready_wos.filter(actual_end_date__isnull=False).order_by("-actual_end_date").first()
            if first_start and last_end and last_end.actual_end_date:
                mr_days = (last_end.actual_end_date - first_start).days
            mr_cost_agg = make_ready_wos.aggregate(total=Sum("estimated_amount"))["total"]
            mr_cost = mr_cost_agg

        move_in = next_lease.move_in_date if next_lease else None
        total_turn = None
        vacancy_cost = None
        target = unit.target_rental_rate or Decimal("0")

        if move_in and lease.move_out_date:
            total_turn = (move_in - lease.move_out_date).days
            if target > 0:
                vacancy_cost = Decimal(str(total_turn)) * target / 30

        results.append({
            "unit_id": unit.id,
            "address": _unit_address(unit),
            "bedrooms": unit.bedrooms,
            "move_out_date": lease.move_out_date,
            "listed_date": cycle.listed_date if cycle else None,
            "leased_date": cycle.leased_date if cycle else None,
            "move_in_date": move_in,
            "make_ready_days": mr_days,
            "dom": cycle.total_dom if cycle else None,
            "total_turn_days": total_turn,
            "vacancy_cost": round(vacancy_cost, 2) if vacancy_cost else None,
            "make_ready_cost": mr_cost,
        })

    return results


# ---------------------------------------------------------------------------
# 13. Concession Tracker
# ---------------------------------------------------------------------------


@router.get("/concession-tracker", response=list[ConcessionSchema])
def concession_tracker(request):
    """Growing historical record of listing cycles from 2026-02-26 forward."""
    tracking_start = date(2026, 2, 26)

    cycles = (
        ListingCycle.objects.filter(listed_date__gte=tracking_start)
        .select_related("unit", "unit__property")
        .order_by("-total_drop_amount", "-listed_date")
    )

    results = []
    for c in cycles:
        unit = c.unit
        ratio = float(c.list_to_lease_ratio) if c.list_to_lease_ratio else None
        results.append({
            "unit_id": unit.id if unit else 0,
            "address": _unit_address(unit) if unit else "",
            "bedrooms": unit.bedrooms if unit else None,
            "original_price": c.original_list_price,
            "final_price": c.final_list_price,
            "signed_amount": c.signed_lease_amount,
            "list_to_lease_ratio": round(ratio, 4) if ratio else None,
            "total_drops": c.total_price_drops,
            "total_drop_amount": c.total_drop_amount or Decimal("0"),
            "is_heavy_concession": ratio is not None and ratio < 0.95,
        })

    return results


# ---------------------------------------------------------------------------
# 14. Renewal Pipeline
# ---------------------------------------------------------------------------


@router.get("/renewal-pipeline", response=RenewalPipelineSchema)
def renewal_pipeline(request, months_ahead: int = 6):
    """Full renewal pipeline: stat cards, clustering, and lease list."""
    months_ahead = max(1, min(months_ahead, 24))
    today = date.today()

    # Window for lease list
    window_end = today + timedelta(days=months_ahead * 30)

    # All active leases
    active_leases = (
        Lease.objects.filter(primary_lease_status=2)
        .select_related("unit", "unit__property")
        .prefetch_related("tenants")
    )
    total_active = active_leases.count()

    # Stat cards: expiring within 30/60/90 days
    exp_30 = active_leases.filter(end_date__gte=today, end_date__lte=today + timedelta(days=30)).count()
    exp_60 = active_leases.filter(end_date__gte=today, end_date__lte=today + timedelta(days=60)).count()
    exp_90 = active_leases.filter(end_date__gte=today, end_date__lte=today + timedelta(days=90)).count()

    # Below-market: active leases where rent is 10%+ below target
    below_market_count = 0
    for lease in active_leases:
        unit = lease.unit
        if not unit or not unit.target_rental_rate or not lease.rent_amount:
            continue
        target = unit.target_rental_rate
        if target > 0:
            gap_ratio = float((target - lease.rent_amount) / target * 100)
            if gap_ratio >= 10:
                below_market_count += 1

    # Clustering: 12 months forward
    clustering = []
    threshold = total_active * 0.15 if total_active else 0
    for i in range(12):
        m = today.month + i
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        last_day = calendar.monthrange(y, m)[1]
        month_start = date(y, m, 1)
        month_end = date(y, m, last_day)
        count = active_leases.filter(end_date__gte=month_start, end_date__lte=month_end).count()
        clustering.append({
            "month": f"{y}-{m:02d}",
            "month_label": f"{calendar.month_abbr[m]} {y}",
            "count": count,
            "is_concentrated": count > threshold,
        })

    # Lease list: expiring within window
    expiring_leases = (
        active_leases.filter(end_date__gte=today, end_date__lte=window_end)
        .order_by("end_date")
    )

    lease_list = []
    for lease in expiring_leases:
        unit = lease.unit
        target = unit.target_rental_rate if unit else None
        rent = lease.rent_amount
        gap_pct = None
        is_below = False
        if target and rent and target > 0:
            gap_pct = round(float((target - rent) / target * 100), 1)
            is_below = gap_pct >= 10

        tenant_names = ", ".join(t.name for t in lease.tenants.all())
        days_left = (lease.end_date - today).days

        lease_list.append({
            "lease_id": lease.id,
            "unit_id": unit.id if unit else 0,
            "address": _unit_address(unit) if unit else "",
            "city": (unit.city if unit else "") or (unit.property.city if unit and unit.property else ""),
            "bedrooms": unit.bedrooms if unit else None,
            "tenant_names": tenant_names,
            "lease_end": lease.end_date,
            "days_until_expiry": days_left,
            "current_rent": rent,
            "target_rent": target,
            "gap_pct": gap_pct,
            "is_below_market": is_below,
        })

    return {
        "expiring_30d": exp_30,
        "expiring_60d": exp_60,
        "expiring_90d": exp_90,
        "below_market_count": below_market_count,
        "total_active_leases": total_active,
        "clustering": clustering,
        "leases": lease_list,
    }

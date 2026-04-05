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

from django.shortcuts import get_object_or_404

from leasing.models import Application, Lease, Prospect, Showing
from maintenance.models import WorkOrder
from market.models import (
    DailyLeasingSummary,
    DailyMarketStats,
    DailySegmentStats,
    DailyUnitSnapshot,
    ListingCycle,
    PriceDrop,
    WeeklyLeasingSummary,
)
from properties.models import Owner, Portfolio, Property, Unit

from .schemas import (
    ActiveListingSchema,
    ConcessionSchema,
    ExpirationClusterSchema,
    LeaseExpirationDetailSchema,
    LeaseExpirationMonthSchema,
    LeasingFunnelSchema,
    LeasingPipelineSchema,
    OwnerReportDetailSchema,
    OwnerActiveListingSchema,
    PortfolioSummarySchema,
    PropertyPerformanceSchema,
    ProspectSourceSchema,
    RenewalPipelineLeaseSchema,
    RenewalPipelineSchema,
    RentGapSchema,
    RentSegmentSchema,
    RevenueLeakageSummarySchema,
    ScorecardSummaryRowSchema,
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
        prospect_f &= Q(source_created_at__date__gte=date_from)
        showing_f &= Q(scheduled_at__date__gte=date_from)
        app_f &= Q(source_created_at__date__gte=date_from)
        lease_f &= Q(start_date__gte=date_from)
    if date_to:
        prospect_f &= Q(source_created_at__date__lte=date_to)
        showing_f &= Q(scheduled_at__date__lte=date_to)
        app_f &= Q(source_created_at__date__lte=date_to)
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

    # Leads/Showings/Applications: prefer WeeklyLeasingSummary (incremental).
    # Fall back to DailyLeasingSummary cumulative delta for units without weekly data.
    # DailyLeasingSummary stores CUMULATIVE totals since listing start — use delta.

    # Build WeeklyLeasingSummary filter
    wls_f = Q()
    if property_id:
        wls_f &= Q(unit__property_id=property_id)
    if portfolio_id:
        wls_f &= Q(unit__property__portfolio_id=portfolio_id)

    if date_from:
        wls_qs = WeeklyLeasingSummary.objects.filter(
            wls_f, week_ending__gte=date_from,
        )
        if date_to:
            wls_qs = wls_qs.filter(week_ending__lte=date_to)

        # Sum incremental weekly counts (WLS is already incremental, not cumulative)
        wls_agg = wls_qs.aggregate(
            lead=Coalesce(Sum("leads_count"), 0),
            show=Coalesce(Sum("showings_completed_count"), 0),
            miss=Coalesce(Sum("showings_missed_count"), 0),
            app=Coalesce(Sum("applications_count"), 0),
        )
        wls_unit_ids = set(wls_qs.values_list("unit_id", flat=True))

        # Fallback: DailyLeasingSummary delta for units NOT in WeeklyLeasingSummary
        _dls_qs = DailyLeasingSummary.objects.all()
        if wls_unit_ids:
            _dls_qs = _dls_qs.exclude(unit_id__in=wls_unit_ids)

        current_qs = _dls_qs.filter(summary_date__gte=date_from)
        if date_to:
            current_qs = current_qs.filter(summary_date__lte=date_to)

        # Max = latest cumulative value within the window
        current = {}
        for row in current_qs.values("unit_id").annotate(
            lead=Max("leads_count"),
            show=Max("showings_completed_count"),
            miss=Max("showings_missed_count"),
            app=Max("applications_count"),
        ):
            current[row["unit_id"]] = row

        # Prior = latest cumulative value before the window
        prior = {}
        for row in _dls_qs.filter(summary_date__lt=date_from).values("unit_id").annotate(
            lead=Max("leads_count"),
            show=Max("showings_completed_count"),
            miss=Max("showings_missed_count"),
            app=Max("applications_count"),
        ):
            prior[row["unit_id"]] = row

        # For units with no prior record (pipeline started mid-window or listing
        # started within window), use the min value within the window as baseline.
        # This prevents historical cumulative totals from appearing as new activity.
        min_in_window = {}
        for row in current_qs.values("unit_id").annotate(
            lead=Min("leads_count"),
            show=Min("showings_completed_count"),
            miss=Min("showings_missed_count"),
            app=Min("applications_count"),
        ):
            min_in_window[row["unit_id"]] = row

        dls_lead = dls_show = dls_miss = dls_app = 0
        for uid, cur in current.items():
            prv = prior.get(uid) or min_in_window.get(uid, {})
            dls_lead += max(cur["lead"] - prv.get("lead", 0), 0)
            dls_show += max(cur["show"] - prv.get("show", 0), 0)
            dls_miss += max(cur["miss"] - prv.get("miss", 0), 0)
            dls_app += max(cur["app"] - prv.get("app", 0), 0)

        total_prospects = wls_agg["lead"] + dls_lead
        total_showings_completed = wls_agg["show"] + dls_show
        total_showings_missed = wls_agg["miss"] + dls_miss
        total_applications = wls_agg["app"] + dls_app
    else:
        # No date filter — sum all WeeklyLeasingSummary + DailyLeasingSummary fallback
        wls_agg = WeeklyLeasingSummary.objects.filter(wls_f).aggregate(
            lead=Coalesce(Sum("leads_count"), 0),
            show=Coalesce(Sum("showings_completed_count"), 0),
            miss=Coalesce(Sum("showings_missed_count"), 0),
            app=Coalesce(Sum("applications_count"), 0),
        )
        wls_unit_ids = set(
            WeeklyLeasingSummary.objects.filter(wls_f).values_list("unit_id", flat=True)
        )

        _dls_qs = DailyLeasingSummary.objects.all()
        if wls_unit_ids:
            _dls_qs = _dls_qs.exclude(unit_id__in=wls_unit_ids)

        _agg = (
            _dls_qs.values("unit_id").annotate(
                lead=Max("leads_count"),
                show=Max("showings_completed_count"),
                miss=Max("showings_missed_count"),
                app=Max("applications_count"),
            ).aggregate(
                total_lead=Coalesce(Sum("lead"), 0),
                total_show=Coalesce(Sum("show"), 0),
                total_miss=Coalesce(Sum("miss"), 0),
                total_app=Coalesce(Sum("app"), 0),
            )
        )
        total_prospects = wls_agg["lead"] + _agg["total_lead"]
        total_showings_completed = wls_agg["show"] + _agg["total_show"]
        total_showings_missed = wls_agg["miss"] + _agg["total_miss"]
        total_applications = wls_agg["app"] + _agg["total_app"]

    total_approved = 0
    total_declined = 0
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
        prospect_qs = prospect_qs.filter(source_created_at__date__gte=date_from)
    if date_to:
        prospect_qs = prospect_qs.filter(source_created_at__date__lte=date_to)

    # Showings date filter applied within the annotation
    showing_filter = Q(showings__status="completed")
    if date_from:
        showing_filter &= Q(showings__scheduled_at__date__gte=date_from)
    if date_to:
        showing_filter &= Q(showings__scheduled_at__date__lte=date_to)

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
        app_qs = app_qs.filter(source_created_at__date__gte=date_from)
    if date_to:
        app_qs = app_qs.filter(source_created_at__date__lte=date_to)

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

    # Batch-fetch leasing aggregates from DailyLeasingSummary.
    # DLS is cumulative since listing start — Max() gives the latest total.
    active_unit_ids = [s.unit_id for s in snapshots]

    summary_by_unit = {}
    for row in DailyLeasingSummary.objects.filter(
        unit_id__in=active_unit_ids
    ).values("unit_id").annotate(
        total_leads=Coalesce(Max("leads_count"), 0),
        total_showings=Coalesce(Max("showings_completed_count"), 0),
        total_missed=Coalesce(Max("showings_missed_count"), 0),
        total_apps=Coalesce(Max("applications_count"), 0),
    ):
        summary_by_unit[row["unit_id"]] = row

    # Latest price drop per active unit
    last_drop_map = {}
    for pd in (
        PriceDrop.objects.filter(unit_id__in=active_unit_ids)
        .order_by("unit_id", "-detected_date")
        .distinct("unit_id")
    ):
        last_drop_map[pd.unit_id] = pd

    # Leads since last price drop: delta between today's DLS leads_count
    # and the DLS leads_count recorded just before the drop date.
    leads_since_drop_map = {}
    for uid, pd in last_drop_map.items():
        current_leads = summary_by_unit.get(uid, {}).get("total_leads", 0)
        prior = (
            DailyLeasingSummary.objects
            .filter(unit_id=uid, summary_date__lt=pd.detected_date)
            .aggregate(n=Coalesce(Max("leads_count"), 0))
        )["n"]
        leads_since_drop_map[uid] = max(current_leads - prior, 0)

    leasing_map = {}
    for uid in active_unit_ids:
        s = summary_by_unit.get(uid, {})
        leasing_map[uid] = {
            "total_leads": s.get("total_leads", 0),
            "total_showings": s.get("total_showings", 0),
            "total_missed": s.get("total_missed", 0),
            "total_apps": s.get("total_apps", 0),
        }

    today = date.today()
    results = []
    for snap in snapshots:
        unit = snap.unit
        prop = unit.property
        dom = snap.days_on_market or 0
        agg = leasing_map.get(unit.id, {})
        total_leads = agg.get("total_leads", 0)
        active_days = max(dom, 1)
        lpd = round(total_leads / active_days, 2)

        pd_row = last_drop_map.get(unit.id)
        if pd_row:
            days_since_drop = max((today - pd_row.detected_date).days, 1)
            leads_since = leads_since_drop_map.get(unit.id, 0)
            lpd_since_drop = round(leads_since / days_since_drop, 2)
        else:
            days_since_drop = None
            leads_since = None
            lpd_since_drop = None

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
            "last_drop_date": pd_row.detected_date if pd_row else None,
            "last_drop_amount": pd_row.drop_amount if pd_row else None,
            "last_drop_pct": pd_row.drop_percent if pd_row else None,
            "leads_since_drop": leads_since,
            "days_since_drop": days_since_drop,
            "leads_per_day_since_drop": lpd_since_drop,
        })

    results.sort(key=lambda x: x["leads_per_active_day"])
    return results


# ---------------------------------------------------------------------------
# 9. Owners with Active Listings (Owner Reports)
# ---------------------------------------------------------------------------


@router.get("/owners-with-active-listings", response=list[OwnerActiveListingSchema])
def owners_with_active_listings(request):
    """Owners who have at least one actively listed unit (DailyUnitSnapshot status=active)."""
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    if not latest_date:
        return []

    # Build a map of unit_id → snapshot for active listings on latest date
    active_snapshots = DailyUnitSnapshot.objects.filter(
        snapshot_date=latest_date, status="active"
    ).select_related("unit")
    snapshot_by_unit = {snap.unit_id: snap for snap in active_snapshots}
    active_unit_ids = set(snapshot_by_unit.keys())

    if not active_unit_ids:
        return []

    owners = Owner.objects.filter(is_active=True).prefetch_related(
        "portfolios__properties__units"
    )

    results = []
    for owner in owners:
        listings = []
        for portfolio in owner.portfolios.all():
            for prop in portfolio.properties.filter(is_active=True):
                for unit in prop.units.filter(is_active=True):
                    if unit.id in active_unit_ids:
                        snap = snapshot_by_unit[unit.id]
                        listings.append({
                            "unit_id": unit.id,
                            "address": unit.address_line_1 or prop.address_line_1,
                            "city": unit.city or prop.city,
                            "state": unit.state or prop.state,
                            "bedrooms": unit.bedrooms,
                            "target_rent": unit.target_rental_rate,
                            "listed_price": snap.listed_price,
                            "days_on_market": snap.days_on_market,
                            "portfolio_name": portfolio.name,
                            "property_name": prop.name or prop.address_line_1,
                        })

        if listings:
            results.append({
                "owner_id": owner.id,
                "owner_name": owner.name,
                "owner_email": owner.email,
                "active_listing_count": len(listings),
                "active_listings": listings,
            })

    results.sort(key=lambda x: -x["active_listing_count"])
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


# ---------------------------------------------------------------------------
# 15. Leasing Pipeline
# ---------------------------------------------------------------------------


@router.get("/leasing-pipeline", response=LeasingPipelineSchema)
def leasing_pipeline(request):
    """Aggregated leasing pipeline stats: funnel, screening, scorecards."""
    from screening.models import ApplicantScorecard, ScreeningApplication

    today = date.today()
    cutoff_30d = today - timedelta(days=30)

    # Funnel (30d): reuse the accurate hybrid logic from leasing_funnel
    # (WeeklyLeasingSummary primary + DailyLeasingSummary delta fallback)
    funnel = leasing_funnel(request, date_from=cutoff_30d)
    prospects_30d = funnel["total_prospects"]
    showings_30d = funnel["total_showings_completed"]
    apps_30d = funnel["total_applications"]
    approved_30d = Application.objects.filter(
        primary_status=6,  # Approved
        source_created_at__date__gte=cutoff_30d,
    ).count()
    leases_30d = funnel["total_leases_signed"]

    # Screening status counts (30d)
    screening_pending = ScreeningApplication.objects.filter(
        status="pending", created_at__date__gte=cutoff_30d
    ).count()
    screening_in_progress = ScreeningApplication.objects.filter(
        status="in_progress", created_at__date__gte=cutoff_30d
    ).count()
    screening_completed = ScreeningApplication.objects.filter(
        status="completed", created_at__date__gte=cutoff_30d
    ).count()

    # Scorecard aggregates (30d)
    scorecards = ApplicantScorecard.objects.filter(created_at__date__gte=cutoff_30d)
    scorecards_count = scorecards.count()
    avg_score_val = scorecards.aggregate(avg=Avg("total_score"))["avg"]

    auto_deny = scorecards.filter(recommendation="auto_deny").count()
    platinum = scorecards.filter(recommendation="platinum").count()
    strong = scorecards.filter(recommendation="strong").count()
    borderline = scorecards.filter(recommendation="borderline").count()
    high_risk = scorecards.filter(recommendation="high_risk").count()
    reject = scorecards.filter(recommendation="reject").count()

    return {
        "total_prospects_30d": prospects_30d,
        "total_showings_30d": showings_30d,
        "total_applications_30d": apps_30d,
        "total_approved_30d": approved_30d,
        "screening_pending": screening_pending,
        "screening_in_progress": screening_in_progress,
        "screening_completed": screening_completed,
        "scorecards_count": scorecards_count,
        "avg_score": round(avg_score_val, 1) if avg_score_val else None,
        "auto_deny_count": auto_deny,
        "platinum_count": platinum,
        "strong_count": strong,
        "borderline_count": borderline,
        "high_risk_count": high_risk,
        "reject_count": reject,
        "lead_to_app_rate": _pct(apps_30d, prospects_30d),
        "app_to_lease_rate": _pct(leases_30d, apps_30d),
    }


@router.get("/leasing-pipeline/scorecards", response=list[ScorecardSummaryRowSchema])
def leasing_pipeline_scorecards(request):
    """Recent scorecards table data for the leasing pipeline page."""
    from screening.models import ApplicantScorecard

    scorecards = (
        ApplicantScorecard.objects.select_related(
            "screening_application", "screening_application__unit"
        )
        .order_by("-updated_at")[:50]
    )

    results = []
    for sc in scorecards:
        app = sc.screening_application
        unit = app.unit
        results.append({
            "id": sc.id,
            "applicant_name": app.applicant_name,
            "applicant_email": app.applicant_email,
            "unit_address": unit.address_line_1 if unit else None,
            "total_score": sc.total_score,
            "recommendation": sc.recommendation,
            "screening_status": app.status,
            "reviewed_by": sc.reviewed_by,
            "updated_at": sc.updated_at.isoformat(),
        })

    return results


# ---------------------------------------------------------------------------
# 16. Owner Report Detail
# ---------------------------------------------------------------------------


def _week_monday(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _summarize_feedback(showings_list) -> list[dict]:
    """Convert Showing objects with feedback JSONField into readable summaries."""
    results = []
    for s in showings_list:
        fb = s.feedback or {}
        if not fb:
            continue
        parts = []
        # Handle common feedback structures
        if isinstance(fb, str):
            parts.append(fb)
        elif isinstance(fb, dict):
            for key in ("comments", "overall_comments", "notes", "summary"):
                if fb.get(key):
                    parts.append(str(fb[key]))
            if fb.get("liked"):
                parts.append(f"Liked: {fb['liked']}")
            if fb.get("disliked"):
                parts.append(f"Concerns: {fb['disliked']}")
            if fb.get("would_apply_if"):
                parts.append(f"Would apply if: {fb['would_apply_if']}")
            # Fallback: if no known keys matched, dump values
            if not parts:
                vals = [str(v) for v in fb.values() if v]
                if vals:
                    parts.append("; ".join(vals))
        if parts:
            prospect_name = s.prospect.name if s.prospect else ""
            results.append({
                "date": s.completed_at.strftime("%Y-%m-%d") if s.completed_at else None,
                "prospect_name": prospect_name,
                "feedback_summary": " ".join(parts),
            })
    return results


def _zillow_url(address, city, state, zip_code):
    """Construct a Zillow search URL from address components."""
    import re
    raw = f"{address} {city} {state} {zip_code}".strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-")
    return f"https://www.zillow.com/homes/{slug}_rb/" if slug else ""


@router.get(
    "/owner-report-detail/{owner_id}", response=OwnerReportDetailSchema
)
def owner_report_detail(
    request,
    owner_id: int,
    week_date: Optional[date] = None,
):
    """Rich per-unit data for an owner's actively listed units. Powers the owner report email."""
    from dashboard.models import PropertyWeeklyNote

    owner = get_object_or_404(Owner.objects.prefetch_related("portfolios__properties__units"), pk=owner_id)

    # Determine week boundaries
    today = date.today()
    if not week_date:
        week_date = _week_monday(today)
    week_start = week_date
    week_end = week_date + timedelta(days=6)
    prev_start = week_date - timedelta(days=7)
    prev_end = week_date - timedelta(days=1)

    # Find actively listed units for this owner
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    active_snapshot_unit_ids = set()
    if latest_date:
        active_snapshot_unit_ids = set(
            DailyUnitSnapshot.objects.filter(
                snapshot_date=latest_date, status="active"
            ).values_list("unit_id", flat=True)
        )

    listed_units = []
    unit_portfolio = {}  # unit.id → portfolio name
    for portfolio in owner.portfolios.all():
        for prop in portfolio.properties.filter(is_active=True):
            for unit in prop.units.filter(is_active=True):
                if unit.id in active_snapshot_unit_ids:
                    listed_units.append(unit)
                    unit_portfolio[unit.id] = portfolio.name or ""

    if not listed_units:
        return {
            "owner_id": owner.id,
            "owner_name": owner.name,
            "owner_email": owner.email,
            "portfolio_medians": {"median_dom": None, "median_list_price": None, "active_unit_count": 0},
            "units": [],
        }

    unit_ids = [u.id for u in listed_units]

    # ── Batch queries ────────────────────────────────────────────────────
    # Leads: count Prospect records using source_created_at (accurate dates).
    # Showings/Apps: prefer WeeklyLeasingSummary (incremental, from imports
    #   or corrected aggregator). Fall back to DailyLeasingSummary cumulative
    #   delta for units without weekly data.

    def _count_leads(unit_ids, date_gte=None, date_lte=None):
        """Count Prospect records per unit using source_created_at."""
        qs = Prospect.objects.filter(unit_of_interest_id__in=unit_ids)
        if date_gte:
            qs = qs.filter(source_created_at__date__gte=date_gte)
        if date_lte:
            qs = qs.filter(source_created_at__date__lte=date_lte)
        return dict(
            qs.values_list("unit_of_interest_id")
            .annotate(n=Count("id"))
            .values_list("unit_of_interest_id", "n")
        )

    def _weekly_leasing(unit_ids, date_gte, date_lte):
        """Get incremental showings/apps from WeeklyLeasingSummary for the period."""
        qs = WeeklyLeasingSummary.objects.filter(
            unit_id__in=unit_ids,
            week_ending__gte=date_gte,
            week_ending__lte=date_lte,
        )
        result = {}
        for row in qs.values("unit_id").annotate(
            showings=Coalesce(Sum("showings_completed_count"), 0),
            missed=Coalesce(Sum("showings_missed_count"), 0),
            apps=Coalesce(Sum("applications_count"), 0),
        ):
            result[row["unit_id"]] = row
        return result

    def _dls_delta(unit_ids, date_gte, date_lte):
        """Fallback: DailyLeasingSummary cumulative delta for a period."""
        cur_qs = DailyLeasingSummary.objects.filter(
            unit_id__in=unit_ids, summary_date__gte=date_gte, summary_date__lte=date_lte,
        )
        current = {}
        for row in cur_qs.values("unit_id").annotate(
            showings=Coalesce(Max("showings_completed_count"), 0),
            missed=Coalesce(Max("showings_missed_count"), 0),
            apps=Coalesce(Max("applications_count"), 0),
        ):
            current[row["unit_id"]] = row

        prv_qs = DailyLeasingSummary.objects.filter(
            unit_id__in=unit_ids, summary_date__lt=date_gte,
        )
        prior = {}
        for row in prv_qs.values("unit_id").annotate(
            showings=Coalesce(Max("showings_completed_count"), 0),
            missed=Coalesce(Max("showings_missed_count"), 0),
            apps=Coalesce(Max("applications_count"), 0),
        ):
            prior[row["unit_id"]] = row

        result = {}
        for uid, cur in current.items():
            prv = prior.get(uid, {})
            result[uid] = {
                "unit_id": uid,
                "showings": max(cur["showings"] - prv.get("showings", 0), 0),
                "missed": max(cur["missed"] - prv.get("missed", 0), 0),
                "apps": max(cur["apps"] - prv.get("apps", 0), 0),
            }
        return result

    def _leasing_for_period(unit_ids, date_gte, date_lte):
        """Get showings/apps per unit: WeeklyLeasingSummary first, DLS delta fallback."""
        wls = _weekly_leasing(unit_ids, date_gte, date_lte)
        # Units not covered by weekly data
        uncovered = [uid for uid in unit_ids if uid not in wls]
        dls = _dls_delta(uncovered, date_gte, date_lte) if uncovered else {}
        merged = {}
        merged.update(wls)
        merged.update(dls)
        return merged

    def _alltime_leasing(unit_ids):
        """All-time showings/apps: sum all WeeklyLeasingSummary + DLS fallback."""
        wls = {}
        for row in WeeklyLeasingSummary.objects.filter(unit_id__in=unit_ids).values("unit_id").annotate(
            showings=Coalesce(Sum("showings_completed_count"), 0),
            missed=Coalesce(Sum("showings_missed_count"), 0),
            apps=Coalesce(Sum("applications_count"), 0),
        ):
            wls[row["unit_id"]] = row

        # Fallback for units without weekly data
        uncovered = [uid for uid in unit_ids if uid not in wls]
        dls = {}
        if uncovered:
            for row in DailyLeasingSummary.objects.filter(unit_id__in=uncovered).values("unit_id").annotate(
                showings=Coalesce(Max("showings_completed_count"), 0),
                missed=Coalesce(Max("showings_missed_count"), 0),
                apps=Coalesce(Max("applications_count"), 0),
            ):
                dls[row["unit_id"]] = row

        merged = {}
        merged.update(wls)
        merged.update(dls)
        return merged

    # This week — leads from Prospect, showings/apps from WeeklyLeasingSummary/DLS
    wk_leads = _count_leads(unit_ids, week_start, week_end)
    wk_leasing = _leasing_for_period(unit_ids, week_start, week_end)

    # Previous week
    pv_leads = _count_leads(unit_ids, prev_start, prev_end)
    pv_leasing = _leasing_for_period(unit_ids, prev_start, prev_end)

    # All-time
    at_leads = _count_leads(unit_ids)
    at_leasing = _alltime_leasing(unit_ids)

    # Build maps
    weekly_map = {}
    prev_map = {}
    alltime_map = {}
    for uid in unit_ids:
        wk_l = wk_leasing.get(uid, {})
        pv_l = pv_leasing.get(uid, {})
        at_l = at_leasing.get(uid, {})

        weekly_map[uid] = {
            "leads": wk_leads.get(uid, 0),
            "showings": wk_l.get("showings", 0),
            "missed": wk_l.get("missed", 0),
            "apps": wk_l.get("apps", 0),
        }
        prev_map[uid] = {
            "leads": pv_leads.get(uid, 0),
            "showings": pv_l.get("showings", 0),
            "missed": pv_l.get("missed", 0),
            "apps": pv_l.get("apps", 0),
        }
        alltime_map[uid] = {
            "leads": at_leads.get(uid, 0),
            "showings": at_l.get("showings", 0),
            "missed": at_l.get("missed", 0),
            "apps": at_l.get("apps", 0),
        }

    # Latest snapshot per unit
    snapshot_map = {}
    for uid in unit_ids:
        snap = DailyUnitSnapshot.objects.filter(unit_id=uid).order_by("-snapshot_date").first()
        if snap:
            snapshot_map[uid] = snap

    # Active days per unit (for leads-per-active-day)
    active_days_map = dict(
        DailyUnitSnapshot.objects.filter(unit_id__in=unit_ids, status="active")
        .values("unit_id")
        .annotate(days=Count("id"))
        .values_list("unit_id", "days")
    )

    # Market context: latest segment stats per zip code
    zip_codes = set()
    for u in listed_units:
        zc = u.postal_code or (u.property.postal_code if u.property else "")
        if zc:
            zip_codes.add(zc)

    zip_context_map = {}
    if zip_codes:
        latest_seg_date = (
            DailySegmentStats.objects.filter(segment_type="zip_code")
            .order_by("-snapshot_date")
            .values_list("snapshot_date", flat=True)
            .first()
        )
        if latest_seg_date:
            for seg in DailySegmentStats.objects.filter(
                segment_type="zip_code",
                segment_value__in=zip_codes,
                snapshot_date=latest_seg_date,
            ):
                zip_context_map[seg.segment_value] = {
                    "zip_avg_list_price": seg.average_price,
                    "zip_avg_dom": seg.average_dom,
                    "zip_active_count": seg.active_unit_count,
                }

    # Showing feedback (completed this week)
    feedback_showings = (
        Showing.objects.filter(
            unit_id__in=unit_ids,
            status="completed",
            completed_at__date__gte=week_start,
            completed_at__date__lte=week_end,
        )
        .select_related("prospect")
        .order_by("completed_at")
    )
    feedback_by_unit = defaultdict(list)
    for s in feedback_showings:
        if s.feedback:
            feedback_by_unit[s.unit_id].append(s)

    # Upcoming showings
    from django.utils import timezone as tz
    now = tz.now()
    upcoming_showings = (
        Showing.objects.filter(
            unit_id__in=unit_ids,
            status__in=["scheduled", "confirmed"],
            scheduled_at__gte=now,
        )
        .select_related("prospect")
        .order_by("scheduled_at")
    )
    upcoming_by_unit = defaultdict(list)
    for s in upcoming_showings:
        upcoming_by_unit[s.unit_id].append(s)

    # Portfolio medians
    latest_market = DailyMarketStats.objects.order_by("-snapshot_date").first()
    portfolio_medians = {
        "median_dom": latest_market.median_dom if latest_market else None,
        "median_list_price": latest_market.median_price if latest_market else None,
        "active_unit_count": latest_market.active_unit_count if latest_market else 0,
    }

    # Property notes
    notes_map = {}
    for note in PropertyWeeklyNote.objects.filter(unit_id__in=unit_ids, week_date=week_date):
        notes_map[note.unit_id] = note

    # Previous notes (last 4 weeks, for context)
    prev_notes_map = defaultdict(list)
    four_weeks_ago = week_date - timedelta(days=28)
    for note in PropertyWeeklyNote.objects.filter(
        unit_id__in=unit_ids,
        week_date__gte=four_weeks_ago,
        week_date__lt=week_date,
    ).order_by("-week_date"):
        prev_notes_map[note.unit_id].append({
            "week_date": note.week_date.isoformat(),
            "author": note.author,
            "note_text": note.note_text,
        })

    # Price history: PriceDrop records for last 180 days
    price_drop_map = defaultdict(list)
    cutoff_180 = date.today() - timedelta(days=180)
    for pd in PriceDrop.objects.filter(
        unit_id__in=unit_ids,
        detected_date__gte=cutoff_180,
    ).order_by("detected_date"):
        price_drop_map[pd.unit_id].append({
            "date": pd.detected_date.isoformat(),
            "previous_price": pd.previous_price,
            "new_price": pd.new_price,
            "drop_amount": pd.drop_amount,
            "drop_percent": pd.drop_percent,
        })

    # ── Build per-unit results ───────────────────────────────────────────

    unit_results = []
    for unit in listed_units:
        prop = unit.property
        zc = unit.postal_code or (prop.postal_code if prop else "")
        address = unit.address_line_1 or (prop.address_line_1 if prop else "")
        city = unit.city or (prop.city if prop else "")
        state = unit.state or (prop.state if prop else "")

        w = weekly_map.get(unit.id, {})
        p = prev_map.get(unit.id, {})
        a = alltime_map.get(unit.id, {})
        snap = snapshot_map.get(unit.id)

        # Leads per active day
        total_leads = a.get("leads", 0)
        active_days = active_days_map.get(unit.id, 0)
        dom = snap.days_on_market if snap else 0
        divisor = max(active_days, dom or 0, 1)
        lpd = round(total_leads / divisor, 2)

        # Market context for this zip
        mkt = zip_context_map.get(zc, {})

        # Price recommendation
        price_rec = {"recommended": False, "current_lpd": lpd, "threshold": 0.5, "message": ""}
        current_price = snap.listed_price if snap else unit.target_rental_rate
        if lpd < 0.5 and current_price:
            suggested = max(Decimal(str(float(current_price) - 50)), Decimal("0"))
            mkt_avg = mkt.get("zip_avg_list_price")
            msg = (
                f"Based on current activity ({lpd} leads/day vs 0.5 threshold), "
                f"we recommend a $50 adjustment."
            )
            if mkt_avg:
                msg += f" Similar units in {zc} are listing at ${mkt_avg:,.0f}."
            price_rec = {
                "recommended": True,
                "current_lpd": lpd,
                "threshold": 0.5,
                "suggested_price": suggested,
                "market_avg": mkt_avg,
                "message": msg,
            }

        # Showing feedback — prefer actual Showing records, fall back to weekly note text
        fb_list = _summarize_feedback(feedback_by_unit.get(unit.id, []))
        if not fb_list:
            # Fall back to most recent PropertyWeeklyNote for this unit as feedback proxy
            fb_note = notes_map.get(unit.id)
            if not fb_note:
                pn_list = prev_notes_map.get(unit.id, [])
                if pn_list:
                    fb_note_data = pn_list[0]  # most recent prev note
                    fb_list = [{
                        "date": fb_note_data["week_date"],
                        "prospect_name": "",
                        "feedback_summary": fb_note_data["note_text"],
                    }]
            if fb_note and not fb_list:
                fb_list = [{
                    "date": week_date.isoformat(),
                    "prospect_name": "",
                    "feedback_summary": fb_note.note_text,
                }]

        # Upcoming showings
        upcoming = []
        for s in upcoming_by_unit.get(unit.id, []):
            upcoming.append({
                "date": s.scheduled_at.strftime("%Y-%m-%d") if s.scheduled_at else "",
                "time": s.scheduled_at.strftime("%-I:%M %p") if s.scheduled_at else "",
                "prospect_name": s.prospect.name if s.prospect else "",
            })

        # Current week note
        note = notes_map.get(unit.id)

        unit_results.append({
            "unit_id": unit.id,
            "address": address,
            "city": city,
            "state": state,
            "zip_code": zc,
            "portfolio_name": unit_portfolio.get(unit.id, ""),
            "bedrooms": unit.bedrooms,
            "bathrooms": snap.bathrooms if snap else unit.full_bathrooms,
            "square_feet": snap.square_feet if snap else unit.square_feet,
            "target_rent": unit.target_rental_rate,
            "current_list_price": snap.listed_price if snap else None,
            "days_on_market": snap.days_on_market if snap else None,
            "date_listed": snap.date_listed if snap else None,
            "weekly": {
                "leads": w.get("leads", 0),
                "showings": w.get("showings", 0),
                "missed": w.get("missed", 0),
                "apps": w.get("apps", 0),
            },
            "prev_weekly": {
                "leads": p.get("leads", 0),
                "showings": p.get("showings", 0),
                "missed": p.get("missed", 0),
                "apps": p.get("apps", 0),
            },
            "all_time": {
                "leads": a.get("leads", 0),
                "showings": a.get("showings", 0),
                "missed": a.get("missed", 0),
                "apps": a.get("apps", 0),
            },
            "leads_per_active_day": lpd,
            "market_context": {
                "zip_avg_list_price": mkt.get("zip_avg_list_price"),
                "zip_avg_dom": mkt.get("zip_avg_dom"),
                "zip_active_count": mkt.get("zip_active_count", 0),
            },
            "showing_feedback": fb_list,
            "upcoming_showings": upcoming,
            "price_recommendation": price_rec,
            "price_history": price_drop_map.get(unit.id, []),
            "zillow_url": _zillow_url(address, city, state, zc),
            "property_note": note.note_text if note else "",
            "property_note_author": note.author if note else "",
            "prev_notes": prev_notes_map.get(unit.id, []),
        })

    return {
        "owner_id": owner.id,
        "owner_name": owner.name,
        "owner_email": owner.email,
        "portfolio_medians": portfolio_medians,
        "units": unit_results,
    }


# ---------------------------------------------------------------------------
# Backfill monthly aggregations
# ---------------------------------------------------------------------------


@router.post("/backfill-monthly", response=dict)
def backfill_monthly(request, start_month: str, end_month: str):
    """
    Re-run MonthlyMarketReport + MonthlySegmentStats for each month in range.
    start_month / end_month format: YYYY-MM
    """
    from market.services import MonthlyMarketReportAggregator, MonthlySegmentStatsAggregator

    def _parse(s):
        parts = s.split("-")
        return int(parts[0]), int(parts[1])

    start_year, start_mo = _parse(start_month)
    end_year, end_mo = _parse(end_month)

    results = []
    year, month = start_year, start_mo
    while (year, month) <= (end_year, end_mo):
        try:
            r1 = MonthlyMarketReportAggregator().run(year, month)
            r2 = MonthlySegmentStatsAggregator().run(year, month)
            results.append({
                "month": f"{year}-{month:02d}",
                "report": r1,
                "segments": r2,
            })
        except Exception as exc:
            results.append({"month": f"{year}-{month:02d}", "error": str(exc)})
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1

    return {"months_processed": len(results), "results": results}

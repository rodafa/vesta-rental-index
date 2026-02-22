from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import LimitOffsetPagination, paginate

from market.models import (
    DailyLeasingSummary,
    DailyMarketStats,
    DailySegmentStats,
    DailyUnitSnapshot,
    ListingCycle,
    MonthlyMarketReport,
    MonthlySegmentStats,
    PriceDrop,
    WeeklyLeasingSummary,
)
from market.schemas import (
    DailyLeasingFilterSchema,
    DailyLeasingSummarySchema,
    DailyMarketStatsSchema,
    DailyStatsFilterSchema,
    DailySegmentStatsSchema,
    DailyUnitSnapshotSchema,
    ListingCycleFilterSchema,
    ListingCycleSchema,
    MonthlyMarketReportSchema,
    MonthlyReportFilterSchema,
    MonthlySegmentFilterSchema,
    MonthlySegmentStatsSchema,
    PriceDropFilterSchema,
    PriceDropSchema,
    SegmentStatsFilterSchema,
    SnapshotFilterSchema,
    WeeklyLeasingFilterSchema,
    WeeklyLeasingSummarySchema,
)

router = Router(tags=["Market"])


# --- DailyUnitSnapshot ---


@router.get("/snapshots", response=list[DailyUnitSnapshotSchema])
@paginate(LimitOffsetPagination)
def list_snapshots(request, filters: Query[SnapshotFilterSchema]):
    qs = DailyUnitSnapshot.objects.all()
    return filters.filter(qs)


@router.get("/snapshots/{snapshot_id}", response=DailyUnitSnapshotSchema)
def get_snapshot(request, snapshot_id: int):
    return get_object_or_404(DailyUnitSnapshot, id=snapshot_id)


# --- DailyMarketStats ---


@router.get("/daily-stats", response=list[DailyMarketStatsSchema])
@paginate(LimitOffsetPagination)
def list_daily_stats(request, filters: Query[DailyStatsFilterSchema]):
    qs = DailyMarketStats.objects.all()
    return filters.filter(qs)


@router.get("/daily-stats/{stat_id}", response=DailyMarketStatsSchema)
def get_daily_stat(request, stat_id: int):
    return get_object_or_404(DailyMarketStats, id=stat_id)


# --- DailyLeasingSummary ---


@router.get("/daily-leasing", response=list[DailyLeasingSummarySchema])
@paginate(LimitOffsetPagination)
def list_daily_leasing(request, filters: Query[DailyLeasingFilterSchema]):
    qs = DailyLeasingSummary.objects.all()
    return filters.filter(qs)


@router.get("/daily-leasing/{summary_id}", response=DailyLeasingSummarySchema)
def get_daily_leasing(request, summary_id: int):
    return get_object_or_404(DailyLeasingSummary, id=summary_id)


# --- WeeklyLeasingSummary ---


@router.get("/weekly-leasing", response=list[WeeklyLeasingSummarySchema])
@paginate(LimitOffsetPagination)
def list_weekly_leasing(request, filters: Query[WeeklyLeasingFilterSchema]):
    qs = WeeklyLeasingSummary.objects.all()
    return filters.filter(qs)


@router.get("/weekly-leasing/{summary_id}", response=WeeklyLeasingSummarySchema)
def get_weekly_leasing(request, summary_id: int):
    return get_object_or_404(WeeklyLeasingSummary, id=summary_id)


# --- MonthlyMarketReport ---


@router.get("/monthly-reports", response=list[MonthlyMarketReportSchema])
@paginate(LimitOffsetPagination)
def list_monthly_reports(request, filters: Query[MonthlyReportFilterSchema]):
    qs = MonthlyMarketReport.objects.all()
    return filters.filter(qs)


@router.get("/monthly-reports/{report_id}", response=MonthlyMarketReportSchema)
def get_monthly_report(request, report_id: int):
    return get_object_or_404(MonthlyMarketReport, id=report_id)


# --- DailySegmentStats ---


@router.get("/segment-stats", response=list[DailySegmentStatsSchema])
@paginate(LimitOffsetPagination)
def list_segment_stats(request, filters: Query[SegmentStatsFilterSchema]):
    qs = DailySegmentStats.objects.all()
    return filters.filter(qs)


@router.get("/segment-stats/{stat_id}", response=DailySegmentStatsSchema)
def get_segment_stat(request, stat_id: int):
    return get_object_or_404(DailySegmentStats, id=stat_id)


# --- PriceDrop ---


@router.get("/price-drops", response=list[PriceDropSchema])
@paginate(LimitOffsetPagination)
def list_price_drops(request, filters: Query[PriceDropFilterSchema]):
    qs = PriceDrop.objects.all()
    return filters.filter(qs)


@router.get("/price-drops/{drop_id}", response=PriceDropSchema)
def get_price_drop(request, drop_id: int):
    return get_object_or_404(PriceDrop, id=drop_id)


# --- MonthlySegmentStats ---


@router.get("/monthly-segments", response=list[MonthlySegmentStatsSchema])
@paginate(LimitOffsetPagination)
def list_monthly_segments(request, filters: Query[MonthlySegmentFilterSchema]):
    qs = MonthlySegmentStats.objects.all()
    return filters.filter(qs)


@router.get("/monthly-segments/{stat_id}", response=MonthlySegmentStatsSchema)
def get_monthly_segment(request, stat_id: int):
    return get_object_or_404(MonthlySegmentStats, id=stat_id)


# --- ListingCycle ---


@router.get("/listing-cycles", response=list[ListingCycleSchema])
@paginate(LimitOffsetPagination)
def list_listing_cycles(request, filters: Query[ListingCycleFilterSchema]):
    qs = ListingCycle.objects.all()
    return filters.filter(qs)


@router.get("/listing-cycles/{cycle_id}", response=ListingCycleSchema)
def get_listing_cycle(request, cycle_id: int):
    return get_object_or_404(ListingCycle, id=cycle_id)

from django.contrib import admin

from .models import (
    DailyLeasingSummary,
    DailyMarketStats,
    DailySegmentStats,
    DailyUnitSnapshot,
    ListingCycle,
    MonthlyMarketReport,
    PriceDrop,
    WeeklyLeasingSummary,
)


@admin.register(DailyUnitSnapshot)
class DailyUnitSnapshotAdmin(admin.ModelAdmin):
    list_display = ("unit", "snapshot_date", "status", "listed_price", "days_on_market", "bedrooms")
    list_filter = ("status", "snapshot_date")
    search_fields = ("unit__address_line_1",)
    date_hierarchy = "snapshot_date"


@admin.register(DailyMarketStats)
class DailyMarketStatsAdmin(admin.ModelAdmin):
    list_display = ("snapshot_date", "active_unit_count", "average_dom", "average_price", "average_portfolio_rent")
    date_hierarchy = "snapshot_date"


@admin.register(DailyLeasingSummary)
class DailyLeasingSummaryAdmin(admin.ModelAdmin):
    list_display = ("unit", "summary_date", "leads_count", "showings_completed_count", "applications_count")
    date_hierarchy = "summary_date"
    search_fields = ("unit__address_line_1",)


@admin.register(WeeklyLeasingSummary)
class WeeklyLeasingSummaryAdmin(admin.ModelAdmin):
    list_display = ("unit", "week_ending", "leads_count", "showings_completed_count", "lead_to_show_rate")
    date_hierarchy = "week_ending"


@admin.register(MonthlyMarketReport)
class MonthlyMarketReportAdmin(admin.ModelAdmin):
    list_display = ("report_month", "average_dom", "average_price", "total_leads", "total_showings")
    date_hierarchy = "report_month"


@admin.register(DailySegmentStats)
class DailySegmentStatsAdmin(admin.ModelAdmin):
    list_display = ("snapshot_date", "segment_type", "segment_value", "active_unit_count", "average_dom", "average_price")
    list_filter = ("segment_type",)
    search_fields = ("segment_value",)
    date_hierarchy = "snapshot_date"


@admin.register(PriceDrop)
class PriceDropAdmin(admin.ModelAdmin):
    list_display = ("unit", "detected_date", "previous_price", "new_price", "drop_amount", "drop_percent")
    date_hierarchy = "detected_date"
    search_fields = ("unit__address_line_1",)


@admin.register(ListingCycle)
class ListingCycleAdmin(admin.ModelAdmin):
    list_display = (
        "unit", "listed_date", "leased_date", "original_list_price",
        "signed_lease_amount", "total_dom", "list_to_lease_ratio",
    )
    date_hierarchy = "listed_date"
    search_fields = ("unit__address_line_1",)

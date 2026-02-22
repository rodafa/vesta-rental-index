from datetime import date
from typing import Optional

from ninja import Field, FilterSchema, ModelSchema

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


# --- DailyUnitSnapshot ---


class DailyUnitSnapshotSchema(ModelSchema):
    class Meta:
        model = DailyUnitSnapshot
        fields = [
            "id",
            "unit",
            "snapshot_date",
            "listed_price",
            "days_on_market",
            "status",
            "bedrooms",
            "bathrooms",
            "square_feet",
            "date_listed",
            "date_off_market",
            "created_at",
        ]


class SnapshotFilterSchema(FilterSchema):
    unit_id: Optional[int] = None
    status: Optional[str] = None
    date_from: Optional[date] = Field(None, q="snapshot_date__gte")
    date_to: Optional[date] = Field(None, q="snapshot_date__lte")


# --- DailyMarketStats ---


class DailyMarketStatsSchema(ModelSchema):
    class Meta:
        model = DailyMarketStats
        fields = [
            "id",
            "snapshot_date",
            "active_unit_count",
            "average_dom",
            "average_price",
            "count_30_plus_dom",
            "average_portfolio_rent",
            "created_at",
        ]


class DailyStatsFilterSchema(FilterSchema):
    date_from: Optional[date] = Field(None, q="snapshot_date__gte")
    date_to: Optional[date] = Field(None, q="snapshot_date__lte")


# --- DailyLeasingSummary ---


class DailyLeasingSummarySchema(ModelSchema):
    class Meta:
        model = DailyLeasingSummary
        fields = [
            "id",
            "summary_date",
            "unit",
            "leads_count",
            "showings_completed_count",
            "showings_missed_count",
            "applications_count",
            "property_display_name",
            "created_at",
        ]


class DailyLeasingFilterSchema(FilterSchema):
    unit_id: Optional[int] = None
    date_from: Optional[date] = Field(None, q="summary_date__gte")
    date_to: Optional[date] = Field(None, q="summary_date__lte")


# --- WeeklyLeasingSummary ---


class WeeklyLeasingSummarySchema(ModelSchema):
    class Meta:
        model = WeeklyLeasingSummary
        fields = [
            "id",
            "week_ending",
            "unit",
            "leads_count",
            "showings_completed_count",
            "showings_missed_count",
            "applications_count",
            "lead_to_show_rate",
            "show_to_app_rate",
            "property_display_name",
            "created_at",
        ]


class WeeklyLeasingFilterSchema(FilterSchema):
    unit_id: Optional[int] = None
    date_from: Optional[date] = Field(None, q="week_ending__gte")
    date_to: Optional[date] = Field(None, q="week_ending__lte")


# --- MonthlyMarketReport ---


class MonthlyMarketReportSchema(ModelSchema):
    class Meta:
        model = MonthlyMarketReport
        fields = [
            "id",
            "report_month",
            "average_dom",
            "average_price",
            "average_30_plus_dom_count",
            "total_leads",
            "total_showings",
            "total_missed_showings",
            "total_applications",
            "lead_to_show_rate",
            "show_to_app_rate",
            "created_at",
        ]


class MonthlyReportFilterSchema(FilterSchema):
    date_from: Optional[date] = Field(None, q="report_month__gte")
    date_to: Optional[date] = Field(None, q="report_month__lte")


# --- DailySegmentStats ---


class DailySegmentStatsSchema(ModelSchema):
    class Meta:
        model = DailySegmentStats
        fields = [
            "id",
            "snapshot_date",
            "segment_type",
            "segment_value",
            "active_unit_count",
            "average_dom",
            "average_price",
            "count_30_plus_dom",
            "leads_count",
            "showings_count",
            "applications_count",
            "lead_to_show_rate",
            "show_to_app_rate",
            "created_at",
        ]


class SegmentStatsFilterSchema(FilterSchema):
    segment_type: Optional[str] = None
    segment_value: Optional[str] = None
    date_from: Optional[date] = Field(None, q="snapshot_date__gte")
    date_to: Optional[date] = Field(None, q="snapshot_date__lte")


# --- PriceDrop ---


class PriceDropSchema(ModelSchema):
    class Meta:
        model = PriceDrop
        fields = [
            "id",
            "unit",
            "previous_price",
            "new_price",
            "drop_amount",
            "drop_percent",
            "detected_date",
            "created_at",
        ]


class PriceDropFilterSchema(FilterSchema):
    unit_id: Optional[int] = None
    date_from: Optional[date] = Field(None, q="detected_date__gte")
    date_to: Optional[date] = Field(None, q="detected_date__lte")


# --- ListingCycle ---


class ListingCycleSchema(ModelSchema):
    class Meta:
        model = ListingCycle
        fields = [
            "id",
            "unit",
            "listed_date",
            "leased_date",
            "lease_start_date",
            "original_list_price",
            "final_list_price",
            "signed_lease_amount",
            "total_dom",
            "total_price_drops",
            "total_drop_amount",
            "list_to_lease_ratio",
            "created_at",
            "updated_at",
        ]


# --- MonthlySegmentStats ---


class MonthlySegmentStatsSchema(ModelSchema):
    class Meta:
        model = MonthlySegmentStats
        fields = [
            "id",
            "month",
            "zip_code",
            "bedroom_count",
            "avg_occupied_rent",
            "avg_list_price",
            "avg_dom",
            "avg_lease_length_months",
            "leases_written_count",
            "total_leads",
            "total_showings",
            "total_applications",
            "avg_credit_score",
            "avg_applicant_income",
            "occupied_unit_count",
            "vacant_unit_count",
            "created_at",
        ]


class MonthlySegmentFilterSchema(FilterSchema):
    zip_code: Optional[str] = None
    bedroom_count: Optional[int] = None
    date_from: Optional[date] = Field(None, q="month__gte")
    date_to: Optional[date] = Field(None, q="month__lte")


class ListingCycleFilterSchema(FilterSchema):
    unit_id: Optional[int] = None
    date_from: Optional[date] = Field(None, q="listed_date__gte")
    date_to: Optional[date] = Field(None, q="listed_date__lte")

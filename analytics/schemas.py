from datetime import date
from decimal import Decimal
from typing import Optional

from ninja import Schema


class PortfolioSummarySchema(Schema):
    total_properties: int
    total_units: int
    occupied_units: int
    vacant_units: int
    vacancy_rate: float
    avg_target_rent: Optional[Decimal] = None
    avg_active_lease_rent: Optional[Decimal] = None
    active_leases: int
    pending_leases: int
    closed_leases: int
    total_prospects: int
    total_showings: int
    total_applications: int


class LeasingFunnelSchema(Schema):
    total_prospects: int
    total_showings_completed: int
    total_showings_missed: int
    total_applications: int
    total_approved: int
    total_declined: int
    total_leases_signed: int
    lead_to_show_rate: float
    show_to_app_rate: float
    app_to_lease_rate: float


class ProspectSourceSchema(Schema):
    source: str
    prospect_count: int
    showing_count: int
    application_count: int
    lead_to_show_rate: float
    show_to_app_rate: float


class VacantUnitSchema(Schema):
    unit_id: int
    unit_name: str
    address: str
    city: str
    state: str
    postal_code: str
    property_name: str
    property_type: str
    portfolio_name: Optional[str] = None
    bedrooms: Optional[int] = None
    square_feet: Optional[int] = None
    target_rental_rate: Optional[Decimal] = None
    days_vacant: Optional[int] = None
    prospect_count: int = 0

    @staticmethod
    def resolve_unit_id(obj):
        return obj.id

    @staticmethod
    def resolve_unit_name(obj):
        return obj.name

    @staticmethod
    def resolve_address(obj):
        return obj.address_line_1 or (obj.property.address_line_1 if obj.property else "")

    @staticmethod
    def resolve_city(obj):
        return obj.city or (obj.property.city if obj.property else "")

    @staticmethod
    def resolve_state(obj):
        return obj.state or (obj.property.state if obj.property else "")

    @staticmethod
    def resolve_postal_code(obj):
        return obj.postal_code or (obj.property.postal_code if obj.property else "")

    @staticmethod
    def resolve_property_name(obj):
        return obj.property.name if obj.property else ""

    @staticmethod
    def resolve_property_type(obj):
        return obj.property.property_type if obj.property else ""

    @staticmethod
    def resolve_portfolio_name(obj):
        prop = obj.property
        if prop and prop.portfolio:
            return prop.portfolio.name
        return None

    @staticmethod
    def resolve_days_vacant(obj):
        last_end = getattr(obj, "last_lease_end", None)
        if last_end:
            return (date.today() - last_end).days
        return None

    @staticmethod
    def resolve_prospect_count(obj):
        return getattr(obj, "prospect_count_ann", 0)


class RentSegmentSchema(Schema):
    segment_label: str
    unit_count: int
    occupied_count: int
    vacant_count: int
    avg_target_rent: Optional[Decimal] = None
    min_target_rent: Optional[Decimal] = None
    max_target_rent: Optional[Decimal] = None
    avg_active_lease_rent: Optional[Decimal] = None
    vacancy_rate: float
    property_names: list[str] = []


class PropertyPerformanceSchema(Schema):
    property_id: int
    name: str
    address: str
    city: str
    state: str
    property_type: str
    portfolio_name: Optional[str] = None
    unit_count: int
    occupied_count: int
    vacancy_rate: float
    avg_rent: Optional[Decimal] = None
    lease_count: int
    prospect_count: int
    showing_count: int
    application_count: int

    @staticmethod
    def resolve_property_id(obj):
        return obj.id

    @staticmethod
    def resolve_address(obj):
        return obj.address_line_1

    @staticmethod
    def resolve_portfolio_name(obj):
        return obj.portfolio.name if obj.portfolio else None

    @staticmethod
    def resolve_unit_count(obj):
        return getattr(obj, "unit_count_ann", 0)

    @staticmethod
    def resolve_occupied_count(obj):
        return getattr(obj, "occupied_count_ann", 0)

    @staticmethod
    def resolve_vacancy_rate(obj):
        total = getattr(obj, "unit_count_ann", 0)
        occupied = getattr(obj, "occupied_count_ann", 0)
        if total == 0:
            return 0.0
        return round((total - occupied) / total * 100, 2)

    @staticmethod
    def resolve_avg_rent(obj):
        return getattr(obj, "avg_rent_ann", None)

    @staticmethod
    def resolve_lease_count(obj):
        return getattr(obj, "lease_count_ann", 0)

    @staticmethod
    def resolve_prospect_count(obj):
        return getattr(obj, "prospect_count_ann", 0)

    @staticmethod
    def resolve_showing_count(obj):
        return getattr(obj, "showing_count_ann", 0)

    @staticmethod
    def resolve_application_count(obj):
        return getattr(obj, "application_count_ann", 0)


class ActiveListingSchema(Schema):
    unit_id: int
    address: str
    city: str
    state: str
    property_name: str
    portfolio_name: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[Decimal] = None
    square_feet: Optional[int] = None
    listed_price: Optional[Decimal] = None
    days_on_market: int
    date_listed: Optional[date] = None
    total_leads: int
    total_showings: int
    total_missed: int
    total_apps: int
    leads_per_active_day: float
    is_flagged: bool


class VacantUnitDetailSchema(Schema):
    unit_id: int
    address: str
    city: str
    state: str
    bedrooms: Optional[int] = None
    target_rent: Optional[Decimal] = None
    portfolio_name: str
    property_name: str


class OwnerVacancySchema(Schema):
    owner_id: int
    owner_name: str
    owner_email: str
    vacant_unit_count: int
    vacant_units: list[VacantUnitDetailSchema]


class LeaseExpirationDetailSchema(Schema):
    lease_id: int
    unit_address: str
    tenant_names: str
    end_date: date
    monthly_rent: Decimal


class LeaseExpirationMonthSchema(Schema):
    month: date
    expiring_count: int
    leases: list[LeaseExpirationDetailSchema]


# ---------------------------------------------------------------------------
# Revenue Intelligence
# ---------------------------------------------------------------------------


class RevenueLeakageSummarySchema(Schema):
    total_annual_rent_gap: Decimal
    avg_rent_gap_pct: float
    avg_turn_days: Optional[float] = None
    avg_concession_rate: Optional[float] = None
    units_below_target: int
    total_occupied_revenue_units: int


class RentGapSchema(Schema):
    unit_id: int
    address: str
    city: str
    bedrooms: Optional[int] = None
    target_rent: Decimal
    lease_rent: Decimal
    gap_amount: Decimal
    gap_pct: float
    is_critical: bool


class TurnCycleSchema(Schema):
    unit_id: int
    address: str
    bedrooms: Optional[int] = None
    move_out_date: Optional[date] = None
    listed_date: Optional[date] = None
    leased_date: Optional[date] = None
    move_in_date: Optional[date] = None
    make_ready_days: Optional[int] = None
    dom: Optional[int] = None
    total_turn_days: Optional[int] = None
    vacancy_cost: Optional[Decimal] = None
    make_ready_cost: Optional[Decimal] = None


class ConcessionSchema(Schema):
    unit_id: int
    address: str
    bedrooms: Optional[int] = None
    original_price: Optional[Decimal] = None
    final_price: Optional[Decimal] = None
    signed_amount: Optional[Decimal] = None
    list_to_lease_ratio: Optional[float] = None
    total_drops: int
    total_drop_amount: Decimal
    is_heavy_concession: bool


# ---------------------------------------------------------------------------
# Renewal Pipeline
# ---------------------------------------------------------------------------


class RenewalPipelineLeaseSchema(Schema):
    lease_id: int
    unit_id: int
    address: str
    city: str
    bedrooms: Optional[int] = None
    tenant_names: str
    lease_end: date
    days_until_expiry: int
    current_rent: Optional[Decimal] = None
    target_rent: Optional[Decimal] = None
    gap_pct: Optional[float] = None
    is_below_market: bool


class ExpirationClusterSchema(Schema):
    month: str
    month_label: str
    count: int
    is_concentrated: bool


class RenewalPipelineSchema(Schema):
    expiring_30d: int
    expiring_60d: int
    expiring_90d: int
    below_market_count: int
    total_active_leases: int
    clustering: list[ExpirationClusterSchema]
    leases: list[RenewalPipelineLeaseSchema]


# ---------------------------------------------------------------------------
# Leasing Pipeline
# ---------------------------------------------------------------------------


class LeasingPipelineSchema(Schema):
    total_prospects_30d: int
    total_showings_30d: int
    total_applications_30d: int
    total_approved_30d: int
    screening_pending: int
    screening_in_progress: int
    screening_completed: int
    scorecards_count: int
    avg_score: Optional[float] = None
    auto_deny_count: int
    platinum_count: int
    strong_count: int
    borderline_count: int
    high_risk_count: int
    reject_count: int
    lead_to_app_rate: float
    app_to_lease_rate: float


class ScorecardSummaryRowSchema(Schema):
    id: int
    applicant_name: str
    applicant_email: str
    unit_address: Optional[str] = None
    total_score: int
    recommendation: str
    screening_status: str
    reviewed_by: str
    updated_at: str


# ---------------------------------------------------------------------------
# Owner Report Detail
# ---------------------------------------------------------------------------


class WeeklyMetricsSchema(Schema):
    leads: int = 0
    showings: int = 0
    missed: int = 0
    apps: int = 0


class ShowingFeedbackSchema(Schema):
    date: Optional[str] = None
    prospect_name: str = ""
    feedback_summary: str = ""


class UpcomingShowingSchema(Schema):
    date: str
    time: str = ""
    prospect_name: str = ""


class PriceRecommendationSchema(Schema):
    recommended: bool = False
    current_lpd: float = 0.0
    threshold: float = 0.5
    suggested_price: Optional[Decimal] = None
    market_avg: Optional[Decimal] = None
    message: str = ""


class MarketContextSchema(Schema):
    zip_avg_list_price: Optional[Decimal] = None
    zip_avg_dom: Optional[int] = None
    zip_active_count: int = 0


class OwnerReportUnitSchema(Schema):
    unit_id: int
    address: str
    city: str
    state: str
    zip_code: str = ""
    bedrooms: Optional[int] = None
    target_rent: Optional[Decimal] = None
    current_list_price: Optional[Decimal] = None
    days_on_market: Optional[int] = None
    date_listed: Optional[date] = None
    weekly: WeeklyMetricsSchema
    prev_weekly: WeeklyMetricsSchema
    all_time: WeeklyMetricsSchema
    leads_per_active_day: float = 0.0
    market_context: MarketContextSchema
    showing_feedback: list[ShowingFeedbackSchema] = []
    upcoming_showings: list[UpcomingShowingSchema] = []
    price_recommendation: PriceRecommendationSchema
    zillow_url: str = ""
    property_note: str = ""
    property_note_author: str = ""
    prev_notes: list[dict] = []


class PortfolioAvgSchema(Schema):
    avg_dom: Optional[int] = None
    avg_list_price: Optional[Decimal] = None
    avg_portfolio_rent: Optional[Decimal] = None
    active_unit_count: int = 0


class OwnerReportDetailSchema(Schema):
    owner_id: int
    owner_name: str
    owner_email: str
    portfolio_averages: PortfolioAvgSchema
    units: list[OwnerReportUnitSchema]

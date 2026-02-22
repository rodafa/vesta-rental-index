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

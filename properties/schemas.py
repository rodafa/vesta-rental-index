from decimal import Decimal
from typing import Optional

from ninja import Field, FilterSchema, ModelSchema

from properties.models import (
    Floorplan,
    MultifamilyProperty,
    Owner,
    Portfolio,
    Property,
    Unit,
)


# --- Portfolio ---


class PortfolioListSchema(ModelSchema):
    class Meta:
        model = Portfolio
        fields = ["id", "name", "is_active"]


class PortfolioSchema(ModelSchema):
    class Meta:
        model = Portfolio
        fields = [
            "id",
            "name",
            "is_active",
            "reserve_amount",
            "additional_reserve_amount",
            "additional_reserve_description",
            "fiscal_year_end_month",
            "hold_distributions",
            "created_at",
            "updated_at",
        ]


class PortfolioFilterSchema(FilterSchema):
    is_active: Optional[bool] = None


# --- Owner ---


class OwnerListSchema(ModelSchema):
    class Meta:
        model = Owner
        fields = ["id", "name", "email", "phone", "is_active"]


class OwnerSchema(ModelSchema):
    portfolio_ids: list[int] = []

    class Meta:
        model = Owner
        fields = [
            "id",
            "name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "is_active",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_portfolio_ids(obj):
        return list(obj.portfolios.values_list("id", flat=True))


class OwnerFilterSchema(FilterSchema):
    is_active: Optional[bool] = None


# --- Property ---


class PropertyListSchema(ModelSchema):
    portfolio_name: Optional[str] = None
    unit_count: int = 0

    class Meta:
        model = Property
        fields = [
            "id",
            "name",
            "property_type",
            "service_type",
            "address_line_1",
            "city",
            "state",
            "postal_code",
            "is_active",
            "is_multi_unit",
            "portfolio",
        ]

    @staticmethod
    def resolve_portfolio_name(obj):
        return obj.portfolio.name if obj.portfolio else None

    @staticmethod
    def resolve_unit_count(obj):
        if hasattr(obj, "unit_count"):
            return obj.unit_count
        return obj.units.count()


class PropertySchema(ModelSchema):
    portfolio_name: Optional[str] = None
    unit_count: int = 0

    class Meta:
        model = Property
        fields = [
            "id",
            "name",
            "property_type",
            "service_type",
            "address_line_1",
            "address_line_2",
            "city",
            "state",
            "postal_code",
            "country",
            "latitude",
            "longitude",
            "county",
            "street_number",
            "street_name",
            "is_multi_unit",
            "year_built",
            "is_active",
            "portfolio",
            "maintenance_limit_amount",
            "reserve_amount",
            "date_contract_begins",
            "date_contract_ends",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_portfolio_name(obj):
        return obj.portfolio.name if obj.portfolio else None

    @staticmethod
    def resolve_unit_count(obj):
        if hasattr(obj, "unit_count"):
            return obj.unit_count
        return obj.units.count()


class PropertyFilterSchema(FilterSchema):
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    property_type: Optional[str] = None
    service_type: Optional[str] = None
    is_active: Optional[bool] = None
    portfolio_id: Optional[int] = None
    is_multi_unit: Optional[bool] = None


# --- Unit ---


class UnitListSchema(ModelSchema):
    property_address: str = ""

    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "property",
            "address_line_1",
            "city",
            "state",
            "postal_code",
            "bedrooms",
            "full_bathrooms",
            "half_bathrooms",
            "square_feet",
            "target_rental_rate",
            "is_active",
        ]

    @staticmethod
    def resolve_property_address(obj):
        return obj.property.address_line_1 or obj.property.name or ""


class UnitSchema(ModelSchema):
    property_address: str = ""

    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "property",
            "address_line_1",
            "address_line_2",
            "city",
            "state",
            "postal_code",
            "latitude",
            "longitude",
            "bedrooms",
            "full_bathrooms",
            "half_bathrooms",
            "square_feet",
            "target_rental_rate",
            "deposit",
            "is_active",
            "multifamily_property",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_property_address(obj):
        return obj.property.address_line_1 or obj.property.name or ""


class UnitFilterSchema(FilterSchema):
    property_id: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    bedrooms: Optional[int] = None
    is_active: Optional[bool] = None
    min_rent: Optional[Decimal] = Field(None, q="target_rental_rate__gte")
    max_rent: Optional[Decimal] = Field(None, q="target_rental_rate__lte")


# --- MultifamilyProperty ---


class MultifamilyPropertySchema(ModelSchema):
    class Meta:
        model = MultifamilyProperty
        fields = [
            "id",
            "rentengine_id",
            "name",
            "text_address",
            "created_at",
            "updated_at",
        ]


# --- Floorplan ---


class FloorplanSchema(ModelSchema):
    class Meta:
        model = Floorplan
        fields = [
            "id",
            "rentengine_id",
            "name",
            "multifamily_property",
            "created_at",
            "updated_at",
        ]

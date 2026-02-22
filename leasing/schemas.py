from datetime import date
from typing import Optional

from ninja import Field, FilterSchema, ModelSchema

from leasing.models import (
    Applicant,
    Application,
    Lease,
    LeasingEvent,
    Prospect,
    Showing,
    Tenant,
)


# --- Tenant ---


class TenantListSchema(ModelSchema):
    class Meta:
        model = Tenant
        fields = ["id", "name", "email", "phone", "is_active"]


class TenantSchema(ModelSchema):
    class Meta:
        model = Tenant
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


class TenantFilterSchema(FilterSchema):
    is_active: Optional[bool] = None


# --- Lease ---


class LeaseListSchema(ModelSchema):
    primary_lease_status_display: Optional[str] = None
    move_out_status_display: Optional[str] = None
    tenant_names: list[str] = []
    unit_address: str = ""

    class Meta:
        model = Lease
        fields = [
            "id",
            "unit",
            "property",
            "primary_lease_status",
            "move_out_status",
            "start_date",
            "end_date",
            "move_in_date",
            "is_renewal",
        ]

    @staticmethod
    def resolve_primary_lease_status_display(obj):
        return (
            obj.get_primary_lease_status_display()
            if obj.primary_lease_status
            else None
        )

    @staticmethod
    def resolve_move_out_status_display(obj):
        return (
            obj.get_move_out_status_display() if obj.move_out_status else None
        )

    @staticmethod
    def resolve_tenant_names(obj):
        return list(obj.tenants.values_list("name", flat=True))

    @staticmethod
    def resolve_unit_address(obj):
        return obj.unit.address_line_1 or obj.unit.name or ""


class LeaseSchema(ModelSchema):
    primary_lease_status_display: Optional[str] = None
    move_out_status_display: Optional[str] = None
    tenants: list[TenantListSchema] = []
    unit_address: str = ""

    class Meta:
        model = Lease
        fields = [
            "id",
            "unit",
            "property",
            "primary_lease_status",
            "lease_status_id",
            "move_out_status",
            "move_in_date",
            "start_date",
            "end_date",
            "closed_date",
            "notice_date",
            "expected_move_out_date",
            "move_out_date",
            "lease_return_charge_amount",
            "is_renewal",
            "previous_lease",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_primary_lease_status_display(obj):
        return (
            obj.get_primary_lease_status_display()
            if obj.primary_lease_status
            else None
        )

    @staticmethod
    def resolve_move_out_status_display(obj):
        return (
            obj.get_move_out_status_display() if obj.move_out_status else None
        )

    @staticmethod
    def resolve_tenants(obj):
        return obj.tenants.all()

    @staticmethod
    def resolve_unit_address(obj):
        return obj.unit.address_line_1 or obj.unit.name or ""


class LeaseFilterSchema(FilterSchema):
    primary_lease_status: Optional[int] = None
    unit_id: Optional[int] = None
    property_id: Optional[int] = None
    is_renewal: Optional[bool] = None


# --- Prospect ---


class ProspectListSchema(ModelSchema):
    unit_address: Optional[str] = None

    class Meta:
        model = Prospect
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "source",
            "status",
            "unit_of_interest",
        ]

    @staticmethod
    def resolve_unit_address(obj):
        if obj.unit_of_interest:
            return (
                obj.unit_of_interest.address_line_1
                or obj.unit_of_interest.name
                or ""
            )
        return None


class ProspectSchema(ModelSchema):
    unit_address: Optional[str] = None

    class Meta:
        model = Prospect
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "source",
            "status",
            "unit_of_interest",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_unit_address(obj):
        if obj.unit_of_interest:
            return (
                obj.unit_of_interest.address_line_1
                or obj.unit_of_interest.name
                or ""
            )
        return None


class ProspectFilterSchema(FilterSchema):
    source: Optional[str] = None
    status: Optional[str] = None
    unit_id: Optional[int] = Field(None, q="unit_of_interest_id")


# --- LeasingEvent ---


class LeasingEventSchema(ModelSchema):
    event_type_display: str = ""

    class Meta:
        model = LeasingEvent
        fields = [
            "id",
            "prospect",
            "unit",
            "event_type",
            "event_timestamp",
            "event_date",
            "created_at",
        ]

    @staticmethod
    def resolve_event_type_display(obj):
        return obj.get_event_type_display()


class LeasingEventFilterSchema(FilterSchema):
    event_type: Optional[str] = None
    prospect_id: Optional[int] = None
    unit_id: Optional[int] = None
    date_from: Optional[date] = Field(None, q="event_date__gte")
    date_to: Optional[date] = Field(None, q="event_date__lte")


# --- Showing ---


class ShowingSchema(ModelSchema):
    class Meta:
        model = Showing
        fields = [
            "id",
            "prospect",
            "unit",
            "showing_method",
            "status",
            "scheduled_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]


class ShowingFilterSchema(FilterSchema):
    status: Optional[str] = None
    showing_method: Optional[str] = None
    unit_id: Optional[int] = None
    prospect_id: Optional[int] = None


# --- Application ---


class ApplicantSchema(ModelSchema):
    class Meta:
        model = Applicant
        fields = ["id", "name", "email", "phone"]


class ApplicationListSchema(ModelSchema):
    primary_status_display: Optional[str] = None
    applicant_count: int = 0
    unit_address: Optional[str] = None

    class Meta:
        model = Application
        fields = [
            "id",
            "unit",
            "primary_status",
            "number",
            "address",
            "city",
            "state",
            "postal_code",
        ]

    @staticmethod
    def resolve_primary_status_display(obj):
        return (
            obj.get_primary_status_display() if obj.primary_status else None
        )

    @staticmethod
    def resolve_applicant_count(obj):
        if hasattr(obj, "applicant_count"):
            return obj.applicant_count
        return obj.applicants.count()

    @staticmethod
    def resolve_unit_address(obj):
        if obj.unit:
            return obj.unit.address_line_1 or obj.unit.name or ""
        return None


class ApplicationSchema(ModelSchema):
    primary_status_display: Optional[str] = None
    applicants: list[ApplicantSchema] = []
    unit_address: Optional[str] = None

    class Meta:
        model = Application
        fields = [
            "id",
            "unit",
            "primary_status",
            "application_status_id",
            "number",
            "address",
            "city",
            "state",
            "postal_code",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_primary_status_display(obj):
        return (
            obj.get_primary_status_display() if obj.primary_status else None
        )

    @staticmethod
    def resolve_applicants(obj):
        return obj.applicants.all()

    @staticmethod
    def resolve_unit_address(obj):
        if obj.unit:
            return obj.unit.address_line_1 or obj.unit.name or ""
        return None


class ApplicationFilterSchema(FilterSchema):
    primary_status: Optional[int] = None
    unit_id: Optional[int] = None

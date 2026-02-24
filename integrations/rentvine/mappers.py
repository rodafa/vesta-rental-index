"""
Field mapping functions: Rentvine API JSON -> Django model field dicts.

Every mapper is defensive -- uses .get() with multiple fallback field names
and safe type coercion helpers that never raise.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe type coercion helpers
# ---------------------------------------------------------------------------

def _safe_decimal(value):
    """Convert to Decimal or return None."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_int(value):
    """Convert to int or return None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_date(value):
    """Parse a date string (YYYY-MM-DD or ISO datetime) or return None."""
    if value is None or value == "" or value == "0000-00-00":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        # Try ISO datetime first, then plain date
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value)[:19], fmt).date()
            except ValueError:
                continue
    except (TypeError, ValueError):
        pass
    return None


def _safe_datetime(value):
    """Parse a datetime string or return None. Always returns timezone-aware."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(str(value)[:26], fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    except (TypeError, ValueError):
        pass
    return None


def _safe_bool(value, default=False):
    """Convert to bool or return default."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return default


def _get(data, *keys, default=""):
    """Get the first non-None value from multiple possible keys."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

def map_portfolio(data):
    """
    Map Rentvine portfolio JSON to Portfolio model fields.

    API response wraps each record in {"portfolio": {...}, "statementSetting": {...}}.
    This mapper accepts either the inner portfolio dict or the wrapper.
    Returns (rentvine_id, defaults_dict).
    """
    # Unwrap if nested under "portfolio" key
    if "portfolio" in data and isinstance(data["portfolio"], dict):
        raw_data = data  # preserve full response including statementSetting
        data = data["portfolio"]
    else:
        raw_data = data

    rentvine_id = _safe_int(
        _get(data, "portfolioID", "portfolio_id", "id")
    )
    if rentvine_id is None:
        raise ValueError(f"Portfolio record missing ID: {data}")

    defaults = {
        "name": str(_get(data, "name", "portfolioName", "portfolio_name", default="")),
        "is_active": _safe_bool(
            _get(data, "isActive", "is_active", "active", default=True), default=True
        ),
        "reserve_amount": _safe_decimal(
            _get(data, "reserveAmount", "reserve_amount", default=0)
        ) or Decimal("0"),
        "additional_reserve_amount": _safe_decimal(
            _get(data, "additionalReserveAmount", "additional_reserve_amount", default=0)
        ) or Decimal("0"),
        "additional_reserve_description": str(
            _get(data, "holdDistributionsDescription", "additionalReserveDescription", default="")
        ),
        "fiscal_year_end_month": _safe_int(
            _get(data, "fiscalYearEndMonth", "fiscal_year_end_month", default=None)
        ),
        "hold_distributions": _safe_bool(
            _get(data, "holdDistributions", "hold_distributions", default=False)
        ),
        "raw_data": raw_data,
        "source_created_at": _safe_datetime(
            _get(data, "dateTimeCreated", "createdAt", "created_at", default=None)
        ),
    }

    return rentvine_id, defaults


def map_owner(data):
    """
    Map Rentvine owner/contact JSON to Owner model fields.

    API response wraps each record in {"contact": {...}}.
    This mapper accepts either the inner contact dict or the wrapper.
    Returns (rentvine_contact_id, defaults_dict).
    """
    # Unwrap if nested under "contact" key
    if "contact" in data and isinstance(data["contact"], dict):
        raw_data = data
        data = data["contact"]
    else:
        raw_data = data

    rentvine_contact_id = _safe_int(
        _get(data, "contactID", "contact_id", "ownerID", "owner_id", "id")
    )
    if rentvine_contact_id is None:
        raise ValueError(f"Owner record missing contact ID: {data}")

    first = str(_get(data, "firstName", "first_name", default=""))
    last = str(_get(data, "lastName", "last_name", default=""))
    full_name = str(_get(data, "name", "fullName", "full_name", "contactName", default=""))
    if not full_name and (first or last):
        full_name = f"{first} {last}".strip()

    # Extract email/phone from nested structures or flat fields
    email = ""
    emails_field = data.get("emails")
    if isinstance(emails_field, list) and emails_field:
        email = str(emails_field[0].get("email", "")) if isinstance(emails_field[0], dict) else str(emails_field[0])
    elif isinstance(emails_field, str) and emails_field:
        email = emails_field
    else:
        email = str(_get(data, "email", "emailAddress", default=""))

    phone = ""
    phones_field = data.get("phones")
    if isinstance(phones_field, list) and phones_field:
        phone = str(phones_field[0].get("phone", "")) if isinstance(phones_field[0], dict) else str(phones_field[0])
    elif isinstance(phones_field, str) and phones_field:
        phone = phones_field
    else:
        phone = str(_get(data, "phone", "phoneNumber", default=""))

    defaults = {
        "name": full_name,
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
        "is_active": _safe_bool(
            _get(data, "isActive", "is_active", "active", default=True), default=True
        ),
        "raw_data": raw_data,
    }

    return rentvine_contact_id, defaults


def map_property(data):
    """
    Map Rentvine property JSON to Property model fields.

    API response wraps each record in {"property": {...}, "token": "..."}.
    This mapper accepts either the inner property dict or the wrapper.
    Returns (rentvine_id, portfolio_rentvine_id_or_none, defaults_dict).
    """
    # Unwrap if nested under "property" key
    if "property" in data and isinstance(data["property"], dict):
        raw_data = data
        data = data["property"]
    else:
        raw_data = data

    rentvine_id = _safe_int(
        _get(data, "propertyID", "property_id", "id")
    )
    if rentvine_id is None:
        raise ValueError(f"Property record missing ID: {data}")

    portfolio_rentvine_id = _safe_int(
        _get(data, "portfolioID", "portfolio_id", default=None)
    )

    # Rentvine uses propertyTypeID (integer), not a string type name
    # Map known IDs; store raw for debugging
    raw_type_id = _safe_int(_get(data, "propertyTypeID", "property_type_id", default=None))
    raw_type_str = str(_get(data, "propertyType", "property_type", "type", default="")).lower()
    type_id_map = {
        1: "single_family",
        2: "apartment",
        3: "condo",
        4: "townhouse",
        5: "duplex",
        6: "multiplex",
        7: "loft",
        8: "mobile_home",
        9: "commercial",
        10: "garage",
    }
    type_str_map = {
        "single family": "single_family",
        "singlefamily": "single_family",
        "single_family": "single_family",
        "apartment": "apartment",
        "condo": "condo",
        "condominium": "condo",
        "townhouse": "townhouse",
        "townhome": "townhouse",
        "duplex": "duplex",
        "multiplex": "multiplex",
        "multi-family": "multiplex",
        "multifamily": "multiplex",
        "loft": "loft",
        "mobile home": "mobile_home",
        "mobile_home": "mobile_home",
        "commercial": "commercial",
        "garage": "garage",
    }
    property_type = type_id_map.get(raw_type_id, "") or type_str_map.get(raw_type_str, "")

    # Build address_line_1: Rentvine uses "address" as primary field
    address_line_1 = str(_get(data, "address", "addressLine1", "address_line_1", "address1", default=""))
    street_number = str(_get(data, "streetNumber", "street_number", default=""))
    street_name = str(_get(data, "streetName", "street_name", default=""))
    if not address_line_1 and (street_number or street_name):
        address_line_1 = f"{street_number} {street_name}".strip()

    defaults = {
        "name": str(_get(data, "name", "propertyName", "property_name", default="")),
        "property_type": property_type,
        "is_multi_unit": _safe_bool(
            _get(data, "isMultiUnit", "is_multi_unit", "multiUnit", default=False)
        ),
        "street_number": street_number,
        "street_name": street_name,
        "address_line_1": address_line_1,
        "address_line_2": str(_get(data, "address2", "addressLine2", "address_line_2", default="")),
        "city": str(_get(data, "city", default="")),
        "state": str(_get(data, "stateID", "state", "stateCode", default=""))[:2],
        "postal_code": str(_get(data, "postalCode", "postal_code", "zip", "zipCode", default="")),
        "country": str(_get(data, "countryID", "country", "countryCode", default="US"))[:2] or "US",
        "latitude": _safe_decimal(_get(data, "latitude", "lat", default=None)),
        "longitude": _safe_decimal(_get(data, "longitude", "lng", "lon", default=None)),
        "county": str(_get(data, "county", default="")),
        "year_built": _safe_int(_get(data, "yearBuilt", "year_built", default=None)),
        "is_active": _safe_bool(
            _get(data, "isActive", "is_active", "active", default=True), default=True
        ),
        "management_fee_setting_id": _safe_int(
            _get(data, "managementFeeSettingID", "management_fee_setting_id", default=None)
        ),
        "maintenance_limit_amount": _safe_decimal(
            _get(data, "maintenanceLimitAmount", "maintenance_limit_amount", default=None)
        ),
        "reserve_amount": _safe_decimal(
            _get(data, "reserveAmount", "reserve_amount", default=0)
        ) or Decimal("0"),
        "date_contract_begins": _safe_date(
            _get(data, "dateContractBegins", "date_contract_begins", default=None)
        ),
        "date_contract_ends": _safe_date(
            _get(data, "dateContractEnds", "date_contract_ends", default=None)
        ),
        "date_insurance_expires": _safe_date(
            _get(data, "dateInsuranceExpires", "date_insurance_expires", default=None)
        ),
        "date_warranty_expires": _safe_date(
            _get(data, "dateWarrantyExpires", "date_warranty_expires", default=None)
        ),
        "raw_data": raw_data,
        "source_created_at": _safe_datetime(
            _get(data, "dateTimeCreated", "createdAt", "created_at", default=None)
        ),
    }

    return rentvine_id, portfolio_rentvine_id, defaults


def map_unit(data):
    """
    Map Rentvine unit JSON to Unit model fields.

    API response wraps each record in {"unit": {...}, "token": "..."}.
    This mapper accepts either the inner unit dict or the wrapper.
    Returns (rentvine_id, property_rentvine_id, defaults_dict).
    """
    # Unwrap if nested under "unit" key
    if "unit" in data and isinstance(data["unit"], dict):
        raw_data = data
        data = data["unit"]
    else:
        raw_data = data

    rentvine_id = _safe_int(
        _get(data, "unitID", "unit_id", "id")
    )
    if rentvine_id is None:
        raise ValueError(f"Unit record missing ID: {data}")

    property_rentvine_id = _safe_int(
        _get(data, "propertyID", "property_id", default=None)
    )

    defaults = {
        "name": str(_get(data, "name", "unitName", "unit_name", default="")),
        "address_line_1": str(_get(data, "address", "addressLine1", "address_line_1", "address1", default="")),
        "address_line_2": str(_get(data, "address2", "addressLine2", "address_line_2", default="")),
        "city": str(_get(data, "city", default="")),
        "state": str(_get(data, "stateID", "state", "stateCode", default=""))[:2],
        "postal_code": str(_get(data, "postalCode", "postal_code", "zip", "zipCode", default="")),
        "latitude": _safe_decimal(_get(data, "latitude", "lat", default=None)),
        "longitude": _safe_decimal(_get(data, "longitude", "lng", "lon", default=None)),
        "bedrooms": _safe_int(_get(data, "beds", "bedrooms", "numberOfBedrooms", default=None)),
        "full_bathrooms": _safe_int(
            _get(data, "fullBaths", "fullBathrooms", "full_bathrooms", "bathrooms", default=None)
        ),
        "half_bathrooms": _safe_int(
            _get(data, "halfBaths", "halfBathrooms", "half_bathrooms", default=None)
        ),
        "square_feet": _safe_int(
            _get(data, "size", "squareFeet", "square_feet", "sqft", default=None)
        ),
        "target_rental_rate": _safe_decimal(
            _get(data, "rent", "targetRentalRate", "target_rental_rate", "rentAmount", default=None)
        ),
        "deposit": _safe_decimal(
            _get(data, "deposit", "depositAmount", "securityDeposit", default=None)
        ),
        "is_active": _safe_bool(
            _get(data, "isActive", "is_active", "active", default=True), default=True
        ),
        "raw_data": raw_data,
        "source_created_at": _safe_datetime(
            _get(data, "dateTimeCreated", "createdAt", "created_at", default=None)
        ),
    }

    return rentvine_id, property_rentvine_id, defaults


def map_lease(data):
    """
    Map Rentvine lease JSON to Lease model fields.

    API response wraps each record in {"lease": {...}, "property": {...}, "unit": {...}}.
    Returns (rentvine_id, unit_rentvine_id, property_rentvine_id, defaults_dict).
    """
    raw_data = data

    # Unwrap nested keys to extract FK IDs from sibling objects
    unit_rentvine_id = None
    property_rentvine_id = None
    if "unit" in data and isinstance(data["unit"], dict):
        unit_rentvine_id = _safe_int(data["unit"].get("unitID"))
    if "property" in data and isinstance(data["property"], dict):
        property_rentvine_id = _safe_int(data["property"].get("propertyID"))

    # Unwrap the lease envelope
    if "lease" in data and isinstance(data["lease"], dict):
        data = data["lease"]

    rentvine_id = _safe_int(
        _get(data, "leaseID", "lease_id", "id")
    )
    if rentvine_id is None:
        raise ValueError(f"Lease record missing ID: {data}")

    # FK IDs from the lease object itself (fallbacks)
    if unit_rentvine_id is None:
        unit_rentvine_id = _safe_int(_get(data, "unitID", "unit_id", default=None))
    if property_rentvine_id is None:
        property_rentvine_id = _safe_int(_get(data, "propertyID", "property_id", default=None))

    defaults = {
        "primary_lease_status": _safe_int(
            _get(data, "primaryLeaseStatusID", "primary_lease_status_id", default=None)
        ),
        "lease_status_id": _safe_int(
            _get(data, "leaseStatusID", "lease_status_id", default=None)
        ),
        "move_out_status": _safe_int(
            _get(data, "moveOutStatusID", "move_out_status_id", default=None)
        ),
        "move_in_date": _safe_date(
            _get(data, "dateMoveIn", "moveInDate", "move_in_date", default=None)
        ),
        "start_date": _safe_date(
            _get(data, "dateLeaseStart", "startDate", "start_date", default=None)
        ),
        "end_date": _safe_date(
            _get(data, "dateLeaseEnd", "endDate", "end_date", default=None)
        ),
        "closed_date": _safe_date(
            _get(data, "dateClosed", "closedDate", "closed_date", default=None)
        ),
        "notice_date": _safe_date(
            _get(data, "dateNotice", "noticeDate", "notice_date", default=None)
        ),
        "expected_move_out_date": _safe_date(
            _get(data, "dateExpectedMoveOut", "expectedMoveOutDate", default=None)
        ),
        "move_out_date": _safe_date(
            _get(data, "dateMoveOut", "moveOutDate", "move_out_date", default=None)
        ),
        "deposit_refund_due_date": _safe_date(
            _get(data, "dateDepositRefundDue", "depositRefundDueDate", default=None)
        ),
        "lease_return_charge_amount": _safe_decimal(
            _get(data, "leaseReturnChargeAmount", "lease_return_charge_amount", default=0)
        ) or Decimal("0"),
        "renters_insurance_company": str(
            _get(data, "rentersInsuranceCompany", default="")
        ),
        "renters_insurance_policy_number": str(
            _get(data, "rentersInsurancePolicyNumber", default="")
        ),
        "renters_insurance_expiration_date": _safe_date(
            _get(data, "dateRentersInsuranceExpires", default=None)
        ),
        "move_out_reason_id": _safe_int(
            _get(data, "moveOutReasonID", "move_out_reason_id", default=None)
        ),
        "move_out_tenant_remarks": str(
            _get(data, "moveOutTenantRemarks", "move_out_tenant_remarks", default="")
        ),
        "forwarding_name": str(_get(data, "forwardingName", default="")),
        "forwarding_address": str(_get(data, "forwardingAddress", default="")),
        "forwarding_city": str(_get(data, "forwardingCity", default="")),
        "forwarding_state": str(_get(data, "forwardingState", default=""))[:2],
        "forwarding_postal_code": str(_get(data, "forwardingPostalCode", default="")),
        "forwarding_email": str(_get(data, "forwardingEmail", default="")),
        "forwarding_phone": str(_get(data, "forwardingPhone", default="")),
        "rentvine_application_id": _safe_int(
            _get(data, "applicationID", "application_id", default=None)
        ),
        "raw_data": raw_data,
        "source_created_at": _safe_datetime(
            _get(data, "dateTimeCreated", "createdAt", "created_at", default=None)
        ),
    }

    return rentvine_id, unit_rentvine_id, property_rentvine_id, defaults


def map_tenant_from_lease(data):
    """
    Map Rentvine lease tenant JSON to Tenant model fields.

    API response from /leases/{id}/tenants wraps each record in
    {"leaseTenant": {...}, "contact": {...}}.
    Returns (rentvine_contact_id, is_primary, defaults_dict).
    """
    raw_data = data

    # Extract primary flag from leaseTenant envelope
    is_primary = False
    if "leaseTenant" in data and isinstance(data["leaseTenant"], dict):
        is_primary = _safe_bool(
            _get(data["leaseTenant"], "isPrimary", "is_primary", default=False)
        )

    # Unwrap contact envelope
    if "contact" in data and isinstance(data["contact"], dict):
        data = data["contact"]

    rentvine_contact_id = _safe_int(
        _get(data, "contactID", "contact_id", "id")
    )
    if rentvine_contact_id is None:
        raise ValueError(f"Tenant record missing contact ID: {data}")

    first = str(_get(data, "firstName", "first_name", default=""))
    last = str(_get(data, "lastName", "last_name", default=""))
    full_name = str(_get(data, "name", "fullName", "full_name", "contactName", default=""))
    if not full_name and (first or last):
        full_name = f"{first} {last}".strip()

    # Extract email/phone from nested structures or flat fields
    email = ""
    emails_field = data.get("emails")
    if isinstance(emails_field, list) and emails_field:
        email = str(emails_field[0].get("email", "")) if isinstance(emails_field[0], dict) else str(emails_field[0])
    elif isinstance(emails_field, str) and emails_field:
        email = emails_field
    else:
        email = str(_get(data, "email", "emailAddress", default=""))

    phone = ""
    phones_field = data.get("phones")
    if isinstance(phones_field, list) and phones_field:
        phone = str(phones_field[0].get("phone", "")) if isinstance(phones_field[0], dict) else str(phones_field[0])
    elif isinstance(phones_field, str) and phones_field:
        phone = phones_field
    else:
        phone = str(_get(data, "phone", "phoneNumber", default=""))

    defaults = {
        "name": full_name,
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
        "is_active": _safe_bool(
            _get(data, "isActive", "is_active", "active", default=True), default=True
        ),
        "raw_data": raw_data,
    }

    return rentvine_contact_id, is_primary, defaults

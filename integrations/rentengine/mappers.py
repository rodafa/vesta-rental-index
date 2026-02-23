"""
Field mapping functions: RentEngine API JSON -> Django model field dicts.

Reuses _safe_* helpers from integrations.rentvine.mappers to avoid duplication.
"""

import logging

from integrations.rentvine.mappers import (
    _safe_decimal,
    _safe_int,
    _safe_date,
    _safe_datetime,
    _safe_bool,
    _get,
)

logger = logging.getLogger(__name__)

# US state name -> 2-letter code for normalising RentEngine's full state names
_STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def _state_to_code(value):
    """Convert a state name or code to a 2-letter code."""
    if not value:
        return ""
    v = str(value).strip()
    if len(v) <= 2:
        return v.upper()
    return _STATE_ABBREV.get(v.lower(), v[:2].upper())


def _extract_address(data):
    """
    Extract address fields from a RentEngine unit record.

    RentEngine nests address data under an "address" key:
    {"address": {"formatted_address": "12 Wood Rd", "street_number": "12",
     "street_name": "Wood Rd", "city": "Arden", "state": "North Carolina",
     "zip_code": "28704", "unit": "B", ...}}
    """
    addr = data.get("address", {})
    if isinstance(addr, dict):
        formatted = str(addr.get("formatted_address", "") or "")
        street_number = str(addr.get("street_number", "") or "")
        street_name = str(addr.get("street_name", "") or "")
        address_line_1 = formatted or f"{street_number} {street_name}".strip()
        unit_number = str(addr.get("unit", "") or "")
        return {
            "address_line_1": address_line_1,
            "address_line_2": unit_number,
            "city": str(addr.get("city", "") or ""),
            "state": _state_to_code(addr.get("state", "")),
            "postal_code": str(addr.get("zip_code", "") or addr.get("postalCode", "") or ""),
            "street_number": street_number,
            "unit_number": unit_number,
        }
    # Fallback: flat fields
    return {
        "address_line_1": str(_get(data, "formatted_address", "street_address", default="")),
        "address_line_2": "",
        "city": str(_get(data, "city", default="")),
        "state": _state_to_code(_get(data, "state", "stateCode", default="")),
        "postal_code": str(_get(data, "zip", "zip_code", "postalCode", default="")),
        "street_number": str(_get(data, "street_number", default="")),
        "unit_number": "",
    }


def map_re_unit(data):
    """
    Map RentEngine unit JSON to address fields for matching existing units.

    Returns (rentengine_id, defaults_dict).
    The defaults contain address fields used to match against existing Unit records.
    """
    rentengine_id = _safe_int(
        _get(data, "id", "unitId", "unit_id", default=None)
    )
    if rentengine_id is None:
        raise ValueError(f"RentEngine unit record missing ID: {data}")

    addr = _extract_address(data)

    defaults = {
        "name": str(_get(data, "name", "unitName", "unit_name", default="")),
        "address_line_1": addr["address_line_1"],
        "address_line_2": addr["address_line_2"],
        "city": addr["city"],
        "state": addr["state"],
        "postal_code": addr["postal_code"],
        "street_number": addr["street_number"],
        "unit_number": addr["unit_number"],
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
        "extracted_from": str(_get(data, "extracted_from", default="") or ""),
        "raw_data": data,
    }

    return rentengine_id, defaults


def map_daily_snapshot(data, snapshot_date):
    """
    Map RentEngine unit JSON to DailyUnitSnapshot defaults.

    Returns a dict of defaults for DailyUnitSnapshot.update_or_create().
    Fields: listed_price, days_on_market, status, bedrooms, bathrooms, square_feet.
    """
    # Map RentEngine status strings to our STATUS_CHOICES
    raw_status = str(_get(
        data, "status", "listingStatus", "listing_status", default=""
    )).lower()
    status_map = {
        "active": "active",
        "listed": "active",
        "available": "active",
        "inactive": "inactive",
        "unlisted": "inactive",
        "leased": "leased",
        "rented": "leased",
        "occupied": "leased",
        "off_market": "off_market",
        "off market": "off_market",
        "removed": "off_market",
    }
    status = status_map.get(raw_status, raw_status if raw_status in ("active", "inactive", "leased", "off_market") else "")

    # Compute bathrooms as a decimal (full + 0.5 * half)
    full_baths = _safe_int(_get(
        data, "fullBaths", "fullBathrooms", "full_bathrooms", "bathrooms", default=None
    ))
    half_baths = _safe_int(_get(
        data, "halfBaths", "halfBathrooms", "half_bathrooms", default=None
    ))
    bathrooms = None
    if full_baths is not None:
        bathrooms = _safe_decimal(full_baths + (0.5 * (half_baths or 0)))

    return {
        "listed_price": _safe_decimal(
            _get(data, "price", "listedPrice", "listed_price", "rent", "rentAmount", default=None)
        ),
        "days_on_market": _safe_int(
            _get(data, "daysOnMarket", "days_on_market", "dom", default=None)
        ),
        "status": status,
        "bedrooms": _safe_int(
            _get(data, "beds", "bedrooms", "numberOfBedrooms", default=None)
        ),
        "bathrooms": bathrooms,
        "square_feet": _safe_int(
            _get(data, "size", "squareFeet", "square_feet", "sqft", default=None)
        ),
        "date_listed": _safe_date(
            _get(data, "dateListed", "date_listed", "listedDate", default=None)
        ),
        "date_off_market": _safe_date(
            _get(data, "dateOffMarket", "date_off_market", "offMarketDate", default=None)
        ),
    }


def map_leasing_performance(data):
    """
    Map RentEngine leasing performance JSON to DailyLeasingSummary defaults.

    Returns a dict of defaults for DailyLeasingSummary.update_or_create().
    Fields: leads_count, showings_completed_count, showings_missed_count, applications_count.
    """
    return {
        "leads_count": _safe_int(
            _get(data, "leads", "leadsCount", "leads_count", "totalLeads", default=0)
        ) or 0,
        "showings_completed_count": _safe_int(
            _get(
                data,
                "showingsCompleted", "showings_completed", "completedShowings",
                "showingsCompletedCount", "completed_showings",
                default=0,
            )
        ) or 0,
        "showings_missed_count": _safe_int(
            _get(
                data,
                "showingsMissed", "showings_missed", "missedShowings",
                "showingsMissedCount", "missed_showings",
                default=0,
            )
        ) or 0,
        "applications_count": _safe_int(
            _get(
                data,
                "applications", "applicationsCount", "applications_count",
                "totalApplications",
                default=0,
            )
        ) or 0,
    }

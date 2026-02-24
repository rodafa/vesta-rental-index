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

import re

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
            _get(data, "target_rental_rate", "price", "listedPrice", "listed_price",
                 "rent", "rentAmount", default=None)
        ),
        "days_on_market": _safe_int(
            _get(data, "days_on_market", "daysOnMarket", "dom", default=None)
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
            _get(data, "created_at", "dateListed", "date_listed", "listedDate", default=None)
        ),
        "date_off_market": _safe_date(
            _get(data, "dateOffMarket", "date_off_market", "offMarketDate", default=None)
        ),
    }


def map_leasing_performance(data):
    """
    Map RentEngine leasing performance JSON to DailyLeasingSummary defaults.

    RentEngine /reporting/leasing-performance/units/{id} returns:
      new_prospects, showings_scheduled, showings_completed,
      applications_requested, applications_submitted, days_on_market, etc.

    Returns (summary_defaults, extra) where extra contains fields for
    updating the DailyUnitSnapshot (days_on_market).
    """
    scheduled = _safe_int(
        _get(data, "showings_scheduled", "showingsScheduled", default=0)
    ) or 0
    completed = _safe_int(
        _get(data, "showings_completed", "showingsCompleted",
             "completedShowings", default=0)
    ) or 0

    summary = {
        "leads_count": _safe_int(
            _get(data, "new_prospects", "leads", "leadsCount", "leads_count",
                 "totalLeads", default=0)
        ) or 0,
        "showings_completed_count": completed,
        "showings_missed_count": _safe_int(
            _get(data, "showings_missed", "showingsMissed", "missedShowings",
                 default=None)
        ) or max(scheduled - completed, 0),
        "applications_count": _safe_int(
            _get(data, "applications_submitted", "applications",
                 "applicationsCount", "applications_count",
                 "totalApplications", default=0)
        ) or 0,
    }

    extra = {
        "days_on_market": _safe_int(
            _get(data, "days_on_market", "daysOnMarket", "dom", default=None)
        ),
    }

    return summary, extra


# ---------------------------------------------------------------------------
# Webhook mappers
# ---------------------------------------------------------------------------

def _normalize_event_type(raw_value):
    """
    Normalize a RentEngine event type string to our EVENT_TYPE_CHOICES key.

    Examples:
        "Showing Complete"  -> "showing_complete"
        "Application Sent to Prospect" -> "application_sent_to_prospect"
        "Prescreen Rejected - Credit" -> "prescreen_rejected_credit"
        "HOA Application Sent To Prospect" -> "hoa_application_sent"
    """
    if not raw_value:
        return ""
    # Lowercase, replace hyphens/dashes with spaces, collapse whitespace
    s = str(raw_value).strip().lower()
    s = re.sub(r"[\-–—]+", " ", s)
    # Strip dangling prepositions/articles for matching
    s = re.sub(r"\s+", "_", s.strip())
    # Remove non-alphanumeric except underscores
    s = re.sub(r"[^a-z0-9_]", "", s)

    # Direct lookup against known choices
    from leasing.models import LeasingEvent
    valid_keys = {choice[0] for choice in LeasingEvent.EVENT_TYPE_CHOICES}
    if s in valid_keys:
        return s

    # Try common abbreviations/aliases
    aliases = {
        "hoa_application_sent_to_prospect": "hoa_application_sent",
        "contacted_awaiting_info": "contacted_awaiting_information",
        "showing_cancelled": "showing_canceled",
    }
    if s in aliases:
        return aliases[s]

    logger.warning("Unknown RentEngine event type: %r (normalized: %r)", raw_value, s)
    return s


def map_prospect_webhook(record):
    """
    Map a RentEngine prospect webhook record to Prospect model fields.

    Returns a dict of defaults for Prospect.objects.update_or_create().
    """
    # Name: try "name", then "first_name" + "last_name", then "full_name"
    name = str(_get(record, "name", "full_name", default="") or "")
    if not name:
        first = str(record.get("first_name", "") or "")
        last = str(record.get("last_name", "") or "")
        name = f"{first} {last}".strip()

    return {
        "name": name,
        "email": str(_get(record, "email", default="") or ""),
        "phone": str(_get(record, "phone", "phone_number", default="") or ""),
        "source": str(_get(record, "source", "lead_source", default="") or ""),
        "status": str(_get(record, "status", default="") or ""),
        "source_created_at": _safe_datetime(
            _get(record, "created_at", "createdAt", default=None)
        ),
        "raw_data": record,
    }


def map_leasing_event_webhook(record):
    """
    Map a RentEngine leasing event webhook record to LeasingEvent model fields.

    Returns a dict of defaults for LeasingEvent.objects.update_or_create().
    """
    event_type = _normalize_event_type(
        _get(record, "event_type", "eventType", "type", "status", default="")
    )

    event_timestamp = _safe_datetime(
        _get(record, "created_at", "createdAt", "event_timestamp", "eventTimestamp", default=None)
    )

    event_date = None
    if event_timestamp:
        event_date = event_timestamp.date()
    else:
        event_date = _safe_date(
            _get(record, "event_date", "eventDate", "date", default=None)
        )

    # Build context from extra fields not covered by explicit columns
    known_keys = {
        "id", "prospect_id", "unit_id", "event_type", "eventType", "type",
        "status", "created_at", "createdAt", "event_timestamp", "eventTimestamp",
        "event_date", "eventDate", "date",
    }
    context = {k: v for k, v in record.items() if k not in known_keys}

    return {
        "event_type": event_type,
        "event_timestamp": event_timestamp,
        "event_date": event_date,
        "context": context,
        "raw_data": record,
    }

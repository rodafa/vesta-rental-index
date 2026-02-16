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

    address_line_1 = str(_get(
        data, "address", "street_address", "addressLine1", "address_line_1", default=""
    ))
    city = str(_get(data, "city", default=""))
    state = str(_get(data, "state", "stateCode", default=""))[:2]
    postal_code = str(_get(data, "zip", "zipCode", "postal_code", "postalCode", default=""))

    defaults = {
        "name": str(_get(data, "name", "unitName", "unit_name", default="")),
        "address_line_1": address_line_1,
        "address_line_2": str(_get(data, "address2", "addressLine2", "address_line_2", default="")),
        "city": city,
        "state": state,
        "postal_code": postal_code,
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

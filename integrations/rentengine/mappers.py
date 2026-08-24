"""
Pure mapping functions for RentEngine API responses.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

# Matches a trailing integer after the last slash
_LISTING_ID_RE = re.compile(r"/(\d+)$")


def parse_rentvine_listing_id(extracted_from):
    """
    Extract the RentVine listing ID from a RentEngine extracted_from URL.

    Example: "https://vestapm.rentvine.com/api/public/listings/180" -> 180
    Returns None on any unexpected shape.
    """
    if not extracted_from or not isinstance(extracted_from, str):
        return None
    match = _LISTING_ID_RE.search(extracted_from.strip())
    if match:
        return int(match.group(1))
    return None


def _safe_decimal(value):
    """Convert to Decimal or return None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def map_unit(raw):
    """
    Normalize a RentEngine unit dict into a flat dict of the fields we use.
    """
    address = raw.get("address") or {}
    return {
        "rentengine_id": raw.get("id"),
        "listing_id": parse_rentvine_listing_id(raw.get("extracted_from")),
        "extracted_from": raw.get("extracted_from") or "",
        "formatted_address": address.get("formatted_address", ""),
        "city": address.get("city", ""),
        "zip_code": address.get("zip_code", ""),
        "status": raw.get("status", ""),
        # RentEngine's "target_rental_rate" is the ADVERTISED listing price.
        # This is NOT the same as core.Unit.target_rental_rate, which comes
        # from RentVine and represents the owner's target rent — a different
        # number from a different system with a confusingly similar name.
        "advertised_rent": _safe_decimal(raw.get("target_rental_rate")),
    }


def parse_timestamp(value):
    """
    Parse an ISO 8601 timestamp string into a timezone-aware datetime.

    Handles strings with and without timezone suffixes.
    Assumes UTC when no timezone is present.
    Returns None on any unparseable input — never raises.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass
    return None


def _safe_int(value):
    """Convert to int or return None."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def map_prospect(raw):
    """
    Normalize a RentEngine prospect dict into Prospect model field values.

    Returns a dict of model fields plus 'unit_of_interest' (int or None)
    for the caller to resolve to a core.Unit FK.
    """
    return {
        "name": str(raw.get("name") or ""),
        "email": str(raw.get("email") or ""),
        "phone": str(raw.get("phone") or ""),
        "status": str(raw.get("status") or ""),
        "source": str(raw.get("source") or ""),
        "prospect_type": str(raw.get("prospect_type") or ""),
        "source_created_at": parse_timestamp(raw.get("created_at")),
        "source_updated_at": parse_timestamp(raw.get("updated_at")),
        "raw_data": raw,
        "unit_of_interest": _safe_int(raw.get("unit_of_interest")),
    }


def map_leasing_event(raw):
    """
    Normalize a RentEngine leasing event dict into LeasingEvent model field values.

    Returns a dict of model fields plus 'unit_of_interest' (int or None)
    and 'prospect_id' (int or None) for the caller to resolve.
    event_date is derived from event_timestamp.
    """
    event_timestamp = parse_timestamp(raw.get("created_at"))
    event_date = event_timestamp.date() if event_timestamp else None

    return {
        "event_type": str(raw.get("event_type") or ""),
        "event_timestamp": event_timestamp,
        "event_date": event_date,
        "source": str(raw.get("source") or ""),
        "created_by": str(raw.get("created_by") or ""),
        "notes": str(raw.get("notes") or ""),
        "planned_date_time": parse_timestamp(raw.get("planned_date_time")),
        "next_follow_up": parse_timestamp(raw.get("next_follow_up")),
        "property_address": str(raw.get("property_address") or ""),
        "context": raw.get("context") if isinstance(raw.get("context"), dict) else {},
        "raw_data": raw,
        "unit_of_interest": _safe_int(raw.get("unit_of_interest")),
        # API response uses "prospect_id"; webhook payload uses "prospect".
        # Try the canonical name first, fall back to the webhook variant.
        "prospect_id": _safe_int(
            raw.get("prospect_id") if raw.get("prospect_id") is not None
            else raw.get("prospect")
        ),
    }


def build_unit_marked_available_dates(rows):
    """
    Map /reporting/units rows to {unit_id: date_marked_available_date}.

    Parses the date portion only (first 10 characters) — no timezone
    conversion, so DOM arithmetic stays correct.

    Skips rows with a null or unparseable date_marked_available.
    """
    result = {}
    for row in rows:
        uid = row.get("unit_id")
        if uid is None:
            continue
        try:
            uid = int(uid)
        except (ValueError, TypeError):
            continue

        raw = row.get("date_marked_available")
        if not raw:
            continue
        try:
            result[uid] = date.fromisoformat(str(raw)[:10])
        except (ValueError, TypeError):
            continue

    return result

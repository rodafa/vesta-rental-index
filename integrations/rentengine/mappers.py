"""
Pure mapping functions for RentEngine API responses.
"""

import re

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
    }

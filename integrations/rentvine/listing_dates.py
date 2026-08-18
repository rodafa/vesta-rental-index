"""
Pure helper: extract active-listing advertisedDate from RentVine listings.

No DB access, no HTTP — takes raw API response, returns a dict.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def build_unit_advertised_dates(rv_listings):
    """
    Parse RentVine listings into {rentvine_unit_id: advertised_date}.

    Only active listings (listing.isActive == "1") with a parseable
    advertisedDate are included.  If a unit has two active listings,
    the latest advertisedDate wins and a warning is logged.

    advertisedDate arrives as "YYYY-MM-DD" or "YYYY-MM-DDT00:00:00".
    We take the first 10 characters and parse as a date — no timezone
    conversion, so DOM arithmetic stays correct.
    """
    result = {}
    active_count = 0

    for row in rv_listings:
        listing = row.get("listing") or {}
        if str(listing.get("isActive")) != "1":
            continue

        active_count += 1

        raw_date = listing.get("advertisedDate")
        if not raw_date:
            continue

        try:
            ad = date.fromisoformat(str(raw_date)[:10])
        except (ValueError, TypeError):
            continue

        unit_id_raw = listing.get("unitID") or (row.get("unit") or {}).get("unitID")
        if unit_id_raw is None:
            continue

        try:
            unit_id = int(unit_id_raw)
        except (ValueError, TypeError):
            continue

        existing = result.get(unit_id)
        if existing is not None and existing >= ad:
            logger.warning(
                "rentvine_duplicate_active_listing",
                extra={
                    "rentvine_unit_id": unit_id,
                    "kept": str(existing),
                    "discarded": str(ad),
                },
            )
            continue

        if existing is not None:
            logger.warning(
                "rentvine_duplicate_active_listing",
                extra={
                    "rentvine_unit_id": unit_id,
                    "kept": str(ad),
                    "discarded": str(existing),
                },
            )

        result[unit_id] = ad

    parsed_count = len(result)
    logger.info(
        "rentvine_listing_dates_built",
        extra={
            "total_listings": len(rv_listings),
            "active_listings": active_count,
            "with_advertised_date": parsed_count,
        },
    )

    return result

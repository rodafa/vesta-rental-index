"""
Pure resolution logic for RentEngine unit reconciliation and sync.

No HTTP calls, no DB access — takes data in, returns results.
"""

from dataclasses import dataclass, field


@dataclass
class ResolutionResult:
    """Result of resolving RentEngine units against RentVine/core.Unit data."""

    matched: list = field(default_factory=list)
    unmatched_no_extracted: list = field(default_factory=list)
    unmatched_not_parseable: list = field(default_factory=list)
    unmatched_listing_missing: list = field(default_factory=list)
    unmatched_unit_missing: list = field(default_factory=list)


def build_listing_to_unit_map(rv_listings):
    """
    Build a mapping of RentVine listing ID -> RentVine unit ID from raw
    /properties/listings/search response rows.

    Each row is nested: row["listing"]["propertyListingID"] and
    row["listing"]["unitID"] (both strings).

    Returns dict[int, int]. Skips malformed rows silently.
    """
    result = {}
    for row in rv_listings:
        inner = row.get("listing") or {} if isinstance(row, dict) else {}
        lid = inner.get("propertyListingID")
        uid = inner.get("unitID")
        if lid is not None and uid is not None:
            try:
                result[int(lid)] = int(uid)
            except (ValueError, TypeError):
                pass
    return result


def resolve_rentengine_units(re_units, listing_to_rv_unit, rv_id_to_db_id):
    """
    Resolve mapped RentEngine units against RentVine and core.Unit lookups.

    Args:
        re_units: list of dicts from mappers.map_unit().
        listing_to_rv_unit: dict[int, int] — listing ID -> RV unit ID.
        rv_id_to_db_id: dict[int, int] — RV unit ID -> core.Unit.id.

    Returns a ResolutionResult with matched pairs and unmatched groups.
    """
    result = ResolutionResult()

    for reu in re_units:
        re_id = reu["rentengine_id"]
        addr = reu["formatted_address"]
        listing_id = reu["listing_id"]
        extracted_from = reu["extracted_from"]

        if not extracted_from:
            result.unmatched_no_extracted.append(reu)
            continue

        if listing_id is None:
            result.unmatched_not_parseable.append(reu)
            continue

        rv_unit_id = listing_to_rv_unit.get(listing_id)
        if rv_unit_id is None:
            result.unmatched_listing_missing.append(reu)
            continue

        db_unit_id = rv_id_to_db_id.get(rv_unit_id)
        if db_unit_id is None:
            result.unmatched_unit_missing.append(
                {**reu, "rv_unit_id": rv_unit_id}
            )
            continue

        result.matched.append({
            "rentengine_id": re_id,
            "rentvine_unit_id": rv_unit_id,
            "db_unit_id": db_unit_id,
            "address": addr,
        })

    return result

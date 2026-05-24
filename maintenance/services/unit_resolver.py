"""
PM↔local address matching and meld-to-unit resolution.

No deterministic IDs exist in PM API (integration_partner_id is None on all records).
Address matching with audit rigor is the only option.
"""

import logging
import re

from properties.models import Property, Unit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------

_ABBREVIATIONS = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "road": "rd",
    "court": "ct",
    "place": "pl",
    "circle": "cir",
    "terrace": "ter",
    "trail": "trl",
    "highway": "hwy",
    "parkway": "pkwy",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
    "apartment": "apt",
    "suite": "ste",
    "unit": "unit",
}


def normalize_address(address):
    """Normalize an address for matching: lowercase, strip, standardize abbreviations."""
    if not address:
        return ""
    text = address.lower().strip()
    # Remove punctuation except hyphens
    text = re.sub(r"[.,#]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Replace common abbreviations
    words = text.split()
    normalized = []
    for word in words:
        normalized.append(_ABBREVIATIONS.get(word, word))
    return " ".join(normalized)


def build_property_key(line_1, city, state, postal_code):
    """Build a normalized key from address components."""
    parts = [
        normalize_address(line_1),
        normalize_address(city),
        (state or "").lower().strip(),
        (postal_code or "").strip()[:5],
    ]
    return "|".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Cross-reference builder
# ---------------------------------------------------------------------------


def build_crossref(client):
    """
    Fetch PM properties and units, match to local records by address.

    Args:
        client: PropertyMeldClient instance

    Returns:
        dict with keys: properties_matched, properties_total, units_matched,
        units_total, audit_log (list of dicts)
    """
    audit_log = []

    # Fetch PM properties
    pm_properties = client.get_all("/property/")
    logger.info("Fetched %d PM properties", len(pm_properties))

    # Fetch PM units
    pm_units = client.get_all("/unit/")
    logger.info("Fetched %d PM units", len(pm_units))

    # Build local property index by normalized address
    local_properties = Property.objects.filter(is_active=True)
    prop_index = {}
    for prop in local_properties:
        key = build_property_key(
            prop.address_line_1, prop.city, prop.state, prop.postal_code
        )
        if key:
            prop_index[key] = prop

    # Match PM properties → local properties
    pm_prop_to_local = {}
    properties_matched = 0

    for pm_prop in pm_properties:
        pm_id = pm_prop.get("id")
        if not pm_id:
            continue

        # PM API returns address as top-level flat fields
        line_1 = pm_prop.get("line_1") or ""
        city = pm_prop.get("city") or ""
        state = pm_prop.get("county_province") or ""  # PM uses county_province for state
        zip_code = pm_prop.get("postcode") or ""

        key = build_property_key(line_1, city, state, zip_code)
        local_prop = prop_index.get(key) if key else None

        if local_prop:
            pm_prop_to_local[pm_id] = local_prop
            properties_matched += 1
            # Write the PM ID to the local property
            if local_prop.property_meld_id != pm_id:
                Property.objects.filter(pk=local_prop.pk).update(property_meld_id=pm_id)
            audit_log.append({
                "type": "property",
                "pm_id": pm_id,
                "local_id": local_prop.pk,
                "confidence": "high",
                "pm_address": line_1,
                "local_address": local_prop.address_line_1,
            })
        else:
            audit_log.append({
                "type": "property",
                "pm_id": pm_id,
                "local_id": None,
                "confidence": "no_match",
                "pm_address": line_1,
                "local_address": None,
            })

    # Match PM units → local units
    units_matched = 0

    for pm_unit in pm_units:
        pm_unit_id = pm_unit.get("id")
        if not pm_unit_id:
            continue

        pm_prop_id = pm_unit.get("property_id") or pm_unit.get("prop")
        local_prop = pm_prop_to_local.get(pm_prop_id) if pm_prop_id else None

        matched_unit = None

        if local_prop:
            # PM unit "unit" field = unit name/designator
            pm_unit_name = (pm_unit.get("unit") or "").strip()

            local_units = list(local_prop.units.filter(is_active=True))

            if len(local_units) == 1:
                # Single unit property — auto-match
                matched_unit = local_units[0]
            elif local_units:
                # Multi-unit: try matching by name or address_line_2
                norm_pm_name = normalize_address(pm_unit_name)
                for lu in local_units:
                    lu_name = normalize_address(lu.name)
                    lu_line2 = normalize_address(lu.address_line_2)
                    if norm_pm_name and (
                        norm_pm_name == lu_name
                        or norm_pm_name == lu_line2
                        or norm_pm_name in lu_name
                        or lu_name in norm_pm_name
                    ):
                        matched_unit = lu
                        break

        if matched_unit:
            units_matched += 1
            if matched_unit.property_meld_id != pm_unit_id:
                Unit.objects.filter(pk=matched_unit.pk).update(
                    property_meld_id=pm_unit_id
                )
            audit_log.append({
                "type": "unit",
                "pm_id": pm_unit_id,
                "local_id": matched_unit.pk,
                "confidence": "high",
                "pm_name": pm_unit.get("unit", ""),
                "local_name": matched_unit.name,
            })
        else:
            audit_log.append({
                "type": "unit",
                "pm_id": pm_unit_id,
                "local_id": None,
                "confidence": "no_match",
                "pm_name": pm_unit.get("unit", ""),
                "local_name": None,
            })

    return {
        "properties_matched": properties_matched,
        "properties_total": len(pm_properties),
        "units_matched": units_matched,
        "units_total": len(pm_units),
        "audit_log": audit_log,
    }


# ---------------------------------------------------------------------------
# Meld → Unit resolution (runs after cross-ref is populated)
# ---------------------------------------------------------------------------


def resolve_meld_unit(meld):
    """
    Resolve a Meld to a local Unit using PM cross-reference IDs.

    Returns the matched Unit or None.
    """
    # Path 1: Meld has PM unit ID → direct lookup via Unit.property_meld_id
    if meld.unit_ref:
        try:
            unit = Unit.objects.filter(property_meld_id=int(meld.unit_ref)).first()
            if unit:
                return unit
        except (ValueError, TypeError):
            pass

    # Path 2: Meld has PM prop ID, no unit → via Property.property_meld_id
    if meld.property_meld_property_id:
        try:
            prop = Property.objects.filter(
                property_meld_id=int(meld.property_meld_property_id)
            ).first()
            if prop:
                units = list(prop.units.filter(is_active=True))
                if len(units) == 1:
                    return units[0]
                # Multi-unit property with no unit specified — can't resolve
                return None
        except (ValueError, TypeError):
            pass

    return None


def resolve_all_melds(queryset=None):
    """
    Run unit resolution on all melds (or a filtered queryset).
    Returns dict: {resolved, unresolved, already_resolved}.
    """
    from maintenance.models import Meld

    if queryset is None:
        queryset = Meld.objects.all()

    resolved = 0
    unresolved = 0
    already_resolved = 0

    for meld in queryset.iterator():
        if meld.unit_fk_id:
            already_resolved += 1
            continue

        unit = resolve_meld_unit(meld)
        if unit:
            meld.unit_fk = unit
            meld.save(update_fields=["unit_fk", "updated_at"])
            resolved += 1
        else:
            unresolved += 1

    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "already_resolved": already_resolved,
    }

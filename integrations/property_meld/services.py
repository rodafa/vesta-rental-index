"""
PropertyMeld sync service and PM↔local unit resolution.

MeldSyncService: pulls melds from the PM API and upserts into maintenance.Meld.
Unit resolver: matches PM properties/units to core.Property/Unit by address,
then resolves each Meld to a core.Unit via the cross-reference IDs.
"""

import logging
import re

from django.utils import timezone

from core.models import Property, Unit
from integrations.models import APISyncLog
from maintenance.models import Meld

from .client import PropertyMeldClient
from .mappers import map_meld

logger = logging.getLogger(__name__)


# ============================================================================
# Meld sync
# ============================================================================


class MeldSyncService:
    """Fetch all melds from PM and upsert on property_meld_id."""

    source = "property_meld"
    endpoint = "meld"
    sync_type = "full"

    def __init__(self, client=None):
        self.client = client or PropertyMeldClient()

    def _create_log(self):
        return APISyncLog.objects.create(
            source=self.source,
            endpoint=self.endpoint,
            sync_type=self.sync_type,
            status="started",
        )

    def _complete_log(self, log, *, created, updated, fetched, errors=None):
        log.status = "completed" if not errors else "partial"
        log.records_fetched = fetched
        log.records_created = created
        log.records_updated = updated
        if errors:
            log.error_message = "\n".join(errors[:50])
        log.completed_at = timezone.now()
        log.save()

    def _fail_log(self, log, error_message):
        log.status = "failed"
        log.error_message = str(error_message)[:2000]
        log.completed_at = timezone.now()
        log.save()

    def sync(self, dry_run=False):
        """
        Fetch all melds from /meld/, map them, and upsert on property_meld_id.
        Returns {"fetched", "created", "updated", "errors"}.
        """
        log = self._create_log()
        try:
            records = self.client.get_all("/meld/")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                property_meld_id, defaults = map_meld(record)

                if dry_run:
                    logger.info(
                        "property_meld_sync_dry_run",
                        extra={
                            "property_meld_id": property_meld_id,
                            "brief": defaults.get("brief_description", "")[:60],
                            "status": defaults.get("status"),
                            "priority": defaults.get("priority"),
                            "vendor": defaults.get("assigned_vendor_name"),
                        },
                    )
                    continue

                _, was_created = Meld.objects.update_or_create(
                    property_meld_id=property_meld_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

            except Exception as exc:
                msg = f"Error syncing meld record: {exc}"
                logger.error(msg)
                errors.append(msg)

        if not dry_run:
            self._complete_log(
                log,
                created=created_count,
                updated=updated_count,
                fetched=len(records),
                errors=errors,
            )
        else:
            log.status = "completed"
            log.records_fetched = len(records)
            log.completed_at = timezone.now()
            log.save()

        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


# ============================================================================
# Address normalisation (for PM↔local cross-reference)
# ============================================================================

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
    text = re.sub(r"[.,#]", "", text)
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def build_property_key(line_1, city, state, postal_code):
    """Build a normalized key from address components."""
    parts = [
        normalize_address(line_1),
        normalize_address(city),
        (state or "").lower().strip(),
        (postal_code or "").strip()[:5],
    ]
    return "|".join(p for p in parts if p)


# ============================================================================
# Cross-reference builder
# ============================================================================


def build_crossref(client):
    """
    Fetch PM properties and units, match to local records by address.

    Populates Property.property_meld_id and Unit.property_meld_id.
    Returns dict with keys: properties_matched, properties_total,
    units_matched, units_total, audit_log.
    """
    audit_log = []

    pm_properties = client.get_all("/property/")
    logger.info(
        "property_meld_crossref_properties_fetched",
        extra={"count": len(pm_properties)},
    )

    pm_units = client.get_all("/unit/")
    logger.info(
        "property_meld_crossref_units_fetched",
        extra={"count": len(pm_units)},
    )

    # Build local property index by normalized address
    local_properties = Property.objects.filter(is_active=True)
    prop_index = {}
    for prop in local_properties:
        key = build_property_key(
            prop.address_line_1, prop.city, prop.state, prop.postal_code
        )
        if key:
            prop_index[key] = prop

    # Match PM properties -> local properties
    pm_prop_to_local = {}
    properties_matched = 0

    for pm_prop in pm_properties:
        pm_id = pm_prop.get("id")
        if not pm_id:
            continue

        line_1 = pm_prop.get("line_1") or ""
        city = pm_prop.get("city") or ""
        state = pm_prop.get("county_province") or ""  # PM uses county_province
        zip_code = pm_prop.get("postcode") or ""

        key = build_property_key(line_1, city, state, zip_code)
        local_prop = prop_index.get(key) if key else None

        if local_prop:
            pm_prop_to_local[pm_id] = local_prop
            properties_matched += 1
            if local_prop.property_meld_id != pm_id:
                Property.objects.filter(pk=local_prop.pk).update(
                    property_meld_id=pm_id
                )
            audit_log.append(
                {
                    "type": "property",
                    "pm_id": pm_id,
                    "local_id": local_prop.pk,
                    "confidence": "high",
                    "pm_address": line_1,
                    "local_address": local_prop.address_line_1,
                }
            )
        else:
            audit_log.append(
                {
                    "type": "property",
                    "pm_id": pm_id,
                    "local_id": None,
                    "confidence": "no_match",
                    "pm_address": line_1,
                    "local_address": None,
                }
            )

    # Match PM units -> local units
    units_matched = 0

    for pm_unit in pm_units:
        pm_unit_id = pm_unit.get("id")
        if not pm_unit_id:
            continue

        pm_prop_id = pm_unit.get("property_id") or pm_unit.get("prop")
        local_prop = pm_prop_to_local.get(pm_prop_id) if pm_prop_id else None

        matched_unit = None

        if local_prop:
            pm_unit_name = (pm_unit.get("unit") or "").strip()
            local_units = list(local_prop.units.filter(is_active=True))

            if len(local_units) == 1:
                matched_unit = local_units[0]
            elif local_units:
                norm_pm_name = normalize_address(pm_unit_name)
                for lu in local_units:
                    lu_name = normalize_address(lu.name)
                    lu_unit_number = normalize_address(lu.unit_number)
                    if norm_pm_name and (
                        norm_pm_name == lu_name
                        or norm_pm_name == lu_unit_number
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
            audit_log.append(
                {
                    "type": "unit",
                    "pm_id": pm_unit_id,
                    "local_id": matched_unit.pk,
                    "confidence": "high",
                    "pm_name": pm_unit.get("unit", ""),
                    "local_name": matched_unit.name,
                }
            )
        else:
            audit_log.append(
                {
                    "type": "unit",
                    "pm_id": pm_unit_id,
                    "local_id": None,
                    "confidence": "no_match",
                    "pm_name": pm_unit.get("unit", ""),
                    "local_name": None,
                }
            )

    return {
        "properties_matched": properties_matched,
        "properties_total": len(pm_properties),
        "units_matched": units_matched,
        "units_total": len(pm_units),
        "audit_log": audit_log,
    }


# ============================================================================
# Meld -> Unit resolution (runs after cross-ref is populated)
# ============================================================================


def _lookup_property(meld):
    """Resolve a Meld to a local Property via PM cross-reference ID."""
    if meld.property_meld_property_id:
        try:
            return Property.objects.filter(
                property_meld_id=int(meld.property_meld_property_id)
            ).first()
        except (ValueError, TypeError):
            pass
    return None


def resolve_meld_unit(meld):
    """
    Resolve a Meld to a local Unit using PM cross-reference IDs.
    Returns the matched Unit or None.
    """
    # Path 1: Meld has PM unit ID -> direct lookup via Unit.property_meld_id
    if meld.unit_ref:
        try:
            unit = Unit.objects.filter(
                property_meld_id=int(meld.unit_ref)
            ).first()
            if unit:
                return unit
        except (ValueError, TypeError):
            pass

    # Path 2: Meld has PM prop ID, no unit -> via Property.property_meld_id
    prop = _lookup_property(meld)
    if prop:
        units = list(prop.units.filter(is_active=True))
        if len(units) == 1:
            return units[0]

    return None


def resolve_all_melds(queryset=None):
    """
    Run resolution on all melds (or a filtered queryset).

    Sets both Meld.property (whenever the PM property matches core) and
    Meld.unit (when the specific unit can be determined). Property-level
    resolution always succeeds when the PM property is in core, even for
    multi-unit properties where the unit can't be pinpointed.

    Returns dict with unit-level and property-level counts.
    """
    if queryset is None:
        queryset = Meld.objects.all()

    unit_resolved = 0
    unit_unresolved = 0
    unit_already = 0
    prop_resolved = 0
    prop_unresolved = 0
    prop_already = 0

    for meld in queryset.iterator():
        changed_fields = []

        # --- Property resolution ---
        if meld.property_id:
            prop_already += 1
        else:
            prop = _lookup_property(meld)
            if prop:
                meld.property = prop
                changed_fields.append("property")
                prop_resolved += 1
            else:
                prop_unresolved += 1

        # --- Unit resolution ---
        if meld.unit_id:
            unit_already += 1
            # Backfill property from unit if missing
            if not meld.property_id:
                meld.property = meld.unit.property
                changed_fields.append("property")
                prop_resolved += 1
                prop_unresolved -= 1  # undo the count from above
        else:
            unit = resolve_meld_unit(meld)
            if unit:
                meld.unit = unit
                changed_fields.append("unit")
                unit_resolved += 1
                # Also set property from the resolved unit
                if not meld.property_id:
                    meld.property = unit.property
                    changed_fields.append("property")
                    prop_resolved += 1
                    prop_unresolved -= 1
            else:
                unit_unresolved += 1

        if changed_fields:
            meld.save(update_fields=changed_fields + ["updated_at"])

    return {
        "unit_resolved": unit_resolved,
        "unit_unresolved": unit_unresolved,
        "unit_already": unit_already,
        "prop_resolved": prop_resolved,
        "prop_unresolved": prop_unresolved,
        "prop_already": prop_already,
    }

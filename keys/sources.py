"""
Reusable fetch functions for key/lockbox external data sources.

No DB writes. Returns raw data dicts for callers to process.
Errors are never swallowed — they are recorded in fetch_errors dicts.
"""

import logging

from integrations.rentengine.client import RentEngineClient
from integrations.rentvine.client import RentvineClient
from keys.services import extract_key_notes
from utilities.services import fetch_notes

logger = logging.getLogger(__name__)


def fetch_rentvine_key_notes(db_units):
    """Fetch Rentvine key/lockbox notes for all units and their properties.

    Property notes are fetched once per property (object_type 6).
    Unit notes are fetched per unit (object_type 7).
    Both are filtered through extract_key_notes for keyword relevance.

    Returns (unit_notes, property_notes, fetch_errors).
      unit_notes:     dict[rentvine_unit_id     → list[{note_id, date, text}]]
      property_notes: dict[rentvine_property_id → list[{note_id, date, text}]]
      fetch_errors:   dict[rentvine_unit_id     → list[str]]
    """
    rv_client = RentvineClient()
    unit_notes = {}
    property_notes = {}
    fetch_errors = {}

    # --- Property-level notes (once per property) ---
    seen_property_ids = set()
    for unit in db_units:
        prop_id = unit.property.rentvine_id
        if prop_id is None or prop_id in seen_property_ids:
            continue
        seen_property_ids.add(prop_id)
        try:
            raw = fetch_notes(rv_client, 6, prop_id)
            notes = extract_key_notes(raw)
            if notes:
                property_notes[prop_id] = notes
        except Exception as exc:
            logger.error(
                "rentvine_property_notes_error",
                extra={"rentvine_property_id": prop_id, "error": str(exc)},
            )
            for u in db_units:
                if u.property.rentvine_id == prop_id:
                    fetch_errors.setdefault(u.rentvine_id, []).append(
                        f"property notes (rv {prop_id}): {exc}"
                    )

    # --- Unit-level notes ---
    for unit in db_units:
        uid = unit.rentvine_id
        try:
            raw = fetch_notes(rv_client, 7, uid)
            notes = extract_key_notes(raw)
            if notes:
                unit_notes[uid] = notes
        except Exception as exc:
            logger.error(
                "rentvine_unit_notes_error",
                extra={"rentvine_unit_id": uid, "error": str(exc)},
            )
            fetch_errors.setdefault(uid, []).append(
                f"unit notes (rv {uid}): {exc}"
            )

    return unit_notes, property_notes, fetch_errors


def fetch_rentengine_index(db_units):
    """Fetch all RentEngine units and index by str(id).

    Flags DB units whose rentengine_id is present but not found in the
    RentEngine response.

    Returns (re_index, fetch_errors).
      re_index:     dict[str(rentengine_id) → raw RE unit dict]
      fetch_errors: dict[rentvine_unit_id   → list[str]]
    """
    re_index = {}
    fetch_errors = {}

    try:
        re_client = RentEngineClient()
        re_raw_units = re_client.get_all("units")
        for ru in re_raw_units:
            re_id = ru.get("id")
            if re_id is not None:
                re_index[str(re_id)] = ru
        logger.info(
            "rentengine_units_indexed",
            extra={"count": len(re_index)},
        )
    except Exception as exc:
        logger.error(
            "rentengine_units_error",
            extra={"error": str(exc)},
        )
        for unit in db_units:
            if unit.rentengine_id:
                fetch_errors.setdefault(unit.rentvine_id, []).append(
                    f"RentEngine units fetch: {exc}"
                )
        # Return early — cannot do the missing-id check without data.
        return re_index, fetch_errors

    # Flag units whose rentengine_id is missing from the RE data.
    for unit in db_units:
        if unit.rentengine_id and str(unit.rentengine_id) not in re_index:
            fetch_errors.setdefault(unit.rentvine_id, []).append(
                f"rentengine_id {unit.rentengine_id} not found "
                f"in RentEngine units"
            )

    return re_index, fetch_errors

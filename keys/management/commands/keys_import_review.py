"""
Management command: read key/lockbox sources, produce a review CSV.

No DB writes.  Read-only use of core.models Property and Unit.
"""

import csv
import logging
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentvine.client import RentvineClient
from keys.services import (
    build_review_rows,
    extract_key_notes,
    parse_key_box_csv,
)
from utilities.services import fetch_notes

logger = logging.getLogger(__name__)


def _parse_box_or_fail(path, box_letter):
    """Parse a key box CSV; raise CommandError if it yields no property rows."""
    rows = parse_key_box_csv(path, box_letter)
    property_rows = [r for r in rows if r["row_type"] == "property"]
    if not property_rows:
        raise CommandError(
            f"Box {box_letter} file '{path}': no property rows found. "
            f"Check that the 'Tag Number' header exists and the file "
            f"is not empty."
        )
    return rows


class Command(BaseCommand):
    help = "Read key/lockbox sources and produce a review CSV. No DB writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--box-a", required=True, type=str,
            help="Path to key box A CSV export.",
        )
        parser.add_argument(
            "--box-b", required=True, type=str,
            help="Path to key box B CSV export.",
        )
        parser.add_argument(
            "--out", type=str, default=None,
            help=(
                "Output CSV path. "
                "Default: keys/import_data/key_review_YYYY-MM-DD.csv"
            ),
        )
        parser.add_argument(
            "--property", type=str, default=None,
            help=(
                "Only process Rentvine properties whose address contains "
                "this text (case-insensitive).  Used for debug runs."
            ),
        )

    def handle(self, *args, **options):
        box_a_path = options["box_a"]
        box_b_path = options["box_b"]
        property_filter = options["property"]

        out_path = options["out"]
        if not out_path:
            out_dir = Path("keys/import_data")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(
                out_dir / f"key_review_{date.today().isoformat()}.csv"
            )

        # 1. Parse both CSVs.
        tag_rows = _parse_box_or_fail(box_a_path, "A")
        tag_rows += _parse_box_or_fail(box_b_path, "B")

        # 2. Load units from DB (Rentvine-linked only).
        units_qs = (
            Unit.objects
            .filter(rentvine_id__isnull=False)
            .select_related("property")
        )
        if property_filter:
            units_qs = units_qs.filter(
                property__address_line_1__icontains=property_filter,
            )
        db_units = list(units_qs)

        if not db_units:
            raise CommandError(
                "No Rentvine-linked units found"
                + (f" matching '{property_filter}'" if property_filter else "")
                + "."
            )

        # 3. Fetch Rentvine notes for each property and unit.
        rv_client = RentvineClient()
        rv_key_notes = {}   # rentvine_unit_id → list of note dicts
        fetch_errors = {}   # rentvine_unit_id → list of error strings

        # Fetch property-level notes once per property.
        seen_properties = {}  # rentvine_property_id → list of key notes
        for unit in db_units:
            prop_id = unit.property.rentvine_id
            if prop_id is None:
                continue
            if prop_id in seen_properties:
                continue
            try:
                raw = fetch_notes(rv_client, 6, prop_id)
                prop_notes = extract_key_notes(raw)
                for n in prop_notes:
                    n["_prefix"] = "[property] "
                seen_properties[prop_id] = prop_notes
            except Exception as exc:
                logger.error(
                    "rentvine_property_notes_error",
                    extra={
                        "rentvine_property_id": prop_id,
                        "error": str(exc),
                    },
                )
                seen_properties[prop_id] = []
                for u in db_units:
                    if u.property.rentvine_id == prop_id:
                        fetch_errors.setdefault(u.rentvine_id, []).append(
                            f"property notes (rv {prop_id}): {exc}"
                        )

        # Fetch unit-level notes and combine with property notes.
        for unit in db_units:
            uid = unit.rentvine_id
            unit_notes = []
            try:
                raw = fetch_notes(rv_client, 7, uid)
                unit_notes = extract_key_notes(raw)
            except Exception as exc:
                logger.error(
                    "rentvine_unit_notes_error",
                    extra={
                        "rentvine_unit_id": uid,
                        "error": str(exc),
                    },
                )
                fetch_errors.setdefault(uid, []).append(
                    f"unit notes (rv {uid}): {exc}"
                )

            prop_notes = seen_properties.get(
                unit.property.rentvine_id, []
            )
            rv_key_notes[uid] = unit_notes + prop_notes

        # 4. Fetch RentEngine units, index by str(id).
        re_index = {}
        re_fetch_ok = False
        try:
            re_client = RentEngineClient()
            re_raw_units = re_client.get_all("units")
            for ru in re_raw_units:
                re_id = ru.get("id")
                if re_id is not None:
                    re_index[str(re_id)] = ru
            re_fetch_ok = True
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

        # 4b. Flag units whose rentengine_id is missing from the RE data.
        if re_fetch_ok:
            for unit in db_units:
                if (unit.rentengine_id
                        and str(unit.rentengine_id) not in re_index):
                    fetch_errors.setdefault(unit.rentvine_id, []).append(
                        f"rentengine_id {unit.rentengine_id} not found "
                        f"in RentEngine units"
                    )

        # 5. Build review rows.
        rows, stats = build_review_rows(
            db_units, tag_rows, re_index, rv_key_notes,
            fetch_errors=fetch_errors,
            property_filter=property_filter,
        )

        # 6. Write CSV.
        if not rows:
            raise CommandError("No rows to write.")

        fieldnames = list(rows[0].keys())
        with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        # 7. Summary line — exact format required by spec.
        self.stdout.write(
            f"Done: "
            f"units={stats['units']} "
            f"tagged={stats['tagged']} "
            f"untagged={stats['untagged']} "
            f"multi_tag={stats['multi_tag']} "
            f"unmatched_tags={stats['unmatched_tags']} "
            f"with_rentengine_notes={stats['with_rentengine_notes']} "
            f"with_rentvine_key_notes={stats['with_rentvine_key_notes']} "
            f"errors={stats['errors']} "
            f"out={out_path}"
        )

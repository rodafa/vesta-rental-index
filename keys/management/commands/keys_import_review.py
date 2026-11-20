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
from keys.services import (
    build_review_rows,
    parse_box_or_fail,
)
from keys.sources import fetch_rentengine_index, fetch_rentvine_key_notes

logger = logging.getLogger(__name__)


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
        try:
            tag_rows = parse_box_or_fail(box_a_path, "A")
            tag_rows += parse_box_or_fail(box_b_path, "B")
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

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

        # 3. Fetch Rentvine notes.
        unit_notes, property_notes, fetch_errors = (
            fetch_rentvine_key_notes(db_units)
        )

        # Reassemble the merged rv_key_notes dict that build_review_rows
        # expects: unit_id → [unit notes..., property notes with prefix...].
        rv_key_notes = {}
        for unit in db_units:
            uid = unit.rentvine_id
            u_notes = unit_notes.get(uid, [])
            p_notes = property_notes.get(unit.property.rentvine_id, [])
            # Tag property notes with "[property] " prefix — same as before.
            tagged_p = [{**n, "_prefix": "[property] "} for n in p_notes]
            rv_key_notes[uid] = u_notes + tagged_p

        # 4. Fetch RentEngine units.
        re_index, re_errors = fetch_rentengine_index(db_units)
        for uid, errs in re_errors.items():
            fetch_errors.setdefault(uid, []).extend(errs)

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

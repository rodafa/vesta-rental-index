"""Management command: load key/lockbox data into Tag and AccessNote tables."""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Unit
from keys.loader import load_access_notes, load_tags
from keys.services import build_unit_address_index, parse_box_or_fail
from keys.sources import fetch_rentengine_index, fetch_rentvine_key_notes

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load key/lockbox sources into Tag and AccessNote. Use --commit to write."

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
            "--property", type=str, default=None,
            help=(
                "Only load tags/notes for properties whose address "
                "contains this text (case-insensitive)."
            ),
        )
        parser.add_argument(
            "--commit", action="store_true", default=False,
            help="Persist changes. Without this flag, runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        box_a_path = options["box_a"]
        box_b_path = options["box_b"]
        property_filter = options["property"]
        commit = options["commit"]

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

        # 3. Fetch external sources.
        unit_notes, property_notes, rv_errors = (
            fetch_rentvine_key_notes(db_units)
        )
        re_index, re_errors = fetch_rentengine_index(db_units)

        # Merge fetch errors.
        fetch_errors = {}
        for uid, errs in rv_errors.items():
            fetch_errors.setdefault(uid, []).extend(errs)
        for uid, errs in re_errors.items():
            fetch_errors.setdefault(uid, []).extend(errs)

        if fetch_errors:
            for uid, errs in sorted(fetch_errors.items()):
                for err in errs:
                    self.stderr.write(f"  fetch error (unit {uid}): {err}")
            raise CommandError(
                f"Fetch errors on {len(fetch_errors)} unit(s) — nothing written."
            )

        # 4. Build address index for tag matching.
        units_by_addr = build_unit_address_index(db_units)

        # 5. Load inside a single transaction; rollback for dry-run.
        mode = "commit" if commit else "dry-run"

        with transaction.atomic():
            tag_stats = load_tags(
                tag_rows, units_by_addr, property_filter=property_filter,
            )
            note_stats = load_access_notes(
                db_units, unit_notes, property_notes, re_index,
            )

            if not commit:
                transaction.set_rollback(True)

        # 6. Summary line.
        self.stdout.write(
            f"Done: "
            f"mode={mode} "
            f"tags_created={tag_stats['created']} "
            f"tags_updated={tag_stats['updated']} "
            f"tags_unchanged={tag_stats['unchanged']} "
            f"tags_skipped_edited={tag_stats['skipped_edited']} "
            f"orphan_tags={tag_stats['orphan']} "
            f"out_of_scope={tag_stats['out_of_scope']} "
            f"notes_created={note_stats['created']} "
            f"notes_updated={note_stats['updated']} "
            f"notes_unchanged={note_stats['unchanged']}"
        )

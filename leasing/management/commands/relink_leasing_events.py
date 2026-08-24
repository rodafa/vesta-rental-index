"""
Re-link LeasingEvent rows with missing prospect or unit FKs.

Pass 1: events with prospect=None — resolve prospect from raw_data,
        inherit unit from the prospect when the event has none.
Pass 2: events that have a prospect but unit=None — inherit unit from
        the prospect's unit_of_interest.

Never creates or deletes rows.
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand

from leasing.models import LeasingEvent, Prospect

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Re-link LeasingEvent rows: resolve missing prospect FKs from "
        "raw_data (pass 1), then inherit unit from prospect (pass 2)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=date.fromisoformat,
            required=True,
            help="Start date inclusive (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end",
            type=date.fromisoformat,
            required=True,
            help="End date inclusive (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Report only, do not write (default).",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            default=False,
            help="Actually write changes (overrides --dry-run).",
        )

    def handle(self, *args, **options):
        start = options["start"]
        end = options["end"]
        dry_run = not options["write"]

        mode = "DRY RUN" if dry_run else "LIVE"
        self.stdout.write(f"relink_leasing_events  [{mode}]")
        self.stdout.write(f"  Range: {start} to {end}")
        self.stdout.write("")

        # Build prospect lookup once for both passes
        prospect_map = {
            p.rentengine_id: p
            for p in Prospect.objects.select_related("unit").all()
        }

        # =============================================================
        # Pass 1: resolve prospect from raw_data where prospect IS NULL
        # =============================================================
        self.stdout.write("Pass 1: resolve missing prospect FKs from raw_data")

        p1_events = LeasingEvent.objects.filter(
            prospect__isnull=True,
            event_date__gte=start,
            event_date__lte=end,
        ).select_related("unit")

        p1_total = p1_events.count()
        self.stdout.write(f"  Events with prospect=None in range: {p1_total}")

        p1_relinked = 0
        p1_no_prospect_in_db = 0
        p1_prospect_found_no_unit = 0
        missing_prospect_ids = set()

        for event in p1_events.iterator():
            raw = event.raw_data or {}

            # Webhook payloads use "prospect"; API uses "prospect_id".
            prospect_re_id = raw.get("prospect_id") or raw.get("prospect")
            if prospect_re_id is None:
                p1_no_prospect_in_db += 1
                continue

            try:
                prospect_re_id = int(prospect_re_id)
            except (ValueError, TypeError):
                p1_no_prospect_in_db += 1
                continue

            prospect = prospect_map.get(prospect_re_id)
            if prospect is None:
                p1_no_prospect_in_db += 1
                missing_prospect_ids.add(prospect_re_id)
                continue

            event.prospect = prospect

            if event.unit_id is None and prospect.unit_id is not None:
                event.unit = prospect.unit
            elif event.unit_id is None and prospect.unit_id is None:
                p1_prospect_found_no_unit += 1

            if not dry_run:
                event.save(update_fields=["prospect", "unit"])

            p1_relinked += 1

        # =============================================================
        # Pass 2: inherit unit from prospect where unit IS NULL
        # =============================================================
        self.stdout.write("")
        self.stdout.write("Pass 2: inherit unit from prospect where unit is NULL")

        p2_events = LeasingEvent.objects.filter(
            unit__isnull=True,
            prospect__isnull=False,
            prospect__unit__isnull=False,
            event_date__gte=start,
            event_date__lte=end,
        ).select_related("prospect", "prospect__unit")

        p2_total = p2_events.count()
        self.stdout.write(
            f"  Events with unit=None, prospect has unit: {p2_total}"
        )

        p2_inherited = 0

        for event in p2_events.iterator():
            event.unit = event.prospect.unit

            if not dry_run:
                event.save(update_fields=["unit"])

            p2_inherited += 1

        # =============================================================
        # Remaining unlinked after both passes
        # =============================================================
        still_no_unit = LeasingEvent.objects.filter(
            unit__isnull=True,
            event_date__gte=start,
            event_date__lte=end,
        )
        still_no_unit_count = still_no_unit.count()
        still_no_unit_prospects = set(
            still_no_unit
            .filter(prospect__isnull=False)
            .values_list("prospect_id", flat=True)
            .distinct()
        )

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"RELINK SUMMARY  [{mode}]")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Pass 1 - prospect relinked       : {p1_relinked}")
        self.stdout.write(f"  Pass 2 - unit inherited          : {p2_inherited}")
        self.stdout.write(f"  Still unlinked (no prospect)     : {p1_no_prospect_in_db}")
        self.stdout.write(f"  Still unlinked (prospect, no unit): {p1_prospect_found_no_unit}")
        self.stdout.write("")
        self.stdout.write(
            f"  Events still with unit=None     : {still_no_unit_count}"
        )
        self.stdout.write(
            f"  Distinct prospects on those      : {len(still_no_unit_prospects)}"
        )

        if missing_prospect_ids:
            self.stdout.write("")
            self.stdout.write(
                f"  Missing prospect rentengine_ids ({len(missing_prospect_ids)}):"
            )
            for pid in sorted(missing_prospect_ids):
                self.stdout.write(f"    {pid}")

        self.stdout.write("=" * 60)

        if dry_run:
            self.stdout.write("")
            self.stdout.write("  No changes written. Re-run with --write to apply.")

        logger.info(
            "relink_leasing_events_complete",
            extra={
                "mode": mode,
                "p1_total": p1_total,
                "p1_relinked": p1_relinked,
                "p1_no_prospect_in_db": p1_no_prospect_in_db,
                "p1_prospect_found_no_unit": p1_prospect_found_no_unit,
                "p2_total": p2_total,
                "p2_inherited": p2_inherited,
                "still_no_unit": still_no_unit_count,
                "still_no_unit_prospect_count": len(still_no_unit_prospects),
                "missing_prospect_ids": sorted(missing_prospect_ids),
            },
        )

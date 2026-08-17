"""
Sync rentengine_id onto existing core.Unit rows.

Dry-run by default. Pass --apply to write.
Database writes: sets Unit.rentengine_id on newly matched rows and updates
Unit.rentengine_status on all matched rows (both new and already-linked).
"""

import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_unit
from integrations.rentengine.services import (
    build_listing_to_unit_map,
    resolve_rentengine_units,
)
from integrations.rentvine.client import RentvineClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Sync rentengine_id onto core.Unit rows matched via RentVine listings. "
        "Dry-run by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write rentengine_id values. Without this flag, dry-run only.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLY" if apply else "DRY RUN"
        self.stdout.write(f"Mode: {mode}")
        self.stdout.write("")

        # --- Fetch RentEngine units ---
        self.stdout.write("Fetching RentEngine units...")
        re_client = RentEngineClient()
        raw_units = re_client.get_all("units")
        re_units = [map_unit(u) for u in raw_units]
        re_id_to_status = {u["rentengine_id"]: u.get("status", "") for u in re_units}
        logger.info("rentengine_units_fetched", extra={"count": len(re_units)})

        # --- Fetch RentVine listings ---
        self.stdout.write("Fetching RentVine listings...")
        rv_client = RentvineClient()
        rv_listings = rv_client.get_all("properties/listings/search")
        logger.info("rentvine_listings_fetched", extra={"count": len(rv_listings)})

        # Validate listing shape
        if rv_listings:
            first_row = rv_listings[0]
            if not isinstance(first_row, dict) or "listing" not in first_row:
                self.stderr.write(self.style.ERROR(
                    "Unexpected listing shape: no 'listing' key in outer row. Aborting."
                ))
                return

        listing_to_rv_unit = build_listing_to_unit_map(rv_listings)

        # --- Load core.Unit data (read-only) ---
        db_units_qs = Unit.objects.select_related("property").all()
        rv_id_to_db = {}
        for u in db_units_qs:
            if u.rentvine_id is not None:
                rv_id_to_db[u.rentvine_id] = u.id

        # --- Resolve ---
        resolution = resolve_rentengine_units(
            re_units, listing_to_rv_unit, rv_id_to_db
        )

        all_unmatched = (
            resolution.unmatched_no_extracted
            + resolution.unmatched_not_parseable
            + resolution.unmatched_listing_missing
            + resolution.unmatched_unit_missing
        )

        if not resolution.matched:
            self.stdout.write("No matched units. Nothing to do.")
            return

        # --- Load matched Unit objects (single query, read-only so far) ---
        matched_db_ids = [m["db_unit_id"] for m in resolution.matched]
        units_by_id = {
            u.id: u
            for u in Unit.objects.filter(id__in=matched_db_ids)
        }

        # --- Safety checks: duplicate mappings ---
        # Check: same rentengine_id -> multiple core.Unit rows
        re_id_to_db_ids = defaultdict(list)
        for m in resolution.matched:
            re_id_to_db_ids[m["rentengine_id"]].append(m["db_unit_id"])
        dupes_re = {
            re_id: db_ids
            for re_id, db_ids in re_id_to_db_ids.items()
            if len(db_ids) > 1
        }

        # Check: same core.Unit -> multiple rentengine_id values
        db_id_to_re_ids = defaultdict(list)
        for m in resolution.matched:
            db_id_to_re_ids[m["db_unit_id"]].append(m["rentengine_id"])
        dupes_db = {
            db_id: re_ids
            for db_id, re_ids in db_id_to_re_ids.items()
            if len(re_ids) > 1
        }

        if dupes_re or dupes_db:
            self.stderr.write(self.style.ERROR("DUPLICATE MAPPING DETECTED — aborting."))
            if dupes_re:
                self.stderr.write("  Same rentengine_id -> multiple core.Unit rows:")
                for re_id, db_ids in dupes_re.items():
                    self.stderr.write(f"    RE {re_id} -> Unit IDs {db_ids}")
            if dupes_db:
                self.stderr.write("  Same core.Unit -> multiple rentengine_id values:")
                for db_id, re_ids in dupes_db.items():
                    self.stderr.write(f"    Unit #{db_id} -> RE IDs {re_ids}")
            logger.info("sync_aborted_duplicates", extra={
                "dupes_re": {str(k): v for k, v in dupes_re.items()},
                "dupes_db": {str(k): v for k, v in dupes_db.items()},
            })
            return

        # --- Classify each match ---
        will_set = []     # (unit, rentengine_id, address, status)
        already_set = []  # (unit, rentengine_id, address, status)
        conflicts = []    # (unit, existing_re_id, incoming_re_id, address)

        for m in resolution.matched:
            unit = units_by_id.get(m["db_unit_id"])
            if unit is None:
                continue
            incoming = m["rentengine_id"]
            addr = m["address"]
            status = re_id_to_status.get(incoming, "")

            if unit.rentengine_id is None:
                will_set.append((unit, incoming, addr, status))
            elif unit.rentengine_id == incoming:
                already_set.append((unit, incoming, addr, status))
            else:
                conflicts.append((unit, unit.rentengine_id, incoming, addr))

        # --- Hard-stop on conflicts ---
        if conflicts:
            self.stderr.write(self.style.ERROR(
                f"CONFLICT — {len(conflicts)} unit(s) already have a "
                f"different rentengine_id. Aborting WITHOUT writing anything."
            ))
            self.stderr.write(
                f"  {'Unit #':>8}  {'Existing':>10}  {'Incoming':>10}  Address"
            )
            self.stderr.write(
                f"  {'------':>8}  {'--------':>10}  {'--------':>10}  -------"
            )
            for unit, existing, incoming, addr in conflicts:
                self.stderr.write(
                    f"  {unit.id:>8}  {existing:>10}  {incoming:>10}  {addr}"
                )
            logger.info("sync_aborted_conflicts", extra={
                "conflict_count": len(conflicts),
                "conflicts": [
                    {"unit_id": u.id, "existing": ex, "incoming": inc}
                    for u, ex, inc, _ in conflicts
                ],
            })
            return

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write("=" * 72)
        self.stdout.write(f"RENTENGINE SYNC \u2014 {mode}")
        self.stdout.write("=" * 72)
        self.stdout.write("")
        self.stdout.write("SUMMARY")
        self.stdout.write(f"  Total matched        : {len(resolution.matched)}")
        self.stdout.write(f"  WILL SET             : {len(will_set)}")
        self.stdout.write(f"  ALREADY SET (no-op)  : {len(already_set)}")
        self.stdout.write(f"  CONFLICT             : {len(conflicts)}")
        self.stdout.write(f"  Unmatched (skipped)  : {len(all_unmatched)}")
        self.stdout.write("")

        if will_set:
            self.stdout.write("WILL SET:")
            self.stdout.write(
                f"  {'Unit #':>8}  {'RE ID':>10}  {'Status':<16}  Address"
            )
            self.stdout.write(
                f"  {'------':>8}  {'-----':>10}  {'------':<16}  -------"
            )
            for unit, re_id, addr, status in will_set:
                self.stdout.write(f"  {unit.id:>8}  {re_id:>10}  {status:<16}  {addr}")
            self.stdout.write("")

        if already_set:
            self.stdout.write(f"ALREADY LINKED (status update only): {len(already_set)}")
            self.stdout.write(
                f"  {'Unit #':>8}  {'RE ID':>10}  {'Status':<16}  Address"
            )
            self.stdout.write(
                f"  {'------':>8}  {'-----':>10}  {'------':<16}  -------"
            )
            for unit, re_id, addr, status in already_set:
                self.stdout.write(f"  {unit.id:>8}  {re_id:>10}  {status:<16}  {addr}")
            self.stdout.write("")

        # --- Unmatched ---
        self.stdout.write(
            f"SKIPPED \u2014 not linkable (expected: former properties): "
            f"{len(all_unmatched)}"
        )
        for reu in all_unmatched:
            self.stdout.write(
                f"  RE {reu['rentengine_id']:>8}  {reu['formatted_address']}"
            )
        self.stdout.write("")

        # --- Status breakdown ---
        all_statuses = (
            [s for _, _, _, s in will_set]
            + [s for _, _, _, s in already_set]
        )
        if all_statuses:
            status_counts = defaultdict(int)
            for s in all_statuses:
                status_counts[s or "(blank)"] += 1
            self.stdout.write("STATUS BREAKDOWN (matched units):")
            for status_val, count in sorted(
                status_counts.items(), key=lambda x: -x[1]
            ):
                self.stdout.write(f"  {status_val:<20} {count}")
            self.stdout.write("")

        # --- Apply ---
        if apply and (will_set or already_set):
            self.stdout.write("Writing...")
            with transaction.atomic():
                for unit, re_id, addr, status in will_set:
                    unit.rentengine_id = re_id
                    unit.rentengine_status = status
                    unit.save(update_fields=["rentengine_id", "rentengine_status"])
                    logger.info(
                        "rentengine_id_set",
                        extra={
                            "unit_id": unit.id,
                            "rentengine_id": re_id,
                            "rentengine_status": status,
                        },
                    )
                for unit, re_id, addr, status in already_set:
                    unit.rentengine_status = status
                    unit.save(update_fields=["rentengine_status"])
            self.stdout.write(self.style.SUCCESS(
                f"Done. Set rentengine_id on {len(will_set)} unit(s), "
                f"updated status on {len(already_set)} existing unit(s)."
            ))
        elif apply:
            self.stdout.write("Nothing to write — no matched units.")
        else:
            self.stdout.write(
                "Dry run complete. Pass --apply to write."
            )

        self.stdout.write("=" * 72)

        logger.info("sync_complete", extra={
            "mode": mode,
            "matched": len(resolution.matched),
            "will_set": len(will_set),
            "already_set": len(already_set),
            "conflicts": len(conflicts),
            "unmatched": len(all_unmatched),
        })

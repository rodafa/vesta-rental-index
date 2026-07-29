"""
Backfill leasing.Prospect from RentEngine /prospects.

Writes only to leasing_prospect. Never touches core.Unit or any other table.
"""

import logging
import time

from django.db import connection, transaction
from django.db.utils import InterfaceError, OperationalError
from django.core.management.base import BaseCommand

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_prospect
from leasing.models import Prospect

logger = logging.getLogger(__name__)

CHUNK_SIZE = 100


class Command(BaseCommand):
    help = "Backfill leasing.Prospect from RentEngine /prospects."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        mode = "DRY RUN" if dry_run else "WRITE"
        self.stdout.write(f"Mode: {mode}")
        self.stdout.write("")

        # --- Build unit lookup (read-only) ---
        re_id_to_unit = {}
        for u in Unit.objects.filter(rentengine_id__isnull=False):
            re_id_to_unit[u.rentengine_id] = u
        self.stdout.write(f"Loaded {len(re_id_to_unit)} core.Unit rows with rentengine_id.")

        # Close the DB connection before the long API fetch.
        # Django will reopen it lazily on the next query.
        connection.close()

        # --- Fetch prospects ---
        self.stdout.write("Fetching prospects from RentEngine...")
        client = RentEngineClient()
        raw_prospects = client.get_all("prospects")
        self.stdout.write(f"Fetched {len(raw_prospects)} prospects.")
        logger.info("prospects_fetched", extra={"count": len(raw_prospects)})

        # --- Map all prospects (no DB needed) ---
        prepared = []
        linked = 0
        unlinked = 0
        unmatched_uoi = set()

        for raw in raw_prospects:
            re_id = raw.get("id")
            if re_id is None:
                continue

            mapped = map_prospect(raw)
            uoi = mapped.pop("unit_of_interest")
            unit = re_id_to_unit.get(uoi) if uoi is not None else None
            mapped["unit"] = unit

            if unit:
                linked += 1
            else:
                unlinked += 1
                if uoi is not None:
                    unmatched_uoi.add(uoi)

            prepared.append((int(re_id), mapped))

        # --- Write in chunks ---
        created = 0
        updated = 0
        failed_chunks = []

        if not dry_run:
            for i in range(0, len(prepared), CHUNK_SIZE):
                chunk = prepared[i : i + CHUNK_SIZE]
                chunk_num = i // CHUNK_SIZE + 1
                success = False

                for attempt in range(2):
                    try:
                        with transaction.atomic():
                            for re_id, mapped in chunk:
                                _, was_created = Prospect.objects.update_or_create(
                                    rentengine_id=re_id,
                                    defaults=mapped,
                                )
                                if was_created:
                                    created += 1
                                else:
                                    updated += 1
                        success = True
                        break
                    except (OperationalError, InterfaceError) as exc:
                        if attempt == 0:
                            logger.warning(
                                "prospect_chunk_db_error",
                                extra={
                                    "chunk": chunk_num,
                                    "attempt": 1,
                                    "error": str(exc),
                                },
                            )
                            connection.close()
                            time.sleep(2)
                        else:
                            logger.warning(
                                "prospect_chunk_failed",
                                extra={
                                    "chunk": chunk_num,
                                    "error": str(exc),
                                },
                            )
                            failed_chunks.append((chunk_num, i, i + len(chunk), str(exc)))

                self.stdout.write(
                    f"  Chunk {chunk_num}: "
                    f"{i + len(chunk)}/{len(prepared)} "
                    f"({'OK' if success else 'FAILED'})"
                )

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"PROSPECT BACKFILL — {mode}")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Total fetched              : {len(raw_prospects)}")
        if not dry_run:
            self.stdout.write(f"  Created                    : {created}")
            self.stdout.write(f"  Updated                    : {updated}")
            if failed_chunks:
                self.stdout.write(
                    self.style.ERROR(f"  Failed chunks              : {len(failed_chunks)}")
                )
                for cnum, start, end, err in failed_chunks:
                    self.stdout.write(f"    chunk {cnum} (rows {start}-{end}): {err}")
        self.stdout.write(f"  Linked to a unit           : {linked}")
        self.stdout.write(f"  Unlinked (no matching unit) : {unlinked}")
        self.stdout.write(f"  Distinct unmatched unit IDs : {len(unmatched_uoi)}")
        self.stdout.write("=" * 60)

        logger.info("prospect_backfill_complete", extra={
            "mode": mode,
            "total": len(raw_prospects),
            "prospects_created": created,
            "prospects_updated": updated,
            "linked": linked,
            "unlinked": unlinked,
            "unmatched_unit_ids": len(unmatched_uoi),
            "failed_chunks": len(failed_chunks),
        })

"""
Backfill leasing.LeasingEvent from RentEngine /leasing_events.

Iterates Prospect rows in the DB, fetches events per prospect.
Writes only to leasing_leasingevent. Never touches core.Unit or any other table.
"""

import logging
import time
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.utils import InterfaceError, OperationalError
from django.core.management.base import BaseCommand

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_leasing_event
from leasing.models import LeasingEvent, Prospect

logger = logging.getLogger(__name__)

PROGRESS_FILE = Path(settings.BASE_DIR) / ".backfill_events_progress.txt"


def _load_progress():
    """Load set of already-processed prospect rentengine_ids."""
    if not PROGRESS_FILE.exists():
        return set()
    result = set()
    try:
        for line in PROGRESS_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                result.add(int(line))
            except ValueError:
                continue
    except OSError:
        pass
    return result


def _append_progress(prospect_id):
    """Append a single prospect rentengine_id to the progress file."""
    with open(PROGRESS_FILE, "a") as f:
        f.write(f"{prospect_id}\n")
        f.flush()


class Command(BaseCommand):
    help = "Backfill leasing.LeasingEvent from RentEngine /leasing_events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Skip prospects already recorded in the progress file.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Process only the first N prospects (0 = all).",
        )
        parser.add_argument(
            "--prospect-id",
            type=int,
            default=0,
            help="Process exactly one prospect by rentengine_id.",
        )

    def handle(self, *args, **options):
        resume = options["resume"]
        limit = options["limit"]
        single_id = options["prospect_id"]

        # --- Build unit lookup (read-only) ---
        re_id_to_unit = {}
        for u in Unit.objects.filter(rentengine_id__isnull=False):
            re_id_to_unit[u.rentengine_id] = u
        self.stdout.write(
            f"Loaded {len(re_id_to_unit)} core.Unit rows with rentengine_id."
        )

        # --- Build prospect queryset ---
        if single_id:
            prospects = Prospect.objects.filter(rentengine_id=single_id)
            if not prospects.exists():
                self.stderr.write(
                    self.style.ERROR(f"No prospect with rentengine_id={single_id}")
                )
                return
        else:
            prospects = Prospect.objects.order_by("rentengine_id")

        # --- Resume logic ---
        if resume:
            done_ids = _load_progress()
            skipped = len(done_ids)
            self.stdout.write(
                f"Resuming: {skipped} prospect(s) in progress file, will skip."
            )
            prospects = prospects.exclude(rentengine_id__in=done_ids)

        if limit > 0 and not single_id:
            prospects = prospects[:limit]

        prospect_list = list(prospects)
        total_prospects = len(prospect_list)
        self.stdout.write(f"Prospects to process: {total_prospects}")
        self.stdout.write("")

        # --- Prospect -> unit_of_interest map for mismatch detection ---
        prospect_uoi = {}
        for p in prospect_list:
            raw = p.raw_data or {}
            uoi = raw.get("unit_of_interest")
            if uoi is not None:
                try:
                    prospect_uoi[p.rentengine_id] = int(uoi)
                except (ValueError, TypeError):
                    pass

        # --- Iterate ---
        client = RentEngineClient()
        processed = 0
        events_created = 0
        events_updated = 0
        prospects_with_zero = 0
        events_no_unit = 0
        event_type_counter = Counter()
        failures = []
        uoi_mismatches = []

        t0 = time.monotonic()

        for prospect in prospect_list:
            processed += 1
            pid = prospect.rentengine_id

            try:
                raw_events = client.get_all_enveloped(
                    "leasing_events", params={"prospect_id": pid}
                )
            except Exception as exc:
                failures.append((pid, str(exc)))
                logger.warning(
                    "event_fetch_failed",
                    extra={"prospect_id": pid, "error": str(exc)},
                )
                continue

            if not raw_events:
                prospects_with_zero += 1

            # --- Prepare mapped events (no DB needed) ---
            event_batch = []
            batch_no_unit = 0
            batch_type_counts = Counter()
            batch_mismatches = []
            for raw_event in raw_events:
                event_re_id = raw_event.get("id")
                if event_re_id is None:
                    continue

                mapped = map_leasing_event(raw_event)

                if mapped["event_timestamp"] is None:
                    logger.warning(
                        "event_missing_timestamp",
                        extra={"event_id": event_re_id, "prospect_id": pid},
                    )
                    continue

                event_uoi = mapped.pop("unit_of_interest")
                mapped.pop("prospect_id")  # we already know the prospect

                unit = re_id_to_unit.get(event_uoi) if event_uoi is not None else None
                mapped["unit"] = unit
                mapped["prospect"] = prospect

                batch_type_counts[mapped["event_type"]] += 1

                if unit is None:
                    batch_no_unit += 1

                # Detect unit_of_interest mismatch
                p_uoi = prospect_uoi.get(pid)
                if event_uoi is not None and p_uoi is not None and event_uoi != p_uoi:
                    batch_mismatches.append({
                        "event_id": event_re_id,
                        "prospect_id": pid,
                        "prospect_uoi": p_uoi,
                        "event_uoi": event_uoi,
                    })

                event_batch.append((int(event_re_id), mapped))

            # --- Write events with retry ---
            write_ok = False
            for attempt in range(2):
                try:
                    with transaction.atomic():
                        for ev_re_id, ev_mapped in event_batch:
                            _, was_created = LeasingEvent.objects.update_or_create(
                                rentengine_id=ev_re_id,
                                defaults=ev_mapped,
                            )
                            if was_created:
                                events_created += 1
                            else:
                                events_updated += 1
                    write_ok = True
                    break
                except (OperationalError, InterfaceError) as exc:
                    if attempt == 0:
                        logger.warning(
                            "event_write_db_error",
                            extra={
                                "prospect_id": pid,
                                "attempt": 1,
                                "error": str(exc),
                            },
                        )
                        connection.close()
                        time.sleep(2)
                    else:
                        failures.append((pid, f"DB write failed: {exc}"))
                        logger.warning(
                            "event_write_failed",
                            extra={"prospect_id": pid, "error": str(exc)},
                        )

            # Only record progress and fold counters if writes succeeded
            if write_ok:
                _append_progress(pid)
                events_no_unit += batch_no_unit
                event_type_counter += batch_type_counts
                uoi_mismatches.extend(batch_mismatches)

            if processed % 100 == 0:
                elapsed = round(time.monotonic() - t0, 1)
                self.stdout.write(
                    f"  Progress: {processed}/{total_prospects} prospects, "
                    f"{events_created} events created, {elapsed}s elapsed"
                )

        # --- Summary ---
        elapsed = round(time.monotonic() - t0, 1)
        self.stdout.write("")
        self.stdout.write("=" * 68)
        self.stdout.write("EVENT BACKFILL SUMMARY")
        self.stdout.write("=" * 68)
        self.stdout.write(f"  Prospects processed      : {processed}")
        self.stdout.write(f"  Prospects with zero events (this run): {prospects_with_zero}")
        self.stdout.write(f"  Events created           : {events_created}")
        self.stdout.write(f"  Events updated           : {events_updated}")
        self.stdout.write(f"  Events with no matching unit: {events_no_unit}")
        self.stdout.write(f"  Elapsed                  : {elapsed}s")

        if failures:
            self.stdout.write("")
            self.stdout.write(f"  FAILURES: {len(failures)}")
            for pid, err in failures:
                self.stdout.write(f"    prospect {pid}: {err}")

        # Unit-of-interest mismatch report
        self.stdout.write("")
        self.stdout.write(
            f"  Unit-of-interest mismatches (event != prospect): "
            f"{len(uoi_mismatches)}"
        )
        if uoi_mismatches:
            self.stdout.write(
                f"  {'Event ID':>10}  {'Prospect':>10}  "
                f"{'Prosp UoI':>10}  {'Event UoI':>10}"
            )
            for m in uoi_mismatches[:10]:
                self.stdout.write(
                    f"  {m['event_id']:>10}  {m['prospect_id']:>10}  "
                    f"{m['prospect_uoi']:>10}  {m['event_uoi']:>10}"
                )
            if len(uoi_mismatches) > 10:
                self.stdout.write(
                    f"  ... and {len(uoi_mismatches) - 10} more"
                )

        self.stdout.write("")
        self.stdout.write("  Event types seen:")
        for etype, count in event_type_counter.most_common():
            self.stdout.write(f"    {etype:<30} {count}")

        self.stdout.write("=" * 68)

        logger.info("event_backfill_complete", extra={
            "prospects_processed": processed,
            "zero_events": prospects_with_zero,
            "events_created": events_created,
            "events_updated": events_updated,
            "no_unit": events_no_unit,
            "failure_count": len(failures),
            "uoi_mismatches": len(uoi_mismatches),
            "elapsed_seconds": elapsed,
        })

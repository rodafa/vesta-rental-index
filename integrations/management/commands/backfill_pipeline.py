import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

from integrations.pipeline_utils import detect_missing_dates
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.services import (
    LeasingPerformanceSyncService,
    UnitSyncService,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Detect and backfill missed pipeline days. "
        "Checks the last N days for gaps in DailyUnitSnapshot and re-runs "
        "sync + aggregation for each missing date."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback",
            type=int,
            default=7,
            help="Number of days to look back for gaps (default: 7).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Detect gaps and report them without writing any data.",
        )

    def handle(self, *args, **options):
        lookback = options["lookback"]
        dry_run = options["dry_run"]

        missing = detect_missing_dates(lookback_days=lookback)

        if not missing:
            self.stdout.write("  No gaps detected in the last "
                              f"{lookback} days.")
            return

        self.stdout.write(
            f"  Detected {len(missing)} missing date(s): "
            f"{', '.join(d.isoformat() for d in missing)}"
        )

        if dry_run:
            self.stdout.write("  DRY RUN — no data written.")
            return

        # Share a single API client across all backfill dates
        client = RentEngineClient()
        unit_svc = UnitSyncService(client=client)
        leasing_svc = LeasingPerformanceSyncService(client=client)

        for gap_date in missing:
            self.stdout.write(f"\n  Backfilling {gap_date} ...")
            date_start = time.time()

            # 1. Unit snapshots
            try:
                result = unit_svc.sync(target_date=gap_date)
                self.stdout.write(self.style.SUCCESS(
                    f"    UnitSync: {result['snapshots']} snapshots"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"    UnitSync FAILED: {exc}"
                ))
                logger.exception("Backfill UnitSync failed for %s", gap_date)

            # 2. Leasing summaries
            try:
                result = leasing_svc.sync(target_date=gap_date)
                self.stdout.write(self.style.SUCCESS(
                    f"    LeasingSync: {result['created']} created, "
                    f"{result['updated']} updated"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"    LeasingSync FAILED: {exc}"
                ))
                logger.exception(
                    "Backfill LeasingSync failed for %s", gap_date
                )

            # 3. Market aggregation
            try:
                call_command(
                    "aggregate_market_data",
                    date=gap_date.isoformat(),
                    stdout=self.stdout,
                )
                self.stdout.write(self.style.SUCCESS(
                    f"    MarketAggregation: OK"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"    MarketAggregation FAILED: {exc}"
                ))
                logger.exception(
                    "Backfill aggregation failed for %s", gap_date
                )

            elapsed = time.time() - date_start
            self.stdout.write(f"    {gap_date} done ({elapsed:.1f}s)")

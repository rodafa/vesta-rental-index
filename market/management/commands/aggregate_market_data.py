import logging
import time
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from market.services import (
    DailyMarketStatsAggregator,
    DailySegmentStatsAggregator,
    ListingCycleTracker,
    MonthlyMarketReportAggregator,
    MonthlySegmentStatsAggregator,
    PriceChangeDetector,
    WeeklyLeasingSummaryAggregator,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Aggregate market data from raw snapshots into higher-level stats"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Date for daily aggregation (YYYY-MM-DD). Default: today.",
        )
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Backfill a date range (requires --start and --end).",
        )
        parser.add_argument(
            "--start",
            type=str,
            help="Start date for backfill (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end",
            type=str,
            help="End date for backfill (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--weekly",
            action="store_true",
            help="Run weekly aggregation for the most recent complete Mon-Sun week.",
        )
        parser.add_argument(
            "--monthly",
            type=str,
            help="Run monthly aggregation for a specific month (YYYY-MM).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run daily + weekly + monthly aggregations.",
        )

    def handle(self, *args, **options):
        start_time = time.time()

        if options["backfill"]:
            self._handle_backfill(options)
        elif options["weekly"]:
            self._handle_weekly()
        elif options["monthly"]:
            self._handle_monthly(options["monthly"])
        elif options["all"]:
            target_date = self._parse_date(options.get("date"))
            self._handle_daily(target_date)
            self._handle_weekly()
            self._handle_monthly_current(target_date)
        else:
            target_date = self._parse_date(options.get("date"))
            self._handle_daily(target_date)

        elapsed = time.time() - start_time
        self.stdout.write(f"\nDone in {elapsed:.1f}s")

    def _parse_date(self, date_str):
        if date_str:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        return date.today()

    def _handle_daily(self, target_date):
        """Run all daily aggregations in dependency order."""
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Daily aggregation for {target_date}")
        self.stdout.write(f"{'='*50}")

        steps = [
            ("DailyMarketStats", DailyMarketStatsAggregator()),
            ("PriceDrop", PriceChangeDetector()),
            ("ListingCycle", ListingCycleTracker()),
            ("DailySegmentStats", DailySegmentStatsAggregator()),
        ]

        for name, service in steps:
            try:
                result = service.run(target_date)
                self.stdout.write(self.style.SUCCESS(
                    f"  {name}: {result['created']} created, {result['updated']} updated"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {name}: FAILED - {exc}"))
                logger.exception("Error in %s for %s", name, target_date)

    def _handle_backfill(self, options):
        if not options.get("start") or not options.get("end"):
            self.stdout.write(self.style.ERROR("--backfill requires --start and --end"))
            return

        start = datetime.strptime(options["start"], "%Y-%m-%d").date()
        end = datetime.strptime(options["end"], "%Y-%m-%d").date()

        self.stdout.write(f"Backfilling {start} to {end}")
        current = start
        while current <= end:
            self._handle_daily(current)
            current += timedelta(days=1)

        # Run weekly for each complete week in the range
        self.stdout.write("\nBackfilling weekly summaries...")
        week_end = start + timedelta(days=(6 - start.weekday()))  # Next Sunday
        while week_end <= end:
            self._handle_weekly_for_date(week_end)
            week_end += timedelta(days=7)

        # Run monthly for each month in the range
        self.stdout.write("\nBackfilling monthly reports...")
        current_month = date(start.year, start.month, 1)
        end_month = date(end.year, end.month, 1)
        while current_month <= end_month:
            self._handle_monthly(f"{current_month.year}-{current_month.month:02d}")
            if current_month.month == 12:
                current_month = date(current_month.year + 1, 1, 1)
            else:
                current_month = date(current_month.year, current_month.month + 1, 1)

    def _handle_weekly(self):
        """Aggregate the most recent complete Mon-Sun week."""
        today = date.today()
        # Find last Sunday
        days_since_sunday = (today.weekday() + 1) % 7
        if days_since_sunday == 0:
            days_since_sunday = 7  # If today is Sunday, use last Sunday
        last_sunday = today - timedelta(days=days_since_sunday)
        self._handle_weekly_for_date(last_sunday)

    def _handle_weekly_for_date(self, week_ending):
        self.stdout.write(f"\n  Weekly aggregation for week ending {week_ending}")
        try:
            result = WeeklyLeasingSummaryAggregator().run(week_ending)
            self.stdout.write(self.style.SUCCESS(
                f"  WeeklyLeasingSummary: {result['created']} created, {result['updated']} updated"
            ))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"  WeeklyLeasingSummary: FAILED - {exc}"))
            logger.exception("Error in weekly aggregation for %s", week_ending)

    def _handle_monthly(self, month_str):
        """Parse YYYY-MM and run monthly aggregations."""
        try:
            parts = month_str.split("-")
            year, month = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            self.stdout.write(self.style.ERROR(f"Invalid month format: {month_str} (expected YYYY-MM)"))
            return

        self.stdout.write(f"\n  Monthly aggregation for {year}-{month:02d}")

        steps = [
            ("MonthlyMarketReport", MonthlyMarketReportAggregator()),
            ("MonthlySegmentStats", MonthlySegmentStatsAggregator()),
        ]
        for name, service in steps:
            try:
                result = service.run(year, month)
                self.stdout.write(self.style.SUCCESS(
                    f"  {name}: {result['created']} created, {result['updated']} updated"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  {name}: FAILED - {exc}"))
                logger.exception("Error in %s for %s-%02d", name, year, month)

    def _handle_monthly_current(self, target_date):
        """Run monthly aggregation for the month of the given date."""
        self._handle_monthly(f"{target_date.year}-{target_date.month:02d}")

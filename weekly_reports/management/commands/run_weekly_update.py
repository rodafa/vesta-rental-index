"""
Management command to run the weekly update pipeline.

Usage:
    python manage.py run_weekly_update
    python manage.py run_weekly_update --start 2026-05-13 --end 2026-05-19
    python manage.py run_weekly_update --dry-run --skip-claude
    python manage.py run_weekly_update --property "123 Main St"
"""

import time
from datetime import date

from django.core.management.base import BaseCommand

from weekly_reports.services.weekly_update import default_date_range, run_for_week


class Command(BaseCommand):
    help = "Run the weekly update pipeline: gather metrics, draft AI notes, persist to DB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            default=None,
            help="Start date (YYYY-MM-DD). Defaults to most recent Tuesday.",
        )
        parser.add_argument(
            "--end",
            type=str,
            default=None,
            help="End date (YYYY-MM-DD). Defaults to Monday after start.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run full pipeline but skip all writes (Claude + DB).",
        )
        parser.add_argument(
            "--skip-claude",
            action="store_true",
            help="Skip Claude API calls (saves cost; use with --dry-run).",
        )
        parser.add_argument(
            "--property",
            type=str,
            default=None,
            help="Only process units matching this address substring.",
        )

    def handle(self, *args, **options):
        start_time = time.time()

        # Parse dates
        if options["start"] and options["end"]:
            try:
                start_date = date.fromisoformat(options["start"])
                end_date = date.fromisoformat(options["end"])
            except ValueError as e:
                self.stderr.write(self.style.ERROR(f"Invalid date: {e}"))
                return
        elif options["start"] or options["end"]:
            self.stderr.write(
                self.style.ERROR("Both --start and --end must be provided together.")
            )
            return
        else:
            start_date, end_date = default_date_range()

        # Validate
        if end_date < start_date:
            self.stderr.write(self.style.ERROR("--end must be >= --start"))
            return
        if end_date > date.today():
            self.stderr.write(self.style.ERROR("--end must be <= today"))
            return
        if (end_date - start_date).days > 31:
            self.stderr.write(self.style.ERROR("Date range must be <= 31 days"))
            return

        dry_run = options["dry_run"]
        skip_claude = options["skip_claude"]
        property_filter = options["property"]

        self.stdout.write(f"\nWeekly Update: {start_date} to {end_date}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no writes will occur"))
        if skip_claude:
            self.stdout.write(self.style.WARNING("SKIP CLAUDE — no AI drafts"))
        self.stdout.write("=" * 50)

        run = run_for_week(
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            skip_claude=skip_claude,
            property_filter=property_filter,
            triggered_by="cli",
        )

        elapsed = time.time() - start_time

        self.stdout.write(f"\nResults:")
        self.stdout.write(f"  Status:      {run.status}")
        self.stdout.write(f"  Processed:   {run.units_processed}")
        if dry_run:
            self.stdout.write(f"  Would draft: {run.notes_drafted}")
            self.stdout.write(f"  Would skip:  {run.units_skipped}")
        else:
            self.stdout.write(f"  Drafted:     {run.notes_drafted}")
            self.stdout.write(f"  Skipped:     {run.units_skipped}")
        self.stdout.write(f"  Errors:      {run.units_errored}")

        if run.error_log:
            self.stdout.write(f"\n  Errors:")
            for err in run.error_log:
                unit_id = err.get("unit", "?")
                self.stdout.write(f"    - Unit {unit_id}: {err.get('error', '?')}")

        self.stdout.write(f"\n  Elapsed: {elapsed:.1f}s")

        if run.status == "success":
            self.stdout.write(self.style.SUCCESS("\nDone."))
        elif run.status == "partial":
            self.stdout.write(self.style.WARNING("\nCompleted with errors."))
        else:
            self.stdout.write(self.style.ERROR("\nFailed."))

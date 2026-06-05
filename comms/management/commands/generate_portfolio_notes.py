"""
Management command: generate portfolio-grain monthly notes synchronously.

Runs the same generate_portfolio_notes() function used by the dashboard
button, but inline in one process — no background thread, no web worker
timeout, no concurrency lock.

Usage:
    python manage.py generate_portfolio_notes --month 2026-05
    python manage.py generate_portfolio_notes --month 2026-05 --dry-run
    python manage.py generate_portfolio_notes --month 2026-05 --portfolio "C2 Holding"
"""

import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from comms.services import generate_portfolio_notes, get_portfolio_generation_scope


class Command(BaseCommand):
    help = "Generate portfolio-grain monthly notes synchronously with progress."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            type=str,
            required=True,
            help="Report month as YYYY-MM (e.g. 2026-05).",
        )
        parser.add_argument(
            "--portfolio",
            type=str,
            default="",
            help="Filter portfolios by name (case-insensitive contains).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run selector + AI calls without writing rows.",
        )

    def handle(self, *args, **options):
        # Parse --month
        month_str = options["month"]
        try:
            parts = month_str.split("-")
            year, month = int(parts[0]), int(parts[1])
            _, last_day = calendar.monthrange(year, month)
            period_start = date(year, month, 1)
            period_end = date(year, month, last_day)
        except (ValueError, IndexError):
            raise CommandError(f"Invalid --month format: {month_str!r} (expected YYYY-MM)")

        # Build queryset (shared scope with the progress endpoint)
        portfolios = get_portfolio_generation_scope()

        portfolio_filter = options["portfolio"].strip()
        if portfolio_filter:
            portfolios = portfolios.filter(name__icontains=portfolio_filter)

        count = portfolios.count()
        mode = "DRY RUN" if options["dry_run"] else "LIVE"
        self.stdout.write(
            f"Generating portfolio notes for {period_start} to {period_end} "
            f"({count} portfolios, {mode})\n"
        )

        if count == 0:
            self.stdout.write(self.style.WARNING("No portfolios in scope."))
            return

        def progress(idx, total, name):
            pct = round(idx / total * 100)
            self.stdout.write(f"  [{idx}/{total}] ({pct}%) {name}")
            self.stdout.flush()

        try:
            result = generate_portfolio_notes(
                portfolios,
                period_start,
                period_end,
                dry_run=options["dry_run"],
                progress_cb=progress,
            )
        except RuntimeError as exc:
            raise CommandError(str(exc))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: generated={result['generated']}  "
                f"skipped={result['skipped']}  "
                f"errors={result['errors']}"
            )
        )
        if result["error_details"]:
            for err in result["error_details"]:
                self.stderr.write(f"  {err}")

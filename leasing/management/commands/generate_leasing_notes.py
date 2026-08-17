"""
Management command: generate portfolio-grain leasing notes (Layer 1).

Usage:
    python manage.py generate_leasing_notes --start 2026-08-04 --end 2026-08-10 --portfolio-id 42
    python manage.py generate_leasing_notes --start 2026-08-04 --end 2026-08-10 --limit 5
    python manage.py generate_leasing_notes --start 2026-08-04 --end 2026-08-10 --all
    python manage.py generate_leasing_notes --start 2026-08-04 --end 2026-08-10 --all --dry-run
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.models import Portfolio
from leasing.note_services import generate_portfolio_leasing_note

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate portfolio-grain leasing notes (Layer 1). No EmailDrafts created."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            required=True,
            help="Period start date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end",
            type=str,
            required=True,
            help="Period end date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--portfolio-id",
            type=int,
            help="Generate for a single portfolio by ID.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of portfolios to process.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run for all active portfolios. Required if no --portfolio-id or --limit.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build and print context without calling Anthropic or writing to DB.",
        )

    def handle(self, *args, **options):
        period_start = date.fromisoformat(options["start"])
        period_end = date.fromisoformat(options["end"])
        dry_run = options["dry_run"]

        has_portfolio_id = options["portfolio_id"] is not None
        has_limit = options["limit"] is not None
        has_all = options["all"]

        if not (has_portfolio_id or has_limit or has_all):
            raise CommandError(
                "Refusing to run unbounded. Provide --portfolio-id, --limit, or --all."
            )

        portfolios = Portfolio.objects.filter(is_active=True)
        if has_portfolio_id:
            portfolios = portfolios.filter(pk=options["portfolio_id"])
            if not portfolios.exists():
                raise CommandError(
                    f"No active portfolio with id={options['portfolio_id']}."
                )
        if has_limit:
            portfolios = portfolios[: options["limit"]]

        portfolio_list = list(portfolios)

        mode = "DRY RUN" if dry_run else "GENERATE"
        self.stdout.write(
            f"Generating leasing notes for {period_start} to {period_end} "
            f"({mode}, {len(portfolio_list)} portfolio(s))..."
        )

        # Close DB connection before API loop — Django reconnects automatically
        connection.close()

        generated = 0
        skipped = 0
        no_data = 0
        errors = []

        for portfolio in portfolio_list:
            try:
                result = generate_portfolio_leasing_note(
                    portfolio, period_start, period_end,
                    period_type="weekly", dry_run=dry_run,
                )

                if result is None:
                    no_data += 1
                    self.stdout.write(f"  {portfolio.name}: no eligible units")
                    continue

                status = result["status"]
                if status == "dry_run":
                    generated += 1
                    self.stdout.write(
                        f"  {portfolio.name}: {len(result['unit_contexts'])} unit(s)"
                    )
                    self.stdout.write(result["prompt"])
                    self.stdout.write("")
                elif status == "skipped":
                    skipped += 1
                    self.stdout.write(
                        f"  {portfolio.name}: skipped ({result['reason']})"
                    )
                elif status == "generated":
                    generated += 1
                    self.stdout.write(
                        f"  {portfolio.name}: generated (note #{result['note_id']}, "
                        f"{result['unit_count']} unit(s))"
                    )

            except Exception as exc:
                msg = f"Error processing {portfolio.name}: {exc}"
                logger.exception(
                    "leasing_note_error",
                    extra={
                        "portfolio_name": portfolio.name,
                        "error_detail": str(exc),
                    },
                )
                errors.append(msg)
                self.stderr.write(f"  {portfolio.name}: ERROR — {exc}")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: generated={generated}  skipped={skipped}  "
                f"no_data={no_data}  errors={len(errors)}"
            )
        )

        logger.info(
            "leasing_notes_run_complete",
            extra={
                "period_start_date": str(period_start),
                "period_end_date": str(period_end),
                "portfolio_count": len(portfolio_list),
                "generated_count": generated,
                "skipped_count": skipped,
                "no_data_count": no_data,
                "error_count": len(errors),
                "is_dry_run": dry_run,
            },
        )

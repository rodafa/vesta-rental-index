"""
Management command: sync portfolio owner statements from RentVine.

Usage:
    python manage.py sync_portfolio_statements
    python manage.py sync_portfolio_statements --portfolio-id 1234
    python manage.py sync_portfolio_statements --limit 5 --dry-run
"""

from django.core.management.base import BaseCommand

from integrations.rentvine.services import PortfolioStatementSyncService


class Command(BaseCommand):
    help = "Sync portfolio owner statements from the RentVine API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--portfolio-id",
            type=int,
            help="Sync statements for a single portfolio by RentVine ID.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of portfolios to process.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and log records without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        portfolio_id = options.get("portfolio_id")
        limit = options.get("limit")

        self.stdout.write("Syncing portfolio statements from RentVine...")

        result = PortfolioStatementSyncService().sync(
            dry_run=dry_run,
            portfolio_id=portfolio_id,
            limit=limit,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: portfolios_processed={result['portfolios_processed']}  "
                f"fetched={result['fetched']}  "
                f"created={result['created']}  "
                f"updated={result['updated']}  "
                f"errors={result['errors']}"
            )
        )

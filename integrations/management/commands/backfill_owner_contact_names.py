import logging

from django.core.management.base import BaseCommand

from core.models import Portfolio
from integrations.rentvine.services import populate_owner_contact_names

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Backfill Owner.contact_first_name for entity owners from "
        "person contacts sharing the same portfolio(s)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--portfolio",
            type=str,
            default=None,
            help=(
                "Limit to a single portfolio by name substring (case-insensitive). "
                "E.g. --portfolio 'Moody' to process only 90 Moody Ave LLC."
            ),
        )

    def handle(self, *args, **options):
        portfolio_filter = options["portfolio"]
        portfolio_id = None

        if portfolio_filter:
            try:
                portfolio = Portfolio.objects.get(
                    name__icontains=portfolio_filter,
                )
            except Portfolio.DoesNotExist:
                self.stderr.write(
                    self.style.ERROR(
                        f"No portfolio matching '{portfolio_filter}'"
                    )
                )
                return
            except Portfolio.MultipleObjectsReturned:
                matches = Portfolio.objects.filter(
                    name__icontains=portfolio_filter,
                ).values_list("name", flat=True)
                self.stderr.write(
                    self.style.ERROR(
                        f"Multiple portfolios match '{portfolio_filter}': "
                        f"{', '.join(matches)}. Be more specific."
                    )
                )
                return
            portfolio_id = portfolio.pk
            self.stdout.write(f"Filtering to portfolio: {portfolio.name}")

        result = populate_owner_contact_names(portfolio_id=portfolio_id)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {result['updated']} updated, "
                f"{result['skipped_person']} skipped (already person), "
                f"{result['skipped_no_match']} skipped (no person contact found)"
            )
        )

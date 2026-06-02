import logging

from django.core.management.base import BaseCommand

from integrations.rentvine.services import link_owners_from_portfolio_contacts

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Link Owner <-> Portfolio M2M from portfolio raw_data contacts"

    def handle(self, *args, **options):
        result = link_owners_from_portfolio_contacts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {result['linked']} links created, "
                f"{result['skipped']} contacts skipped (owner not found)"
            )
        )

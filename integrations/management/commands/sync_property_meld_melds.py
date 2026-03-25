"""
Management command: sync Property Meld melds into the local Meld table.

Usage:
    python manage.py sync_property_meld_melds
    python manage.py sync_property_meld_melds --dry-run
"""

from django.core.management.base import BaseCommand

from integrations.property_meld.services import MeldSyncService


class Command(BaseCommand):
    help = "Sync melds from Property Meld API into the local Meld table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and map records but do not write to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write("DRY RUN — no database writes will occur.\n")

        service = MeldSyncService()
        result = service.sync(dry_run=dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                "Sync complete: fetched={fetched}  created={created}  "
                "updated={updated}  errors={errors}".format(**result)
            )
        )

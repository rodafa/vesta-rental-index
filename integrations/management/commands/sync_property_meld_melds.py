"""
Management command: sync Property Meld melds into local tables.

Usage:
    python manage.py sync_property_meld_melds
    python manage.py sync_property_meld_melds --dry-run
    python manage.py sync_property_meld_melds --resolve-units
"""

from django.core.management.base import BaseCommand

from integrations.property_meld.services import MeldSyncService, resolve_all_melds
from maintenance.models import Meld


class Command(BaseCommand):
    help = "Sync melds from Property Meld API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and map records but do not write to the database.",
        )
        parser.add_argument(
            "--resolve-units",
            action="store_true",
            help="Run unit resolution after sync to populate unit FK.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write("DRY RUN — no database writes will occur.\n")

        service = MeldSyncService()
        result = service.sync(dry_run=dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                "Meld sync complete: fetched={fetched}  created={created}  "
                "updated={updated}  errors={errors}".format(**result)
            )
        )

        if not dry_run:
            all_statuses = sorted(
                Meld.objects.values_list("status", flat=True).distinct()
            )
            self.stdout.write(
                f"\nAll distinct PM meld statuses ({len(all_statuses)}):"
            )
            for s in all_statuses:
                self.stdout.write(f"  - {s}")

        if options["resolve_units"] and not dry_run:
            self.stdout.write("\nResolving meld -> property + unit links...")
            res = resolve_all_melds()
            total = Meld.objects.count()
            unit_linked = Meld.objects.filter(unit__isnull=False).count()
            prop_linked = Meld.objects.filter(property__isnull=False).count()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Unit resolution:     resolved={res['unit_resolved']}  "
                    f"unresolved={res['unit_unresolved']}  "
                    f"already={res['unit_already']}\n"
                    f"Property resolution: resolved={res['prop_resolved']}  "
                    f"unresolved={res['prop_unresolved']}  "
                    f"already={res['prop_already']}\n"
                    f"Coverage — unit:     {unit_linked}/{total} "
                    f"({unit_linked * 100 // total if total else 0}%)\n"
                    f"Coverage — property: {prop_linked}/{total} "
                    f"({prop_linked * 100 // total if total else 0}%)"
                )
            )

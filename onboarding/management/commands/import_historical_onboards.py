import csv

from django.core.management.base import BaseCommand, CommandError

from onboarding.models import OwnerOnboard

# All writable model fields — excludes id and submitted_at (auto)
IMPORTABLE_FIELDS = {
    f.name
    for f in OwnerOnboard._meta.get_fields()
    if hasattr(f, "column") and f.name not in ("id", "submitted_at")
}


class Command(BaseCommand):
    help = "Import historical onboarding records from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Path to the CSV file")

    def handle(self, *args, **options):
        csv_path = options["csv_path"]

        try:
            f = open(csv_path, newline="", encoding="utf-8-sig")
        except FileNotFoundError:
            raise CommandError(f"File not found: {csv_path}")

        skipped_empty = 0
        to_create = []

        with f:
            reader = csv.DictReader(f)
            for row in reader:
                address = (row.get("property_address") or "").strip()

                if not address:
                    skipped_empty += 1
                    continue

                kwargs = {
                    k: (v or "").strip()
                    for k, v in row.items()
                    if k in IMPORTABLE_FIELDS
                }

                to_create.append(OwnerOnboard(**kwargs))

        if to_create:
            OwnerOnboard.objects.bulk_create(to_create)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {len(to_create)} | Skipped (no address): {skipped_empty}"
            )
        )

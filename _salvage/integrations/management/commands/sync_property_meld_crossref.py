"""
Management command: build PM↔local address cross-reference.

Fetches PM properties/units and matches them to local records by normalized address.
Populates Property.property_meld_id and Unit.property_meld_id.

Usage:
    python manage.py sync_property_meld_crossref
    python manage.py sync_property_meld_crossref --dry-run
"""

from django.core.management.base import BaseCommand

from integrations.property_meld.client import PropertyMeldClient
from maintenance.services.unit_resolver import build_crossref


class Command(BaseCommand):
    help = "Build PM↔local property/unit cross-reference by address matching."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run matching logic but don't write to database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write("DRY RUN — will report matches but not write IDs.\n")

        client = PropertyMeldClient()
        result = build_crossref(client)

        pm = result["properties_matched"]
        pt = result["properties_total"]
        um = result["units_matched"]
        ut = result["units_total"]

        self.stdout.write(
            self.style.SUCCESS(
                f"\nCross-reference results:\n"
                f"  Properties: {pm}/{pt} resolved "
                f"({pm * 100 // pt if pt else 0}%)\n"
                f"  Units: {um}/{ut} resolved "
                f"({um * 100 // ut if ut else 0}%)"
            )
        )

        # Print unmatched for audit
        unmatched_props = [
            a for a in result["audit_log"]
            if a["type"] == "property" and a["confidence"] == "no_match"
        ]
        unmatched_units = [
            a for a in result["audit_log"]
            if a["type"] == "unit" and a["confidence"] == "no_match"
        ]

        if unmatched_props:
            self.stdout.write(
                f"\nUnmatched PM properties ({len(unmatched_props)}):"
            )
            for entry in unmatched_props[:20]:
                self.stdout.write(
                    f"  PM#{entry['pm_id']}: {entry['pm_address']}"
                )
            if len(unmatched_props) > 20:
                self.stdout.write(
                    f"  ... and {len(unmatched_props) - 20} more"
                )

        if unmatched_units:
            self.stdout.write(
                f"\nUnmatched PM units ({len(unmatched_units)}):"
            )
            for entry in unmatched_units[:20]:
                self.stdout.write(
                    f"  PM#{entry['pm_id']}: {entry['pm_name']}"
                )
            if len(unmatched_units) > 20:
                self.stdout.write(
                    f"  ... and {len(unmatched_units) - 20} more"
                )

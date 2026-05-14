"""
Read-only reconciliation of local Unit linkage against RentEngine's inventory.

Produces a CSV report and stdout summary identifying five anomaly classes:
  - stale_link: local Unit has rentengine_id that no longer exists in RE
  - unlinked_on_our_side: RE unit exists but no local Unit is linked to it
  - address_drift: linked pair whose addresses disagree
  - multi_unit_ambiguity: multiple RE units share an address with no unit_number
  - missing_rentengine_address: RE unit has no usable address fields

Usage:
    python manage.py audit_unit_matching
    python manage.py audit_unit_matching --output data/unit_matching_audit.csv
"""

import csv
import os
from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand

from leasing.services.unit_matching_audit import run_audit


class Command(BaseCommand):
    help = "Reconcile local Unit linkage against RentEngine inventory (read-only)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="CSV output path. Defaults to data/unit_matching_audit_<date>.csv",
        )

    def handle(self, *args, **options):
        output_path = options["output"] or f"data/unit_matching_audit_{date.today().isoformat()}.csv"

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        self.stdout.write("Running unit matching audit...")
        result = run_audit()

        # --- Write CSV ---
        all_anomalies = []
        for anomaly_list in result["anomalies"].values():
            all_anomalies.extend(anomaly_list)

        columns = [
            "anomaly_class", "rentengine_unit_id", "rentengine_address",
            "local_unit_id", "local_unit_address",
            "suggested_local_unit_id", "suggested_local_unit_address", "notes",
        ]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in all_anomalies:
                writer.writerow(row)

        # --- Stdout summary ---
        totals = result["totals"]
        total_re = result["rentengine_unit_count"]
        total_linked = result["local_linked_count"]
        total_clean = total_linked - totals["stale_link"] - totals["address_drift"]
        total_anomalies = sum(totals.values())

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("  UNIT MATCHING AUDIT REPORT")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  RentEngine units:       {total_re}")
        self.stdout.write(f"  Local linked units:     {total_linked}")
        self.stdout.write(f"  Clean links:            {total_clean}")
        self.stdout.write("-" * 60)
        self.stdout.write(f"  stale_link:             {totals['stale_link']}")
        self.stdout.write(f"  unlinked_on_our_side:   {totals['unlinked_on_our_side']}")

        unlinked = result["anomalies"]["unlinked_on_our_side"]
        with_candidate = sum(1 for a in unlinked if a["suggested_local_unit_id"])
        self.stdout.write(f"    - with candidate:     {with_candidate}")
        self.stdout.write(f"    - no candidate:       {totals['unlinked_on_our_side'] - with_candidate}")

        self.stdout.write(f"  address_drift:          {totals['address_drift']}")
        self.stdout.write(f"  multi_unit_ambiguity:   {totals['multi_unit_ambiguity']}")
        self.stdout.write(f"  missing_re_address:     {totals['missing_rentengine_address']}")
        self.stdout.write("-" * 60)
        self.stdout.write(f"  Total anomalies:        {total_anomalies}")
        self.stdout.write("=" * 60)
        self.stdout.write(f"\n  CSV written to: {output_path}")

        if total_anomalies == 0:
            self.stdout.write(self.style.SUCCESS("\n  All units reconciled cleanly."))
        elif total_anomalies <= 10:
            self.stdout.write(self.style.WARNING("\n  Minor cleanup needed. Should be a quick fix."))
        else:
            self.stdout.write(self.style.WARNING(f"\n  {total_anomalies} anomalies found. Review the CSV for details."))

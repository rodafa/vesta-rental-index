"""
Management command: send weekly maintenance summary emails to owners.

Usage:
    python manage.py send_maintenance_emails --start 2026-05-17 --end 2026-05-24
    python manage.py send_maintenance_emails --start 2026-05-17 --end 2026-05-24 --dry-run
    python manage.py send_maintenance_emails --start 2026-05-17 --end 2026-05-24 --to rodrigo@vestapm.com
    python manage.py send_maintenance_emails --start 2026-05-17 --end 2026-05-24 --owner 42
"""

from datetime import date

from django.core.management.base import BaseCommand

from maintenance.services.send_maintenance_emails import send_maintenance_emails


class Command(BaseCommand):
    help = "Send weekly maintenance summary emails to property owners."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=date.fromisoformat,
            required=True,
            help="Start date of reporting period (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end",
            type=date.fromisoformat,
            required=True,
            help="End date of reporting period (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Log what would be sent without actually sending.",
        )
        parser.add_argument(
            "--to",
            type=str,
            default=None,
            help="Override recipient email for test sends.",
        )
        parser.add_argument(
            "--owner",
            type=int,
            default=None,
            help="Only send to this owner ID.",
        )

    def handle(self, *args, **options):
        date_start = options["start"]
        date_end = options["end"]
        dry_run = options["dry_run"]
        test_recipient = options["to"]
        owner_id = options["owner"]

        if dry_run:
            self.stdout.write("DRY RUN — no emails will be sent.\n")

        result = send_maintenance_emails(
            date_start=date_start,
            date_end=date_end,
            dry_run=dry_run,
            test_recipient=test_recipient,
            owner_id=owner_id,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Send complete: sent={sent}  skipped={skipped}  "
                "failed={failed}".format(**result)
            )
        )

        if result["errors"]:
            self.stdout.write(self.style.ERROR("Errors:"))
            for err in result["errors"]:
                self.stdout.write(f"  {err['owner']}: {err['error']}")

"""Management command to send approved weekly leasing emails to owners."""

from datetime import date

from django.core.management.base import BaseCommand

from weekly_reports.services.send_owner_emails import send_approved_emails
from weekly_reports.services.weekly_update import default_date_range


class Command(BaseCommand):
    help = "Send approved weekly leasing emails to owners."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
        parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
        parser.add_argument("--dry-run", action="store_true", help="Log without sending")
        parser.add_argument("--owner", type=int, help="Send to a specific owner ID only")
        parser.add_argument(
            "--to", type=str,
            help="Test send: deliver to this address instead of the owner's. "
                 "Skips idempotency and does not write OwnerEmailSend records.",
        )

    def handle(self, *args, **options):
        if options["start"] and options["end"]:
            week_start = date.fromisoformat(options["start"])
            week_end = date.fromisoformat(options["end"])
        else:
            week_start, week_end = default_date_range()

        test_recipient = options.get("to")
        label = f"Sending emails for {week_start} - {week_end}"
        if test_recipient:
            label += f" (test → {test_recipient})"
        if options["dry_run"]:
            label += " (dry run)"
        self.stdout.write(label)

        result = send_approved_emails(
            week_start=week_start,
            week_end=week_end,
            dry_run=options["dry_run"],
            test_recipient=test_recipient,
            owner_id=options.get("owner"),
        )

        self.stdout.write(
            f"Done: sent={result['sent']} skipped={result['skipped']} "
            f"failed={result['failed']}"
        )
        if result["errors"]:
            for err in result["errors"]:
                self.stderr.write(f"  ERROR: {err['owner']} — {err['error']}")

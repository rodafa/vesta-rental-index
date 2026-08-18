"""
Management command: assemble leasing email drafts (Layer 2).

Assembles owner-grain emails on-demand from PortfolioLeasingNote rows
and upserts EmailDraft rows for human review. Does NOT send.

Usage:
    python manage.py send_leasing_emails --start 2026-08-04 --end 2026-08-10 --dry-run
    python manage.py send_leasing_emails --start 2026-08-04 --end 2026-08-10
    python manage.py send_leasing_emails --start 2026-08-04 --end 2026-08-10 --owner-email owner@example.com
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from leasing.email_services import (
    assemble_leasing_drafts,
    get_leasing_recipient_emails,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Assemble leasing email drafts from portfolio-grain notes (Layer 2)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            required=True,
            help="Period start date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end",
            type=str,
            required=True,
            help="Period end date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--owner-email",
            type=str,
            default=None,
            help="Process a single owner email only (for validation/testing).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print what would be created without writing to the database.",
        )

    def handle(self, *args, **options):
        period_start = date.fromisoformat(options["start"])
        period_end = date.fromisoformat(options["end"])
        dry_run = options["dry_run"]

        self.stdout.write(
            f"Assembling leasing email drafts for {period_start} to {period_end}"
            f"{' (dry run)' if dry_run else ''}..."
        )

        if dry_run:
            self._handle_dry_run(period_start, period_end, options["owner_email"])
            return

        result = assemble_leasing_drafts(
            period_start, period_end,
            owner_email=options["owner_email"],
        )

        if result["blocking_portfolios"]:
            names = ", ".join(result["blocking_portfolios"])
            raise CommandError(
                f"Cannot assemble: these portfolios are not approved: {names}"
            )

        for err in result["errors"]:
            self.stderr.write(self.style.ERROR(f"  {err}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: created={result['created']}  updated={result['updated']}  "
                f"skipped={result['skipped']}  errors={len(result['errors'])}"
            )
        )

        logger.info(
            "send_leasing_emails_complete",
            extra={
                "period_start": str(period_start),
                "period_end": str(period_end),
                "dry_run": False,
                "drafts_created": result["created"],
                "drafts_updated": result["updated"],
                "skipped": result["skipped"],
                "error_count": len(result["errors"]),
            },
        )

    def _handle_dry_run(self, period_start, period_end, owner_email_filter):
        """Dry-run mode: preview what would be assembled without writing."""
        from comms.services import (
            _normalize_email,
            assemble_owner_leasing_email,
        )
        from core.models import Owner

        if owner_email_filter:
            norm = _normalize_email(owner_email_filter)
            if not norm or not Owner.objects.filter(
                is_active=True, email__iexact=norm,
            ).exists():
                raise CommandError("No active owner with that email found.")
            target_emails = {norm}
        else:
            target_emails = get_leasing_recipient_emails(period_start, period_end)

        if not target_emails:
            raise CommandError("No active owners with email found.")

        created = 0
        skipped = 0

        for norm_email in sorted(target_emails):
            assembled = assemble_owner_leasing_email(
                norm_email, period_start, period_end,
            )
            if assembled is None:
                self.stdout.write(f"  {norm_email}: no notes \u2014 skipped")
                skipped += 1
                continue

            portfolio_count = len(assembled["portfolios"])
            unit_count = assembled["unit_count"]
            self.stdout.write(
                f"  {norm_email}: {assembled['owner_name']} \u2014 "
                f"{portfolio_count} portfolio(s), {unit_count} unit(s) [DRY RUN]"
            )
            created += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Done: would_create={created}  skipped={skipped}")
        )

        logger.info(
            "send_leasing_emails_complete",
            extra={
                "period_start": str(period_start),
                "period_end": str(period_end),
                "dry_run": True,
                "drafts_created": created,
                "skipped": skipped,
            },
        )

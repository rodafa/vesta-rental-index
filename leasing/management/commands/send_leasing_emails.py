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

from comms.models import EmailDraft
from comms.services import (
    _normalize_email,
    _format_period_label,
    assemble_owner_leasing_email,
)
from core.models import Owner

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
        period_type = "weekly"
        dry_run = options["dry_run"]

        self.stdout.write(
            f"Assembling leasing email drafts for {period_start} to {period_end}"
            f"{' (dry run)' if dry_run else ''}..."
        )

        # Build the set of unique recipient emails to process
        target_emails = self._get_target_emails(options["owner_email"])
        if not target_emails:
            raise CommandError("No active owners with email found.")

        created = 0
        updated = 0
        skipped = 0
        errors = []

        for norm_email in sorted(target_emails):
            try:
                assembled = assemble_owner_leasing_email(
                    norm_email, period_start, period_end, period_type=period_type,
                )

                if assembled is None:
                    self.stdout.write(f"  {norm_email}: no notes — skipped")
                    skipped += 1
                    continue

                rep_owner = assembled["rep_owner"]
                portfolio_count = len(assembled["portfolios"])
                unit_count = assembled["unit_count"]

                period_label = _format_period_label(period_type, period_start, period_end)
                subject = f"Weekly Leasing Update — {period_label}"

                if dry_run:
                    self.stdout.write(
                        f"  {norm_email}: {assembled['owner_name']} — "
                        f"{portfolio_count} portfolio(s), {unit_count} unit(s) [DRY RUN]"
                    )
                    created += 1
                    continue

                draft, was_created = EmailDraft.objects.update_or_create(
                    product="leasing",
                    owner=rep_owner,
                    period_type=period_type,
                    period_start=period_start,
                    defaults={
                        "recipient_email": norm_email,
                        "subject": subject,
                        "body_html": assembled["body_html"],
                        "generated_note": "",
                        "period_end": period_end,
                        "status": "draft",
                        "sent_at": None,
                        "sent_by": None,
                    },
                )

                action = "created" if was_created else "updated"
                if was_created:
                    created += 1
                else:
                    updated += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {norm_email}: {assembled['owner_name']} — "
                        f"{portfolio_count} portfolio(s), {unit_count} unit(s) [{action}]"
                    )
                )

            except Exception as exc:
                msg = f"  {norm_email}: ERROR — {exc}"
                self.stderr.write(self.style.ERROR(msg))
                errors.append(msg)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: created={created}  updated={updated}  "
                f"skipped={skipped}  errors={len(errors)}"
            )
        )

        logger.info(
            "send_leasing_emails_complete",
            extra={
                "period_start": str(period_start),
                "period_end": str(period_end),
                "dry_run": dry_run,
                "drafts_created": created,
                "drafts_updated": updated,
                "skipped": skipped,
                "error_count": len(errors),
            },
        )

    def _get_target_emails(self, owner_email_filter):
        """
        Build the deduplicated set of recipient emails to process.

        If --owner-email is provided, returns just that one (normalized).
        Otherwise, returns all unique emails from active owners.
        """
        if owner_email_filter:
            norm = _normalize_email(owner_email_filter)
            if not norm:
                return set()
            if not Owner.objects.filter(is_active=True, email__iexact=norm).exists():
                return set()
            return {norm}

        # All unique emails from active owners
        emails = (
            Owner.objects.filter(is_active=True)
            .exclude(email="")
            .exclude(email__isnull=True)
            .values_list("email", flat=True)
        )
        return {_normalize_email(e) for e in emails if _normalize_email(e)}

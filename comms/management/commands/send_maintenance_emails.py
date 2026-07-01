"""
Management command: assemble and send maintenance emails (Layer 2).

Assembles owner-grain emails on-demand from PortfolioMaintenanceNote rows,
upserts EmailDraft, and sends via send_draft().

Deduplicates by recipient email — two Owner records sharing one email
produce exactly ONE assembled email and ONE send.

Usage:
    python manage.py send_maintenance_emails --period-start 2026-05-25 --acting-user rodrigo --sandbox
    python manage.py send_maintenance_emails --period-start 2026-05-25 --acting-user rodrigo --test-email me@example.com
    python manage.py send_maintenance_emails --period-start 2026-05-25 --acting-user rodrigo --live
    python manage.py send_maintenance_emails --period-start 2026-05-25 --acting-user rodrigo --test-email me@example.com --owner-email owner@example.com
"""

import logging
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from comms.models import EmailDraft, PortfolioMaintenanceNote
from comms.services import (
    _normalize_email,
    _format_period_label,
    assemble_owner_maintenance_email,
    send_draft,
)
from core.models import Owner

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Assemble and send maintenance emails from portfolio-grain notes (Layer 2)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period-start",
            type=str,
            required=True,
            help="Period start date (YYYY-MM-DD) — targets the already-reviewed week.",
        )
        parser.add_argument(
            "--acting-user",
            required=True,
            help="Username of the staff member authorising the send.",
        )
        parser.add_argument(
            "--owner-email",
            type=str,
            default=None,
            help="Process a single owner email only (for validation/testing).",
        )

        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--sandbox",
            action="store_true",
            default=False,
            help="SendGrid sandbox mode — validates but delivers nothing.",
        )
        mode.add_argument(
            "--test-email",
            type=str,
            default=None,
            help="Send a real email but redirect to this address (dry run).",
        )
        mode.add_argument(
            "--live",
            action="store_true",
            default=False,
            help="Send for real to the owner's email. Marks draft as sent.",
        )

    def handle(self, *args, **options):
        # Require exactly one mode flag
        if not options["sandbox"] and not options["test_email"] and not options["live"]:
            raise CommandError(
                "You must specify one of --sandbox, --test-email, or --live. "
                "This is a safety measure — no sends without an explicit mode."
            )

        try:
            acting_user = User.objects.get(username=options["acting_user"])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['acting_user']}' not found.")

        period_start = date.fromisoformat(options["period_start"])
        period_end = period_start + timedelta(days=6)
        period_type = "weekly"

        sandbox = options["sandbox"]
        recipient_override = options["test_email"] or None
        mode_label = "sandbox" if sandbox else "test" if recipient_override else "live"

        self.stdout.write(
            f"Assembling maintenance emails for {period_start} to {period_end} "
            f"(mode={mode_label})..."
        )

        # Build the set of unique recipient emails to process
        target_emails = self._get_target_emails(options["owner_email"])
        if not target_emails:
            raise CommandError("No active owners with email found.")

        sent = 0
        skipped = 0
        errors = []

        for norm_email in sorted(target_emails):
            try:
                # Assemble on-demand from portfolio notes
                assembled = assemble_owner_maintenance_email(
                    norm_email, period_start, period_type=period_type
                )

                if assembled is None:
                    self.stdout.write(f"  {norm_email}: no activity — skipped")
                    skipped += 1
                    continue

                # LIVE gate: refuse if not all notes are approved
                if options["live"] and not assembled["all_approved"]:
                    unapproved = [
                        p["name"] for p in assembled["portfolios"]
                        if p["status"] != "approved"
                    ]
                    self.stderr.write(
                        self.style.WARNING(
                            f"  {norm_email}: BLOCKED — unapproved portfolios: "
                            f"{', '.join(unapproved)}"
                        )
                    )
                    skipped += 1
                    continue

                # Upsert EmailDraft — keyed by (product, owner, period_type, period_start)
                rep_owner = Owner.objects.filter(
                    is_active=True,
                    email__iexact=norm_email,
                ).order_by("pk").first()

                if not rep_owner:
                    self.stderr.write(f"  {norm_email}: no active owner found — skipped")
                    skipped += 1
                    continue

                # Read period_end from this recipient's notes; fall back to +6.
                _assembled_pks = [p["id"] for p in assembled["portfolios"]]
                _note_period_ends = list(
                    PortfolioMaintenanceNote.objects.filter(
                        portfolio_id__in=_assembled_pks,
                        period_type=period_type,
                        period_start=period_start,
                    )
                    .exclude(period_end__isnull=True)
                    .values_list("period_end", flat=True)
                    .distinct()
                )
                if len(_note_period_ends) > 1:
                    logger.warning(
                        "comms_period_end_mismatch",
                        extra={
                            "recipient": norm_email,
                            "portfolio_pks": _assembled_pks,
                            "distinct_period_ends": sorted(
                                str(d) for d in _note_period_ends
                            ),
                        },
                    )
                period_end = max(_note_period_ends, default=None) or (
                    period_start + timedelta(days=6)
                )

                period_label = _format_period_label(period_type, period_start, period_end)
                subject = f"Weekly Maintenance Update — {period_label}"

                draft, _ = EmailDraft.objects.update_or_create(
                    product="maintenance",
                    owner=rep_owner,
                    period_type=period_type,
                    period_start=period_start,
                    defaults={
                        "recipient_email": norm_email,
                        "subject": subject,
                        "body_html": assembled["body_html"],
                        "generated_note": "",
                        "period_end": period_end,
                        "status": "approved" if assembled["all_approved"] else "draft",
                        "sent_at": None,
                        "sent_by": None,
                    },
                )

                # Send
                result = send_draft(
                    draft=draft,
                    acting_user=acting_user,
                    recipient_override=recipient_override,
                    sandbox=sandbox,
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {norm_email}: sent "
                        f"(mode={result['mode']}, "
                        f"status={result['sendgrid_status']}, "
                        f"portfolios={len(assembled['portfolios'])})"
                    )
                )
                sent += 1

            except Exception as exc:
                msg = f"  {norm_email}: ERROR — {exc}"
                self.stderr.write(self.style.ERROR(msg))
                errors.append(msg)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: sent={sent}  skipped={skipped}  errors={len(errors)}"
            )
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
            # Verify the owner exists
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

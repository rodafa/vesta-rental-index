"""
Management command: generate owner distribution snapshots AND/OR send distribution emails.

Generate mode (no send flag): fetches the month's RentVine transactions ONCE,
then builds a PortfolioDistributionSnapshot for each portfolio.

Send mode (--sandbox / --test-email / --live): reads EXISTING snapshots and
sends. Never regenerates data. Build and send are separate invocations.

Usage:
    # Generate snapshots only
    python manage.py generate_portfolio_distributions --month 2026-06
    python manage.py generate_portfolio_distributions --month 2026-06 --portfolio 82

    # Send from existing snapshots
    python manage.py generate_portfolio_distributions --month 2026-06 --sandbox --acting-user rodrigo
    python manage.py generate_portfolio_distributions --month 2026-06 --test-email rodrigo@vestapm.com --acting-user rodrigo
    python manage.py generate_portfolio_distributions --month 2026-06 --live --acting-user rodrigo
    python manage.py generate_portfolio_distributions --month 2026-06 --sandbox --acting-user rodrigo --owner-email owner@example.com
"""

import calendar
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from comms.distribution_services import generate_distribution_snapshots
from comms.services import (
    _normalize_email,
    send_distribution_email,
)
from core.models import Owner, Portfolio


class Command(BaseCommand):
    help = "Generate owner distribution snapshots and/or send distribution emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            type=str,
            required=True,
            help="Target month as YYYY-MM (e.g. 2026-06).",
        )
        parser.add_argument(
            "--portfolio",
            type=int,
            default=None,
            help="Single portfolio rentvine_id for debugging (generate mode only).",
        )
        parser.add_argument(
            "--acting-user",
            type=str,
            default=None,
            help="Username of the staff member authorising the send (required for send modes).",
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
        # Parse month
        month_str = options["month"]
        try:
            year, month = month_str.split("-")
            year, month = int(year), int(month)
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
        except (ValueError, IndexError):
            raise CommandError(
                f"Invalid --month '{month_str}'. Use YYYY-MM format (e.g. 2026-06)."
            )

        has_send_flag = options["sandbox"] or options["test_email"] or options["live"]

        if has_send_flag:
            self._handle_send(options, month_start, month_end)
        else:
            self._handle_generate(options, month_start, month_end)

    def _handle_generate(self, options, month_start, month_end):
        """Generate mode: thin wrapper around generate_distribution_snapshots."""
        self.stdout.write(
            f"Generating distribution snapshots for {month_start} to {month_end}..."
        )

        # Validate --portfolio up front (CLI-specific error)
        portfolio_rentvine_id = options["portfolio"]
        if portfolio_rentvine_id is not None:
            if not Portfolio.objects.filter(
                rentvine_id=portfolio_rentvine_id, is_active=True
            ).exists():
                raise CommandError(
                    f"Portfolio with rentvine_id={portfolio_rentvine_id} not found or inactive."
                )

        def _progress(event, payload):
            if event == "portfolios_resolved":
                self.stdout.write(f"Processing {payload['count']} portfolio(s).")
            elif event == "fetch_start":
                messages = {
                    "transactions": "Fetching transactions from RentVine...",
                    "ledger_ids": "Resolving portfolio ledger IDs...",
                    "lease_balances": "Fetching lease balances from RentVine...",
                }
                self.stdout.write(messages[payload["step"]])
            elif event == "fetch_end":
                messages = {
                    "transactions": lambda c: f"Fetched {c} transactions for the month.",
                    "ledger_ids": lambda c: f"Resolved {c} ledger ID(s).",
                    "lease_balances": lambda c: f"Found {c} property/properties with positive tenant balance.",
                }
                self.stdout.write(messages[payload["step"]](payload["count"]))
            elif event == "portfolio_done":
                o = payload
                if o["status"] == "error":
                    msg = f"  [{o['index']}/{o['total']}] {o['portfolio_name']}: ERROR — {o['error_message']}"
                    self.stderr.write(self.style.ERROR(msg))
                else:
                    undeposited_str = ""
                    if o["undeposited_amount"]:
                        undeposited_str = (
                            f" | undeposited=${o['undeposited_amount']:,.2f}"
                            f" ({o['undeposited_source']})"
                        )
                    else:
                        undeposited_str = f" | undeposited=$0 ({o['undeposited_source']})"

                    self.stdout.write(
                        f"  [{o['index']}/{o['total']}] {o['portfolio_name']}: {o['status']} | "
                        f"properties={o['properties']} | "
                        f"expected=${o['expected']:,.2f} | "
                        f"collected=${o['collected']:,.2f} | "
                        f"distribution=${o['distribution_amount']:,.2f} "
                        f"({o['distribution_date'] or 'no date'})"
                        f"{undeposited_str}"
                    )
            elif event == "complete":
                self.stdout.write("")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done: created={payload['created']}  updated={payload['updated']}  "
                        f"errors={payload['errors']}"
                    )
                )

        generate_distribution_snapshots(
            month_start,
            month_end,
            portfolio_rentvine_id=portfolio_rentvine_id,
            progress_cb=_progress,
        )

    def _handle_send(self, options, month_start, month_end):
        """Send mode: read existing snapshots, assemble, and send emails."""
        # Validate acting-user
        if not options["acting_user"]:
            raise CommandError(
                "--acting-user is required when using --sandbox, --test-email, or --live."
            )

        try:
            acting_user = User.objects.get(username=options["acting_user"])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['acting_user']}' not found.")

        sandbox = options["sandbox"]
        recipient_override = options["test_email"] or None
        mode_label = "sandbox" if sandbox else "test" if recipient_override else "live"

        # OWNER_PORTAL_URL guard
        portal_url = getattr(settings, "OWNER_PORTAL_URL", "")
        if not portal_url:
            if options["live"]:
                raise CommandError(
                    "OWNER_PORTAL_URL is not set. Refusing --live send without portal URL."
                )
            else:
                self.stderr.write(
                    self.style.WARNING(
                        "WARNING: OWNER_PORTAL_URL is not set. "
                        "Portal CTA will be hidden in emails."
                    )
                )

        period_type = "monthly"

        self.stdout.write(
            f"Sending distribution emails for {month_start} to {month_end} "
            f"(mode={mode_label})..."
        )

        # Build the set of unique recipient emails
        target_emails = self._get_target_emails(options["owner_email"])
        if not target_emails:
            raise CommandError("No active owners with email found.")

        sent = 0
        skipped = 0
        errors = []

        for norm_email in sorted(target_emails):
            try:
                result = send_distribution_email(
                    recipient_email=norm_email,
                    period_start=month_start,
                    period_type=period_type,
                    period_end=month_end,
                    acting_user=acting_user,
                    recipient_override=recipient_override,
                    sandbox=sandbox,
                )

                if result == "already_sent":
                    self.stdout.write(f"  {norm_email}: already sent — skipped")
                    skipped += 1
                elif result == "no_activity":
                    self.stdout.write(f"  {norm_email}: no qualifying distributions — skipped")
                    skipped += 1
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  {norm_email}: sent "
                            f"(mode={result['mode']}, "
                            f"status={result['sendgrid_status']})"
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
            if not Owner.objects.filter(is_active=True, email__iexact=norm).exists():
                return set()
            return {norm}

        emails = (
            Owner.objects.filter(is_active=True)
            .exclude(email="")
            .exclude(email__isnull=True)
            .values_list("email", flat=True)
        )
        return {_normalize_email(e) for e in emails if _normalize_email(e)}

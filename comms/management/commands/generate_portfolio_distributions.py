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
import logging
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from comms.models import PortfolioDistributionSnapshot
from comms.selectors import get_undeposited_rent
from comms.services import (
    _normalize_email,
    build_distribution_snapshot,
    send_distribution_email,
    upsert_distribution_snapshot,
)
from core.models import Owner, Portfolio
from integrations.rentvine.client import RentvineClient

logger = logging.getLogger(__name__)


def _fetch_month_transactions(month_start, month_end):
    """
    Fetch all transactions from RentVine for the target month.

    Paginates the full /accounting/transactions endpoint and keeps
    only records with datePosted within [month_start, month_end].
    No type or account filtering — that's the selectors' job.

    Returns a list of raw transaction dicts.
    """
    client = RentvineClient()
    records = []
    page = 1
    page_size = 100
    total_scanned = 0

    month_start_str = str(month_start)
    month_end_str = str(month_end)

    while True:
        data = client.get(
            "/accounting/transactions",
            params={"page": page, "pageSize": page_size},
        )
        if not isinstance(data, list) or not data:
            break

        for record in data:
            total_scanned += 1
            tx = (
                record.get("transaction", record)
                if isinstance(record, dict)
                else record
            )
            date_posted = tx.get("datePosted") or ""
            if month_start_str <= date_posted <= month_end_str:
                records.append(record)

        if len(data) < page_size:
            break
        page += 1

    logger.info(
        "comms_distribution_fetch_transactions",
        extra={
            "month_start": month_start_str,
            "month_end": month_end_str,
            "total_scanned": total_scanned,
            "kept": len(records),
            "pages": page,
        },
    )
    return records


def _resolve_ledger_ids():
    """
    Fetch the portfolio → ledgerID mapping from RentVine once per run.

    GET /accounting/ledgers/search?ledgerTypeID=2 returns portfolio-type ledgers.
    Each record has objectID (= portfolio's RentVine internal ID) and ledgerID.

    Paginates the response so all ledgers are captured, not just the first page.

    Returns {portfolio_rentvine_id: ledger_id} dict.
    """
    client = RentvineClient()
    ledger_map = {}
    page = 1
    page_size = 100

    while True:
        data = client.get(
            "/accounting/ledgers/search",
            params={"ledgerTypeID": 2, "page": page, "pageSize": page_size},
        )

        records = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if not records:
            break

        for record in records:
            obj = record.get("ledger", record) if isinstance(record, dict) else record
            obj_id = obj.get("objectID")
            ledger_id = obj.get("ledgerID")
            if obj_id and ledger_id:
                ledger_map[int(obj_id)] = int(ledger_id)

        if len(records) < page_size:
            break
        page += 1

    logger.info(
        "comms_distribution_ledger_map",
        extra={"ledger_count": len(ledger_map)},
    )
    return ledger_map


def _fetch_portfolio_balances(ledger_id):
    """
    Fetch balances for a single ledger from RentVine.

    GET /accounting/ledgers/{ledgerID}/balances
    Returns the raw balances dict.
    """
    client = RentvineClient()
    data = client.get(f"/accounting/ledgers/{ledger_id}/balances")
    # Response may be a dict directly or wrapped in a list/data key
    if isinstance(data, list) and len(data) == 1:
        return data[0]
    return data


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
            self._handle_generate(options, month_start, month_end, month_str)

    def _handle_generate(self, options, month_start, month_end, month_str):
        """Generate mode: fetch transactions, build snapshots."""
        self.stdout.write(
            f"Generating distribution snapshots for {month_start} to {month_end}..."
        )

        # Resolve portfolios
        if options["portfolio"]:
            portfolios = Portfolio.objects.filter(
                rentvine_id=options["portfolio"], is_active=True
            )
            if not portfolios.exists():
                raise CommandError(
                    f"Portfolio with rentvine_id={options['portfolio']} not found or inactive."
                )
        else:
            portfolios = Portfolio.objects.filter(is_active=True).order_by("name")

        portfolio_count = portfolios.count()
        self.stdout.write(f"Processing {portfolio_count} portfolio(s).")

        # Fetch all transactions for the month ONCE
        self.stdout.write("Fetching transactions from RentVine...")
        transactions = _fetch_month_transactions(month_start, month_end)
        self.stdout.write(f"Fetched {len(transactions)} transactions for the month.")

        # Resolve ledger IDs once for undeposited balances
        self.stdout.write("Resolving portfolio ledger IDs...")
        ledger_map = _resolve_ledger_ids()
        self.stdout.write(f"Resolved {len(ledger_map)} ledger ID(s).")

        # ET-aware "today" for undeposited_as_of
        today_et = timezone.localtime(timezone.now()).date()

        # Build snapshots
        created = 0
        updated = 0
        errors = []

        for idx, portfolio in enumerate(portfolios, 1):
            try:
                payload = build_distribution_snapshot(
                    portfolio, month_start, month_end, transactions
                )

                # Fetch undeposited balance for every active portfolio.
                # Once captured (undeposited_as_of is set), freeze — never
                # re-fetch so a later re-run cannot overwrite the original value.
                existing_snapshot = (
                    PortfolioDistributionSnapshot.objects.filter(
                        portfolio=portfolio,
                        period_type="monthly",
                        period_start=month_start,
                    )
                    .values("undeposited_amount", "undeposited_as_of")
                    .first()
                )

                if existing_snapshot and existing_snapshot["undeposited_as_of"] is not None:
                    # Already captured — carry forward unchanged
                    payload["undeposited_amount"] = existing_snapshot["undeposited_amount"]
                    payload["undeposited_as_of"] = existing_snapshot["undeposited_as_of"]
                    undeposited_source = "frozen"
                else:
                    # First capture — fetch live from RentVine
                    ledger_id = ledger_map.get(portfolio.rentvine_id)
                    if ledger_id:
                        try:
                            balances = _fetch_portfolio_balances(ledger_id)
                            payload["undeposited_amount"] = get_undeposited_rent(balances)
                            payload["undeposited_as_of"] = today_et
                            undeposited_source = "fetched"
                        except Exception as bal_exc:
                            logger.warning(
                                "comms_distribution_balances_error",
                                extra={
                                    "portfolio": str(portfolio),
                                    "ledger_id": ledger_id,
                                    "error": str(bal_exc),
                                },
                            )
                            undeposited_source = "error"
                            # Default to $0 — don't crash the batch
                    else:
                        logger.warning(
                            "comms_distribution_missing_ledger",
                            extra={
                                "portfolio": str(portfolio),
                                "rentvine_id": portfolio.rentvine_id,
                            },
                        )
                        undeposited_source = "no-ledger"
                        # Default to $0 — undeposited stays at model default

                snapshot, was_created = upsert_distribution_snapshot(payload)

                label = "created" if was_created else "updated"
                if was_created:
                    created += 1
                else:
                    updated += 1

                prop_count = len(payload["line_items"])
                total_expected = sum(
                    float(item["expected"]) for item in payload["line_items"]
                )
                total_collected = sum(
                    float(item["collected"]) for item in payload["line_items"]
                )

                undeposited_str = ""
                if payload.get("undeposited_amount"):
                    undeposited_str = (
                        f" | undeposited=${payload['undeposited_amount']:,.2f}"
                        f" ({undeposited_source})"
                    )
                else:
                    undeposited_str = f" | undeposited=$0 ({undeposited_source})"

                self.stdout.write(
                    f"  [{idx}/{portfolio_count}] {portfolio.name}: {label} | "
                    f"properties={prop_count} | "
                    f"expected=${total_expected:,.2f} | "
                    f"collected=${total_collected:,.2f} | "
                    f"distribution=${payload['distribution_amount']:,.2f} "
                    f"({payload['distribution_date'] or 'no date'})"
                    f"{undeposited_str}"
                )

            except Exception as exc:
                msg = f"  [{idx}/{portfolio_count}] {portfolio.name}: ERROR — {exc}"
                self.stderr.write(self.style.ERROR(msg))
                errors.append(msg)
                logger.exception(
                    "comms_distribution_snapshot_error",
                    extra={
                        "portfolio": str(portfolio),
                        "month": str(month_start),
                        "error": str(exc),
                    },
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: created={created}  updated={updated}  "
                f"errors={len(errors)}"
            )
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

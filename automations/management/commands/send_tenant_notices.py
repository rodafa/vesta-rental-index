"""
Management command: daily tenant delinquency notice cadence.

Schedule: runs daily via Railway cron service. On day 5 sends 'reminder',
day 6 sends 'pay_or_quit', day 11 sends 'final'. All other days: no-op.

Safety modes mirror the existing comms pattern:
    --dry-run   No DB writes, no emails - prints what would happen + HTML previews.
    --sandbox   SendGrid sandbox mode - validates but delivers nothing.
    --live      Real sends. Requires explicit opt-in.

Usage:
    python manage.py send_tenant_notices --dry-run
    python manage.py send_tenant_notices --sandbox
    python manage.py send_tenant_notices --live
    python manage.py send_tenant_notices --live --as-of 2026-09-05
    python manage.py send_tenant_notices --dry-run --scheduled
    python manage.py send_tenant_notices --live --lease-id 161

With --scheduled (unattended cron): only 'reminder' (day 5) may dispatch.
Days 6 and 11 log the withheld kind and exit 0. Without --scheduled, a
human run selects the kind from the date as before.
"""

import logging
from datetime import date
from pathlib import Path

from django.conf import settings as _settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.utils import timezone

from automations.models import TenantNotice, TenantNoticeHold
from automations.selectors import get_delinquent_leases, resolve_lease_tenant_emails
from core.models import Lease
from integrations.rentvine.client import RentvineClient

logger = logging.getLogger(__name__)

# Day-of-month -> notice kind
CADENCE = {
    5: "reminder",
    6: "pay_or_quit",
    11: "final",
}

# The ONLY kind that --scheduled (unattended) mode may dispatch.
# Hard-coded by design: pay_or_quit and final require human approval.
# Do not make this configurable by env var or setting.
SCHEDULED_ALLOWED_KIND = "reminder"


class Command(BaseCommand):
    help = (
        "Send tenant delinquency notices. Runs daily; sends only on "
        "cadence days (5th, 6th, 11th of the month)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-of",
            type=str,
            default=None,
            help=(
                "Override today's date (YYYY-MM-DD). For testing or "
                "backfilling a missed day."
            ),
        )
        parser.add_argument(
            "--scheduled",
            action="store_true",
            default=False,
            help=(
                "Unattended cron mode. May ONLY dispatch 'reminder' (day 5). "
                "On cadence days 6/11, logs the withheld kind and exits 0. "
                "Human-triggered kinds require a manual run without this flag."
            ),
        )
        parser.add_argument(
            "--lease-id",
            type=int,
            default=None,
            help=(
                "Restrict to a single Rentvine lease ID (the numeric ID from "
                "RentVine, not the local database PK). The lease must appear "
                "in the delinquent set after all selector exclusions apply. "
                "Mutually exclusive with --scheduled."
            ),
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="No DB writes, no emails. Prints report + writes HTML previews.",
        )
        mode.add_argument(
            "--sandbox",
            action="store_true",
            default=False,
            help="SendGrid sandbox mode - validates payloads but delivers nothing.",
        )
        mode.add_argument(
            "--live",
            action="store_true",
            default=False,
            help="Real sends to real tenants.",
        )

    def handle(self, *args, **options):
        as_of = (
            date.fromisoformat(options["as_of"])
            if options["as_of"]
            else timezone.localdate()
        )
        dry_run = options["dry_run"]
        sandbox = options["sandbox"]
        mode_label = "dry-run" if dry_run else "sandbox" if sandbox else "live"
        scheduled = options["scheduled"]
        target_lease_id = options["lease_id"]
        if target_lease_id is not None and scheduled:
            raise CommandError(
                "--lease-id and --scheduled are mutually exclusive. "
                "An unattended run never targets a single lease."
            )

        kind = CADENCE.get(as_of.day)
        if kind is None:
            self.stdout.write(
                f"Day {as_of.day} is not a cadence day. Nothing to send."
            )
            return

        # -- Scheduled-mode gate ----------------------------------------------
        # Unattended runs may ONLY dispatch SCHEDULED_ALLOWED_KIND ('reminder').
        # Any other cadence kind is withheld — a human must trigger it manually.
        if scheduled and kind != SCHEDULED_ALLOWED_KIND:
            logger.info(
                "tenant_notice_scheduled_skip",
                extra={
                    "date": str(as_of),
                    "day": as_of.day,
                    "kind_withheld": kind,
                    "mode": "scheduled",
                    "send_mode": mode_label,
                    "reason": (
                        f"scheduled mode may only dispatch "
                        f"'{SCHEDULED_ALLOWED_KIND}'"
                    ),
                },
            )
            self.stdout.write(
                f"Scheduled mode: withheld '{kind}' on day {as_of.day}. "
                f"Only '{SCHEDULED_ALLOWED_KIND}' is permitted unattended."
            )
            return

        period_key = as_of.strftime("%Y-%m")
        self.stdout.write(
            f"Tenant notices: kind={kind}  period={period_key}  "
            f"as_of={as_of}  mode={mode_label}  scheduled={scheduled}  "
            f"lease_id={target_lease_id}"
        )

        client = RentvineClient()

        # Step 1: Identify delinquent leases (holds NOT filtered here - R8)
        result = get_delinquent_leases(
            as_of_date=as_of, client=client, dry_run=dry_run
        )
        delinquent = result["delinquent"]

        # R9d: skipped_no_lease_id surfaced prominently
        self.stdout.write(
            f"Delinquent leases: {len(delinquent)}  |  "
            f"no_lease_id={result['skipped_no_lease_id']}  "
            f"voided={result['skipped_voided']}  "
            f"below_min={result['skipped_below_minimum']}  "
            f"no_address={result['skipped_no_address']}  "
            f"status_excluded={result['skipped_status_excluded']}  "
            f"status_unknown={result['skipped_status_unknown']}  "
            f"charges_scanned={result['total_charges_scanned']}"
        )
        if dry_run:
            self.stdout.write(
                f"Display-only charges: "
                f"scanned={result['display_only_charges_scanned']}  "
                f"voided={result['display_only_skipped_voided']}  "
                f"no_lease_id={result['display_only_skipped_no_lease_id']}"
            )

        if dry_run and result.get("diagnostics", {}).get("voided_value_forms"):
            self.stdout.write(
                f"  isVoided value forms seen: "
                f"{result['diagnostics']['voided_value_forms']}"
            )

        # -- Single-lease filter -----------------------------------------------
        if target_lease_id is not None:
            match = [
                e for e in delinquent
                if e["rentvine_lease_id"] == target_lease_id
            ]
            if not match:
                logger.info(
                    "tenant_notice_lease_not_in_delinquent_set",
                    extra={
                        "rentvine_lease_id": target_lease_id,
                        "date": str(as_of),
                        "kind": kind,
                        "delinquent_count": len(delinquent),
                        "reason": (
                            "lease not found in delinquent set after selector "
                            "exclusions (voided, below minimum, status "
                            "excluded, no address, or no lease id)"
                        ),
                    },
                )
                self.stdout.write(
                    f"Lease {target_lease_id} not in delinquent set "
                    f"({len(delinquent)} leases qualified). Nothing to send."
                )
                return
            delinquent = match
            self.stdout.write(
                f"Single-lease mode: targeting Rentvine lease "
                f"{target_lease_id}"
            )

        if not delinquent:
            self.stdout.write("No delinquent leases - done.")
            return

        # Pre-load holds for all delinquent lease RentVine IDs (R8)
        delinquent_rv_ids = [e["rentvine_lease_id"] for e in delinquent]
        holds = self._load_holds(delinquent_rv_ids, period_key)

        # Step 2: Process each lease
        sent = 0
        previewed = 0
        skipped_dedupe = 0
        skipped_no_email = 0
        skipped_hold = 0
        skipped_no_prior_notice = 0
        failed = 0

        resident_portal_url = getattr(_settings, "RESIDENT_PORTAL_URL", "")

        # Dry-run: prepare preview output directory
        preview_dir = None
        if dry_run:
            preview_dir = Path("tenant_notice_previews") / period_key / kind
            preview_dir.mkdir(parents=True, exist_ok=True)

        for entry in delinquent:
            rv_lease_id = entry["rentvine_lease_id"]
            balance = entry["balance"]
            display_bal = entry["display_balance"]
            address = entry["property_address"]

            # R8: Hold check in the command, not the selector
            hold = holds.get(rv_lease_id)
            if hold is not None:
                skipped_hold += 1
                if dry_run:
                    self.stdout.write(
                        f"  Lease {rv_lease_id}: HELD - "
                        f"reason={hold['reason']!r}  "
                        f"hold_period={hold['period_key']!r}  "
                        f"balance=${balance:,.2f}  "
                        f"display=${display_bal:,.2f}  address={address}"
                    )
                else:
                    logger.info(
                        "tenant_notice_held",
                        extra={
                            "rentvine_lease_id": rv_lease_id,
                            "hold_reason": hold["reason"],
                            "balance": str(balance),
                        },
                    )
                continue

            # Part B: day-11 requires a prior day-6 with delivery_status="sent"
            if kind == "final":
                prior_exists = TenantNotice.objects.filter(
                    rentvine_lease_id=rv_lease_id,
                    period_key=period_key,
                    kind="pay_or_quit",
                    delivery_status="sent",
                ).exists()
                if not prior_exists:
                    skipped_no_prior_notice += 1
                    logger.warning(
                        "tenant_notice_no_prior_pay_or_quit",
                        extra={
                            "rentvine_lease_id": rv_lease_id,
                            "period_key": period_key,
                        },
                    )
                    if dry_run:
                        self.stdout.write(
                            f"  Lease {rv_lease_id}: SKIPPED - no prior "
                            f"pay_or_quit sent for {period_key}  "
                            f"balance=${balance:,.2f}  "
                            f"display=${display_bal:,.2f}  address={address}"
                        )
                    else:
                        self.stdout.write(
                            f"  Lease {rv_lease_id}: no prior pay_or_quit "
                            f"for {period_key} - skipped"
                        )
                    continue

            # Resolve tenant emails (R7: API first)
            tenant_info = resolve_lease_tenant_emails(rv_lease_id, client)
            emails = tenant_info["emails"]
            missing = tenant_info["missing_recipients"]

            if not emails:
                skipped_no_email += 1
                logger.warning(
                    "tenant_notice_no_emails",
                    extra={
                        "rentvine_lease_id": rv_lease_id,
                        "missing_recipients": missing,
                    },
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"  Lease {rv_lease_id}: no tenant emails - skipped  "
                        f"missing={missing}"
                    )
                )
                continue

            # Render the notice
            template_name = f"automations/notices/{kind}.html"
            context = {
                "property_address": address,
                "display_balance": f"{display_bal:,.2f}",
                "period_key": period_key,
                "resident_portal_url": resident_portal_url,
                "recipient_emails": emails,
            }
            try:
                body_html = render_to_string(template_name, context)
            except Exception:
                logger.exception(
                    "tenant_notice_render_failed",
                    extra={
                        "rentvine_lease_id": rv_lease_id,
                        "template": template_name,
                    },
                )
                failed += 1
                continue

            subject = self._build_subject(kind, address, period_key)

            # --- DRY RUN: preview only, no DB, no send (R6) ---
            if dry_run:
                preview_file = preview_dir / f"lease_{rv_lease_id}.html"
                preview_file.write_text(body_html, encoding="utf-8")
                self.stdout.write(
                    f"  Lease {rv_lease_id}: "
                    f"decision=${balance:,.2f}  "
                    f"display=${display_bal:,.2f}  "
                    f"to={emails}  missing={missing}  "
                    f"address={address}  "
                    f"status_id={entry.get('lease_status_id')}  "
                    f"preview={preview_file}"
                )
                previewed += 1
                continue

            # --- SANDBOX / LIVE: create evidence row, then send ---

            # R2: resolve local FK (nullable), always store rentvine_lease_id
            lease_obj = self._get_lease(rv_lease_id)

            # Dedupe: IntegrityError on (rentvine_lease_id, kind, period_key)
            try:
                notice = TenantNotice.objects.create(
                    kind=kind,
                    period_key=period_key,
                    lease=lease_obj,  # may be None
                    rentvine_lease_id=rv_lease_id,
                    recipient_emails=emails,
                    missing_recipients=missing,
                    property_address=address,
                    balance_owed=balance,
                    display_balance=display_bal,
                    delivery_status="pending",
                )
            except IntegrityError:
                skipped_dedupe += 1
                logger.info(
                    "tenant_notice_duplicate",
                    extra={
                        "rentvine_lease_id": rv_lease_id,
                        "kind": kind,
                        "period_key": period_key,
                    },
                )
                self.stdout.write(
                    f"  Lease {rv_lease_id}: already sent {kind} for "
                    f"{period_key} - skipped"
                )
                continue

            # Send via SendGrid (R4: keyword-only signature)
            try:
                sg_result = self._send_notice(
                    notice=notice,
                    emails=emails,
                    subject=subject,
                    body_html=body_html,
                    sandbox=sandbox,
                )
                notice.delivery_status = "sent"
                notice.sendgrid_message_id = sg_result.get("message_id", "")
                notice.save(
                    update_fields=[
                        "delivery_status",
                        "sendgrid_message_id",
                    ]
                )
                sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Lease {rv_lease_id}: {kind} sent to {emails} "
                        f"(${balance:,.2f})"
                    )
                )
            except Exception as exc:
                notice.delivery_status = "failed"
                notice.error_message = str(exc)[:1000]
                notice.save(
                    update_fields=["delivery_status", "error_message"]
                )
                failed += 1
                logger.exception(
                    "tenant_notice_send_failed",
                    extra={
                        "notice_id": notice.pk,
                        "rentvine_lease_id": rv_lease_id,
                    },
                )
                self.stderr.write(
                    self.style.ERROR(
                        f"  Lease {rv_lease_id}: send FAILED - {exc}"
                    )
                )

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done ({mode_label}): previewed={previewed}  "
                    f"hold={skipped_hold}  no_email={skipped_no_email}  "
                    f"no_prior_notice={skipped_no_prior_notice}  "
                    f"failed={failed}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done ({mode_label}): sent={sent}  "
                    f"dedupe={skipped_dedupe}  hold={skipped_hold}  "
                    f"no_email={skipped_no_email}  "
                    f"no_prior_notice={skipped_no_prior_notice}  "
                    f"failed={failed}"
                )
            )

    def _build_subject(self, kind, address, period_key):
        """Build the email subject line."""
        labels = {
            "reminder": "Rent Payment Reminder",
            "pay_or_quit": "Notice to Pay Rent or Quit",
            "final": "Final Notice - Rent Payment Required",
        }
        label = labels.get(kind, "Rent Notice")
        return f"{label} - {address} - {period_key}"

    def _get_lease(self, rentvine_lease_id):
        """Look up the local Lease object by RentVine ID. Returns None if missing."""
        try:
            return Lease.objects.get(rentvine_id=rentvine_lease_id)
        except Lease.DoesNotExist:
            return None

    def _load_holds(self, rentvine_lease_ids, period_key):
        """
        Load holds matching these RentVine lease IDs for the given period.

        Returns a dict: {rentvine_lease_id: {"reason": str, "period_key": str}}
        Matches on exact period_key OR blanket hold (period_key="").
        """
        from django.db.models import Q

        holds = {}
        qs = TenantNoticeHold.objects.filter(
            Q(period_key=period_key) | Q(period_key=""),
            rentvine_lease_id__in=rentvine_lease_ids,
        ).values_list("rentvine_lease_id", "reason", "period_key")
        for rv_id, reason, hold_period in qs:
            holds[rv_id] = {"reason": reason, "period_key": hold_period}
        return holds

    def _send_notice(self, *, notice, emails, subject, body_html, sandbox):
        """
        Send the notice email via SendGrid.

        Uses comms.services.send_email() - keyword-only signature (R4).
        Returns {"message_id": str, "status_code": int}.
        """
        from comms.services import send_email

        return send_email(
            to_emails=emails,
            subject=subject,
            html_content=body_html,
            sandbox=sandbox,
        )

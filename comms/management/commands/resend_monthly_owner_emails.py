"""
Management command: resend monthly owner emails that were sent with a stale template.

Uses each draft's stored body_html (no reassembly) and the corrected SendGrid
template.  Dry-run by default — pass --send to deliver for real.

Usage:
    # Dry run — preview what would be resent:
    python manage.py resend_monthly_owner_emails --month 2026-09 --sent-before 2026-10-03T00:00:00Z

    # Send for real, first 10:
    python manage.py resend_monthly_owner_emails --month 2026-09 --sent-before 2026-10-03T00:00:00Z --send --user rodrigo --limit 10

    # Send all eligible:
    python manage.py resend_monthly_owner_emails --month 2026-09 --sent-before 2026-10-03T00:00:00Z --send --user rodrigo
"""

import logging
from datetime import date, datetime, timezone as _tz

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from comms.services import resend_monthly_owner_emails

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Resend monthly owner emails that were sent before a cutoff date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            type=str,
            required=True,
            help="Target month (YYYY-MM).",
        )
        parser.add_argument(
            "--sent-before",
            type=str,
            required=True,
            help="ISO 8601 cutoff — only drafts sent before this are eligible.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of drafts to process.",
        )
        parser.add_argument(
            "--send",
            action="store_true",
            default=False,
            help="Actually send. Without this flag the command is a dry run.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Username of the staff member authorising the send (required with --send).",
        )

    def handle(self, *args, **options):
        # Parse --month
        try:
            parts = options["month"].split("-")
            period_start = date(int(parts[0]), int(parts[1]), 1)
        except (ValueError, IndexError):
            raise CommandError(f"Invalid --month: {options['month']} (expected YYYY-MM)")

        # Parse --sent-before
        try:
            sent_before = datetime.fromisoformat(options["sent_before"])
            if sent_before.tzinfo is None:
                sent_before = sent_before.replace(tzinfo=_tz.utc)
        except (ValueError, TypeError):
            raise CommandError(
                f"Invalid --sent-before: {options['sent_before']} (expected ISO 8601)"
            )

        is_live = options["send"]
        dry_run = not is_live

        # Resolve acting user
        acting_user = None
        if is_live:
            if not options["user"]:
                raise CommandError("--user is required when --send is used.")
            try:
                acting_user = User.objects.get(username=options["user"])
            except User.DoesNotExist:
                raise CommandError(f"User '{options['user']}' not found.")

        mode_label = "LIVE" if is_live else "DRY RUN"
        self.stdout.write(
            f"Resend monthly owner emails for {period_start.strftime('%B %Y')}\n"
            f"  sent-before: {sent_before.isoformat()}\n"
            f"  limit: {options['limit'] or 'none'}\n"
            f"  mode: {mode_label}\n"
        )

        sent = 0
        failed = 0
        would_send = 0

        for item in resend_monthly_owner_emails(
            period_start=period_start,
            sent_before=sent_before,
            acting_user=acting_user,
            limit=options["limit"],
            dry_run=dry_run,
        ):
            draft = item["draft"]
            action = item["action"]
            email = draft.recipient_email or draft.owner.email

            if action == "would_send":
                self.stdout.write(f"  {email} (draft {draft.pk}): WOULD SEND")
                would_send += 1

            elif action == "sent":
                sg_status = item["result"].get("sendgrid_status", "?")
                self.stdout.write(
                    self.style.SUCCESS(f"  {email} (draft {draft.pk}): SENT ({sg_status})")
                )
                logger.info("comms_resend_sent", extra={
                    "draft_id": draft.pk,
                    "recipient": email,
                    "sendgrid_status": sg_status,
                })
                sent += 1

            elif action == "failed":
                error_msg = item.get("error", "unknown error")
                self.stderr.write(
                    self.style.ERROR(f"  {email} (draft {draft.pk}): FAILED: {error_msg}")
                )
                logger.error("comms_resend_failed", extra={
                    "draft_id": draft.pk,
                    "recipient": email,
                    "error": error_msg,
                })
                failed += 1

        # Summary
        eligible = sent + failed + would_send
        remaining = eligible - sent
        self.stdout.write("")
        self.stdout.write(
            f"Done: eligible={eligible}  sent={sent}  failed={failed}  "
            f"remaining={remaining}"
        )

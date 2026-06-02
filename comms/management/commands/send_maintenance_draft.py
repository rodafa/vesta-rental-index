"""
Management command: send a single maintenance email draft via SendGrid.

Usage:
    python manage.py send_maintenance_draft <draft_id> --acting-user <username> --sandbox
    python manage.py send_maintenance_draft <draft_id> --acting-user <username> --test-email addr@example.com
    python manage.py send_maintenance_draft <draft_id> --acting-user <username> --live

Exactly one of --sandbox, --test-email, or --live is required.
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from comms.models import EmailDraft
from comms.services import send_draft


class Command(BaseCommand):
    help = "Send a single maintenance email draft via SendGrid."

    def add_arguments(self, parser):
        parser.add_argument("draft_id", type=int, help="EmailDraft PK to send.")
        parser.add_argument(
            "--acting-user",
            required=True,
            help="Username of the staff member authorising the send.",
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
            draft = EmailDraft.objects.get(pk=options["draft_id"])
        except EmailDraft.DoesNotExist:
            raise CommandError(f"EmailDraft {options['draft_id']} not found.")

        try:
            user = User.objects.get(username=options["acting_user"])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['acting_user']}' not found.")

        recipient_override = options["test_email"] or None
        sandbox = options["sandbox"]

        self.stdout.write(
            f"Sending draft {draft.pk} for {draft.owner} "
            f"(mode={'sandbox' if sandbox else 'test' if recipient_override else 'live'})..."
        )

        try:
            result = send_draft(
                draft=draft,
                acting_user=user,
                recipient_override=recipient_override,
                sandbox=sandbox,
            )
        except (PermissionError, ValueError, RuntimeError) as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: sendgrid_status={result['sendgrid_status']}  "
                f"mode={result['mode']}  recipient={result['recipient']}"
            )
        )

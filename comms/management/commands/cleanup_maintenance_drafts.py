"""
One-time cleanup: delete all maintenance EmailDraft rows.

These are legacy owner-grain drafts from the old single-layer pipeline.
No maintenance emails have ever been sent, so all rows are safe to delete.

Usage:
    python manage.py cleanup_maintenance_drafts
"""

from django.core.management.base import BaseCommand

from comms.models import EmailDraft


class Command(BaseCommand):
    help = "Delete all maintenance EmailDraft rows (one-time cleanup)."

    def handle(self, *args, **options):
        sent_count = EmailDraft.objects.filter(
            product="maintenance", status="sent"
        ).count()

        if sent_count:
            self.stderr.write(
                self.style.ERROR(
                    f"ABORT: found {sent_count} SENT maintenance drafts. "
                    f"This command is only safe when zero have been sent."
                )
            )
            return

        qs = EmailDraft.objects.filter(product="maintenance")
        total = qs.count()

        if total == 0:
            self.stdout.write("No maintenance EmailDrafts to delete.")
            return

        qs.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {total} maintenance EmailDraft rows.")
        )

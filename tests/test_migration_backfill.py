"""
Migration backfill test: verifies that 0003_emaildraft_period_fields correctly
copies week_start/week_end into period_type/period_start/period_end for all
existing rows.

Uses Django's MigrationExecutor to step through real schema states.
"""

from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TestMigration0003Backfill(TransactionTestCase):
    """Create an EmailDraft at the 0002 schema, migrate to 0003, verify backfill."""

    migrate_from = [
        ("comms", "0002_remove_emaildraft_comms_email_owner_i_8c607c_idx_and_more"),
    ]
    migrate_to = [
        ("comms", "0003_emaildraft_period_fields"),
    ]

    def setUp(self):
        # Roll back to pre-0003 state
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        executor.loader.build_graph()

        # Get historical models at the 0002 state
        apps = executor.loader.project_state(self.migrate_from).apps
        Owner = apps.get_model("core", "Owner")
        EmailDraft = apps.get_model("comms", "EmailDraft")

        # Create the FK graph EmailDraft needs: just an Owner
        self.owner = Owner.objects.create(
            rentvine_contact_id=900,
            name="Backfill Test Owner",
            first_name="Backfill",
            is_active=True,
        )

        # Create an EmailDraft in the OLD shape — only week_start/week_end exist
        self.draft = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner,
            subject="Weekly Maintenance Update — May 25 – May 31, 2026",
            body_html="<p>Test</p>",
            status="draft",
            week_start=date(2026, 5, 25),
            week_end=date(2026, 5, 31),
        )
        self.draft_pk = self.draft.pk

        # Now migrate forward to 0003 (runs backfill_period_fields)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_backfill_populates_period_fields(self):
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        draft = EmailDraft.objects.get(pk=self.draft_pk)

        assert draft.period_type == "weekly"
        assert draft.period_start == date(2026, 5, 25)
        assert draft.period_end == date(2026, 5, 31)
        # Original fields unchanged
        assert draft.week_start == date(2026, 5, 25)
        assert draft.week_end == date(2026, 5, 31)

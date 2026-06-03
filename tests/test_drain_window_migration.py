"""
Drain-window migration test: verifies that 0004's defensive backfill catches
rows written by old Deploy-1 code that only populated week_start/week_end
but left period_start/period_end NULL.

Uses Django's MigrationExecutor to step through real schema states.
"""

from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TestMigration0004DrainWindow(TransactionTestCase):
    """
    Simulate a drain-window row: migrate to 0003, create a row with
    period_start=None (old code wrote week_* only), migrate to 0004,
    verify defensive backfill sets period_start=week_start and
    period_end=week_end, and that period_start is non-null after
    the AlterField makes the column NOT NULL.
    """

    migrate_from = [
        ("comms", "0003_emaildraft_period_fields"),
    ]
    migrate_to = [
        ("comms", "0004_emaildraft_contract"),
    ]

    def setUp(self):
        # Roll back to the 0003 state (expand phase — period_* is nullable)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        executor.loader.build_graph()

        # Get historical models at the 0003 state
        apps = executor.loader.project_state(self.migrate_from).apps
        Owner = apps.get_model("core", "Owner")
        EmailDraft = apps.get_model("comms", "EmailDraft")

        # Create the FK graph: just an Owner
        self.owner = Owner.objects.create(
            rentvine_contact_id=901,
            name="Drain Window Test Owner",
            first_name="Drain",
            is_active=True,
        )

        # Simulate a row written by old Deploy-1 code during drain window:
        # week_start/week_end populated, period_start/period_end left NULL.
        self.draft = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner,
            subject="Weekly Maintenance Update — May 18 – May 24, 2026",
            body_html="<p>Drain window row</p>",
            status="draft",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            period_type="weekly",
            period_start=None,
            period_end=None,
        )
        self.draft_pk = self.draft.pk

        # Migrate forward to 0004 (runs defensive_backfill, then AlterField)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_defensive_backfill_populates_period_fields(self):
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        draft = EmailDraft.objects.get(pk=self.draft_pk)

        assert draft.period_type == "weekly"
        assert draft.period_start == date(2026, 5, 18)
        assert draft.period_end == date(2026, 5, 24)
        # period_start is non-null (AlterField succeeded after backfill)
        assert draft.period_start is not None

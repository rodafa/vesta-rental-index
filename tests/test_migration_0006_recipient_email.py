"""
Migration 0006 test: verifies recipient_email backfill, dedupe of monthly
notes, and partial unique constraint behavior.

Seeds production-realistic data BEFORE 0006 and asserts:
  - All 4 maintenance drafts survive untouched (blank + shared email)
  - Duplicate monthly_owner_notes rows are deduped to 1
  - The partial constraint blocks a 2nd monthly note with same email+period
  - The partial constraint does NOT block maintenance duplicates

Uses Django's MigrationExecutor to step through real schema states.
"""

from datetime import date

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TestMigration0006RecipientEmail(TransactionTestCase):

    migrate_from = [
        ("comms", "0005_emaildraft_approved_generated_note"),
    ]
    migrate_to = [
        ("comms", "0006_emaildraft_recipient_email"),
    ]

    def setUp(self):
        # Roll back to pre-0006 state
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        executor.loader.build_graph()

        apps = executor.loader.project_state(self.migrate_from).apps
        Owner = apps.get_model("core", "Owner")
        EmailDraft = apps.get_model("comms", "EmailDraft")

        # -- Owners --
        self.owner_blank_1 = Owner.objects.create(
            rentvine_contact_id=601,
            name="Blank Email Owner 1",
            first_name="Blank1",
            email="",
            is_active=True,
        )
        self.owner_blank_2 = Owner.objects.create(
            rentvine_contact_id=602,
            name="Blank Email Owner 2",
            first_name="Blank2",
            email="",
            is_active=True,
        )
        self.owner_shared_1 = Owner.objects.create(
            rentvine_contact_id=603,
            name="Shared Email Owner 1",
            first_name="Shared1",
            email="x@x.com",
            is_active=True,
        )
        self.owner_shared_2 = Owner.objects.create(
            rentvine_contact_id=604,
            name="Shared Email Owner 2",
            first_name="Shared2",
            email="x@x.com",
            is_active=True,
        )
        self.owner_monthly_1 = Owner.objects.create(
            rentvine_contact_id=605,
            name="Monthly Owner 1",
            first_name="Monthly1",
            email="y@y.com",
            is_active=True,
        )
        self.owner_monthly_2 = Owner.objects.create(
            rentvine_contact_id=606,
            name="Monthly Owner 2",
            first_name="Monthly2",
            email="y@y.com",
            is_active=True,
        )

        # -- 2 maintenance drafts, blank email, same (weekly, 2026-05-25) --
        self.maint_blank_1 = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner_blank_1,
            subject="Maint Blank 1",
            body_html="<p>1</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )
        self.maint_blank_2 = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner_blank_2,
            subject="Maint Blank 2",
            body_html="<p>2</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )

        # -- 2 maintenance drafts, same email x@x.com, same (weekly, 2026-05-25) --
        self.maint_shared_1 = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner_shared_1,
            subject="Maint Shared 1",
            body_html="<p>3</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )
        self.maint_shared_2 = EmailDraft.objects.create(
            product="maintenance",
            owner=self.owner_shared_2,
            subject="Maint Shared 2",
            body_html="<p>4</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )

        # -- 2 monthly_owner_notes drafts, same email y@y.com, same period --
        self.monthly_1 = EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=self.owner_monthly_1,
            subject="Monthly 1",
            body_html="<p>5</p>",
            status="draft",
            period_type="monthly",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )
        self.monthly_2 = EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=self.owner_monthly_2,
            subject="Monthly 2",
            body_html="<p>6</p>",
            status="draft",
            period_type="monthly",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )

        self.maint_blank_1_pk = self.maint_blank_1.pk
        self.maint_blank_2_pk = self.maint_blank_2.pk
        self.maint_shared_1_pk = self.maint_shared_1.pk
        self.maint_shared_2_pk = self.maint_shared_2.pk
        self.monthly_1_pk = self.monthly_1.pk
        self.monthly_2_pk = self.monthly_2.pk

        # Migrate forward to 0006
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_all_maintenance_drafts_survive(self):
        """All 4 maintenance drafts must be untouched after migration."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        maint_count = EmailDraft.objects.filter(product="maintenance").count()
        assert maint_count == 4, f"Expected 4 maintenance drafts, got {maint_count}"

        # Each one still exists
        for pk in [
            self.maint_blank_1_pk,
            self.maint_blank_2_pk,
            self.maint_shared_1_pk,
            self.maint_shared_2_pk,
        ]:
            assert EmailDraft.objects.filter(pk=pk).exists(), f"Maintenance draft {pk} missing"

    def test_maintenance_blank_email_backfilled(self):
        """Blank-email owners get recipient_email=''."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        d = EmailDraft.objects.get(pk=self.maint_blank_1_pk)
        assert d.recipient_email == ""

    def test_maintenance_shared_email_backfilled(self):
        """Shared-email owners get recipient_email='x@x.com'."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        d = EmailDraft.objects.get(pk=self.maint_shared_1_pk)
        assert d.recipient_email == "x@x.com"

    def test_monthly_notes_deduped_to_one(self):
        """The two monthly_owner_notes with same email+period deduped to 1."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        count = EmailDraft.objects.filter(product="monthly_owner_notes").count()
        assert count == 1, f"Expected 1 monthly draft after dedupe, got {count}"

    def test_monthly_notes_kept_most_recent(self):
        """The surviving monthly draft is the one with the higher PK."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        surviving = EmailDraft.objects.get(product="monthly_owner_notes")
        assert surviving.pk == self.monthly_2_pk, (
            f"Expected pk={self.monthly_2_pk} (most recent), got {surviving.pk}"
        )

    def test_second_monthly_note_same_email_raises(self):
        """Partial constraint blocks a 2nd monthly note with same email+period."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        Owner = self.apps.get_model("core", "Owner")

        extra_owner = Owner.objects.create(
            rentvine_contact_id=607,
            name="Extra Monthly Owner",
            first_name="Extra",
            email="y@y.com",
            is_active=True,
        )

        with self.assertRaises(IntegrityError):
            EmailDraft.objects.create(
                product="monthly_owner_notes",
                owner=extra_owner,
                recipient_email="y@y.com",
                subject="Colliding monthly",
                body_html="<p>collision</p>",
                status="draft",
                period_type="monthly",
                period_start=date(2026, 5, 1),
                period_end=date(2026, 5, 31),
            )

    def test_second_maintenance_same_email_allowed(self):
        """Maintenance drafts with same email+period are NOT blocked."""
        EmailDraft = self.apps.get_model("comms", "EmailDraft")
        Owner = self.apps.get_model("core", "Owner")

        extra_owner = Owner.objects.create(
            rentvine_contact_id=608,
            name="Extra Maint Owner",
            first_name="ExtraM",
            email="x@x.com",
            is_active=True,
        )

        # Should NOT raise — maintenance is not covered by the partial constraint
        draft = EmailDraft.objects.create(
            product="maintenance",
            owner=extra_owner,
            recipient_email="x@x.com",
            subject="Third maint same email",
            body_html="<p>ok</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )
        assert draft.pk is not None

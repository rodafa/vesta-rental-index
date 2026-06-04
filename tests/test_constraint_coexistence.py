"""
Constraint coexistence test: validates the period-aware unique constraint
(product, owner, period_type, period_start) governs draft uniqueness.

After the contract migration, week_start/week_end are gone and only the
period-aware constraint remains. Weekly and monthly rows CAN share the same
period_start if they have different period_types.
"""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from comms.models import EmailDraft
from core.models import Owner


@pytest.fixture
def owner():
    return Owner.objects.create(
        rentvine_contact_id=800,
        name="Constraint Test Owner",
        first_name="Constraint",
        email="constraint@example.com",
        is_active=True,
    )


@pytest.mark.django_db
class TestConstraintCoexistence:
    def test_weekly_and_monthly_coexist_same_period_start(self, owner):
        """Weekly and monthly drafts with the same period_start coexist (different period_type)."""
        EmailDraft.objects.create(
            product="maintenance",
            owner=owner,
            recipient_email="constraint@example.com",
            subject="Weekly Update",
            body_html="<p>Weekly</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )
        EmailDraft.objects.create(
            product="maintenance",
            owner=owner,
            recipient_email="constraint@example.com",
            subject="Monthly Update",
            body_html="<p>Monthly</p>",
            status="draft",
            period_type="monthly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )

        assert EmailDraft.objects.filter(owner=owner).count() == 2

    def test_duplicate_weekly_raises_integrity_error(self, owner):
        """A second weekly draft with the same grain raises IntegrityError."""
        EmailDraft.objects.create(
            product="maintenance",
            owner=owner,
            recipient_email="constraint@example.com",
            subject="Weekly Update",
            body_html="<p>First</p>",
            status="draft",
            period_type="weekly",
            period_start=date(2026, 5, 25),
            period_end=date(2026, 5, 31),
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EmailDraft.objects.create(
                    product="maintenance",
                    owner=owner,
                    recipient_email="constraint@example.com",
                    subject="Duplicate Weekly",
                    body_html="<p>Dupe</p>",
                    status="draft",
                    period_type="weekly",
                    period_start=date(2026, 5, 25),
                    period_end=date(2026, 5, 31),
                )

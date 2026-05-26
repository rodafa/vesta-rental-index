"""
Regression test: send_maintenance_emails writes frozen MaintenanceEmailMeld
snapshots per meld at send time.

Proves:
(a) One snapshot row per meld is created with correct summary_text, cost,
    section, and the correct per-meld portfolio (not the owner's first portfolio).
(b) Editing a meld's summary AFTER send does NOT alter the snapshot.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from accounting.models import Bill, BillCharge
from maintenance.models import (
    MaintenanceEmailMeld,
    MaintenanceEmailSend,
    Meld,
)
from maintenance.services.send_maintenance_emails import send_maintenance_emails
from properties.models import Owner, Portfolio, Property, Unit


class MaintenanceEmailSnapshotTest(TestCase):
    """Test that send creates per-meld frozen snapshots with correct portfolio."""

    def setUp(self):
        """
        Create an owner with TWO portfolios, each with a property/unit,
        and one meld per unit — so we can verify portfolio is derived per-meld.
        """
        # Two portfolios
        self.portfolio_a = Portfolio.objects.create(
            rentvine_id=901, name="Portfolio Alpha", slug="alpha"
        )
        self.portfolio_b = Portfolio.objects.create(
            rentvine_id=902, name="Portfolio Beta", slug="beta"
        )

        # Owner linked to both portfolios
        self.owner = Owner.objects.create(
            rentvine_contact_id=8001,
            name="Test Owner",
            email="testowner@example.com",
            is_active=True,
        )
        self.owner.portfolios.add(self.portfolio_a, self.portfolio_b)

        # Property + Unit under Portfolio A
        self.prop_a = Property.objects.create(
            rentvine_id=3001,
            address_line_1="100 Alpha St",
            portfolio=self.portfolio_a,
            is_active=True,
        )
        self.unit_a = Unit.objects.create(
            rentvine_id=4001,
            property=self.prop_a,
            address_line_1="100 Alpha St",
            is_active=True,
        )

        # Property + Unit under Portfolio B
        self.prop_b = Property.objects.create(
            rentvine_id=3002,
            address_line_1="200 Beta Ave",
            portfolio=self.portfolio_b,
            is_active=True,
        )
        self.unit_b = Unit.objects.create(
            rentvine_id=4002,
            property=self.prop_b,
            address_line_1="200 Beta Ave",
            is_active=True,
        )

        # Meld on unit_a (open) — Portfolio Alpha
        self.meld_a = Meld.objects.create(
            property_meld_id="7001",
            brief_description="Broken window",
            reference_id="M-7001",
            category="GENERAL",
            status="PENDING_COMPLETION",
            unit_fk=self.unit_a,
            ai_summary="Window cracked, vendor assigned",
            summary_status="auto",
        )

        # Meld on unit_b (closed in range) — Portfolio Beta
        self.meld_b = Meld.objects.create(
            property_meld_id="7002",
            brief_description="Fix HVAC",
            reference_id="M-7002",
            category="HVAC",
            status="COMPLETED",
            unit_fk=self.unit_b,
            completion_date="2026-05-20T12:00:00Z",
            staff_summary="HVAC repaired, $350 total",
            summary_status="edited",
        )

        # Bill + charge on meld_b (replaces old Expenditure fixture)
        bill = Bill.objects.create(
            rentvine_id=9001,
            meld=self.meld_b,
            bill_date=date(2026, 5, 18),
            date_due=date(2026, 5, 25),
        )
        BillCharge.objects.create(
            rentvine_transaction_id=9001,
            bill=bill,
            amount=Decimal("350.00"),
        )

        self.date_start = date(2026, 5, 17)
        self.date_end = date(2026, 5, 24)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_snapshots_created_with_correct_per_meld_portfolio(self):
        """Each snapshot gets the portfolio from its meld's unit→property→portfolio."""
        result = send_maintenance_emails(
            date_start=self.date_start,
            date_end=self.date_end,
            owner_id=self.owner.pk,
        )

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 0)

        # Check MaintenanceEmailSend was created
        email_send = MaintenanceEmailSend.objects.get(owner=self.owner)
        self.assertEqual(email_send.status, "sent")

        # Check snapshots — one per meld
        snapshots = MaintenanceEmailMeld.objects.filter(
            email_send=email_send
        ).order_by("meld_reference_id")

        self.assertEqual(snapshots.count(), 2)

        # Snapshot for meld_a (open, Portfolio Alpha)
        snap_a = snapshots.get(meld=self.meld_a)
        self.assertEqual(snap_a.section, "open")
        self.assertEqual(snap_a.meld_reference_id, "M-7001")
        self.assertEqual(snap_a.summary_text, "Window cracked, vendor assigned")
        self.assertEqual(snap_a.status_at_send, "PENDING_COMPLETION")
        self.assertEqual(snap_a.portfolio, self.portfolio_a)
        self.assertEqual(snap_a.portfolio_name, "Portfolio Alpha")

        # Snapshot for meld_b (closed, Portfolio Beta)
        snap_b = snapshots.get(meld=self.meld_b)
        self.assertEqual(snap_b.section, "closed")
        self.assertEqual(snap_b.meld_reference_id, "M-7002")
        self.assertEqual(snap_b.summary_text, "HVAC repaired, $350 total")
        self.assertEqual(snap_b.cost, Decimal("350.00"))
        self.assertEqual(snap_b.status_at_send, "COMPLETED")
        self.assertEqual(snap_b.portfolio, self.portfolio_b)
        self.assertEqual(snap_b.portfolio_name, "Portfolio Beta")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_snapshot_immutable_after_summary_edit(self):
        """Editing a meld's summary after send must not change the snapshot."""
        send_maintenance_emails(
            date_start=self.date_start,
            date_end=self.date_end,
            owner_id=self.owner.pk,
        )

        # Verify snapshot captured the current summary
        snap_a = MaintenanceEmailMeld.objects.get(meld=self.meld_a)
        self.assertEqual(snap_a.summary_text, "Window cracked, vendor assigned")

        # Now staff edits the meld's summary
        self.meld_a.staff_summary = "UPDATED: vendor rescheduled to next week"
        self.meld_a.summary_status = "edited"
        self.meld_a.save(update_fields=["staff_summary", "summary_status"])

        # Snapshot must be unchanged — it's a frozen copy
        snap_a.refresh_from_db()
        self.assertEqual(snap_a.summary_text, "Window cracked, vendor assigned")

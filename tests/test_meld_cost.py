"""
Unit tests for maintenance.services.meld_cost — bill-charge-based meld cost lookups.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounting.models import Bill, BillCharge
from maintenance.models import Meld
from maintenance.services.meld_cost import get_meld_cost, get_meld_costs


class MeldCostTestCase(TestCase):
    """Verify cost aggregation from BillCharge through Bill -> Meld FK."""

    def setUp(self):
        self.meld = Meld.objects.create(
            property_meld_id="COST-001",
            brief_description="Test meld",
            status="COMPLETED",
        )

    def _make_bill(self, meld, *, is_voided=False, rentvine_id=None):
        """Helper to create a Bill linked to a meld."""
        if rentvine_id is None:
            rentvine_id = Bill.objects.count() + 9000
        return Bill.objects.create(
            rentvine_id=rentvine_id,
            meld=meld,
            bill_date=date(2026, 5, 1),
            date_due=date(2026, 5, 15),
            is_voided=is_voided,
        )

    def _make_charge(self, bill, amount, *, is_voided=False, tx_id=None):
        """Helper to create a BillCharge on a bill."""
        if tx_id is None:
            tx_id = BillCharge.objects.count() + 9000
        return BillCharge.objects.create(
            rentvine_transaction_id=tx_id,
            bill=bill,
            amount=Decimal(str(amount)),
            is_voided=is_voided,
        )

    def test_multiple_charges_sum(self):
        """Multiple charges on a meld sum correctly."""
        bill = self._make_bill(self.meld)
        self._make_charge(bill, "200.00")
        self._make_charge(bill, "575.00")

        result = get_meld_cost(self.meld)
        self.assertEqual(result, Decimal("775.00"))

    def test_voided_bill_excluded(self):
        """Charges on a voided bill are excluded."""
        good_bill = self._make_bill(self.meld, rentvine_id=8001)
        self._make_charge(good_bill, "100.00", tx_id=8001)

        voided_bill = self._make_bill(self.meld, is_voided=True, rentvine_id=8002)
        self._make_charge(voided_bill, "999.00", tx_id=8002)

        result = get_meld_cost(self.meld)
        self.assertEqual(result, Decimal("100.00"))

    def test_voided_charge_excluded(self):
        """A voided charge on a non-voided bill is excluded."""
        bill = self._make_bill(self.meld)
        self._make_charge(bill, "300.00", tx_id=7001)
        self._make_charge(bill, "50.00", is_voided=True, tx_id=7002)

        result = get_meld_cost(self.meld)
        self.assertEqual(result, Decimal("300.00"))

    def test_no_charges_returns_none(self):
        """Meld with no charges returns None."""
        result = get_meld_cost(self.meld)
        self.assertIsNone(result)

    def test_batch_get_meld_costs(self):
        """Batch lookup returns correct dict keyed by meld PK."""
        meld_a = self.meld
        meld_b = Meld.objects.create(
            property_meld_id="COST-002",
            brief_description="Second meld",
            status="COMPLETED",
        )
        meld_no_cost = Meld.objects.create(
            property_meld_id="COST-003",
            brief_description="No cost meld",
            status="COMPLETED",
        )

        bill_a = self._make_bill(meld_a, rentvine_id=6001)
        self._make_charge(bill_a, "100.00", tx_id=6001)
        self._make_charge(bill_a, "50.00", tx_id=6002)

        bill_b = self._make_bill(meld_b, rentvine_id=6003)
        self._make_charge(bill_b, "1795.00", tx_id=6003)

        costs = get_meld_costs([meld_a.pk, meld_b.pk, meld_no_cost.pk])

        self.assertEqual(costs[meld_a.pk], Decimal("150.00"))
        self.assertEqual(costs[meld_b.pk], Decimal("1795.00"))
        self.assertNotIn(meld_no_cost.pk, costs)

    def test_batch_empty_list(self):
        """Batch with empty list returns empty dict."""
        self.assertEqual(get_meld_costs([]), {})

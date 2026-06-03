"""
Tests for meld cost aggregation via BillCharge → Bill → Meld.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Bill, BillCharge
from maintenance.models import Meld
from maintenance.selectors import get_meld_cost, get_meld_costs


@pytest.fixture
def meld():
    return Meld.objects.create(
        property_meld_id="COST-001",
        brief_description="Test meld",
        status="COMPLETED",
    )


def _make_bill(meld, *, is_voided=False, rentvine_id=None):
    if rentvine_id is None:
        rentvine_id = Bill.objects.count() + 9000
    return Bill.objects.create(
        rentvine_id=rentvine_id,
        meld=meld,
        bill_date=date(2026, 5, 1),
        date_due=date(2026, 5, 15),
        is_voided=is_voided,
    )


def _make_charge(bill, amount, *, is_voided=False, tx_id=None):
    if tx_id is None:
        tx_id = BillCharge.objects.count() + 9000
    return BillCharge.objects.create(
        rentvine_transaction_id=tx_id,
        bill=bill,
        amount=Decimal(str(amount)),
        is_voided=is_voided,
    )


@pytest.mark.django_db
class TestMeldCost:
    def test_multiple_charges_sum(self, meld):
        bill = _make_bill(meld)
        _make_charge(bill, "200.00")
        _make_charge(bill, "575.00")

        assert get_meld_cost(meld) == Decimal("775.00")

    def test_voided_bill_excluded(self, meld):
        good_bill = _make_bill(meld, rentvine_id=8001)
        _make_charge(good_bill, "100.00", tx_id=8001)

        voided_bill = _make_bill(meld, is_voided=True, rentvine_id=8002)
        _make_charge(voided_bill, "999.00", tx_id=8002)

        assert get_meld_cost(meld) == Decimal("100.00")

    def test_voided_charge_excluded(self, meld):
        bill = _make_bill(meld)
        _make_charge(bill, "300.00", tx_id=7001)
        _make_charge(bill, "50.00", is_voided=True, tx_id=7002)

        assert get_meld_cost(meld) == Decimal("300.00")

    def test_no_charges_returns_none(self, meld):
        assert get_meld_cost(meld) is None

    def test_batch_get_meld_costs(self, meld):
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

        bill_a = _make_bill(meld, rentvine_id=6001)
        _make_charge(bill_a, "100.00", tx_id=6001)
        _make_charge(bill_a, "50.00", tx_id=6002)

        bill_b = _make_bill(meld_b, rentvine_id=6003)
        _make_charge(bill_b, "1795.00", tx_id=6003)

        costs = get_meld_costs([meld.pk, meld_b.pk, meld_no_cost.pk])

        assert costs[meld.pk] == Decimal("150.00")
        assert costs[meld_b.pk] == Decimal("1795.00")
        assert meld_no_cost.pk not in costs

    def test_batch_empty_list(self):
        assert get_meld_costs([]) == {}

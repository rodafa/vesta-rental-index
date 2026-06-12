"""
Tests for the owner distribution data layer (Step 1).

Covers:
  - Selector grouping by property
  - expected = sum(amount), collected = sum(amountPaid) — separately
  - Partially-paid charge proves the two fields are read independently
  - Voided record exclusion (type-safe: str and int isVoided)
  - Month date-range filtering (excludes forward-posted charges)
  - Portfolio filtering
  - Records with no propertyID logged and skipped
  - Distribution sum and latest-date logic
  - Zero-distribution returns {amount: 0, date: None}
  - Snapshot upsert idempotency
  - Property address resolved from local model
  - Undeposited rent selector sums receipts + prepayment only
  - Upsert stores undeposited fields
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from comms.models import PortfolioDistributionSnapshot
from comms.selectors import get_portfolio_distribution, get_rent_by_property
from comms.services import build_distribution_snapshot, upsert_distribution_snapshot
from core.models import Portfolio, Property

pytestmark = pytest.mark.django_db

MONTH_START = date(2026, 6, 1)
MONTH_END = date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Helpers — build fake RentVine transaction dicts
# ---------------------------------------------------------------------------


def _rent_tx(
    transaction_id,
    portfolio_id,
    property_id,
    amount,
    amount_paid,
    date_posted="2026-06-01",
    charge_account_id=13,
    is_voided=0,
):
    """Build a type-1 rent income transaction record."""
    return {
        "transaction": {
            "transactionID": str(transaction_id),
            "transactionTypeID": "1",
            "portfolioID": str(portfolio_id),
            "propertyID": str(property_id) if property_id is not None else None,
            "unitID": "100",
            "chargeAccountID": str(charge_account_id),
            "amount": str(amount),
            "amountPaid": str(amount_paid),
            "datePosted": date_posted,
            "isVoided": is_voided,
        }
    }


def _dist_tx(
    transaction_id,
    portfolio_id,
    amount,
    date_posted="2026-06-11",
    is_voided=0,
):
    """Build a type-16 owner distribution transaction record."""
    return {
        "transaction": {
            "transactionID": str(transaction_id),
            "transactionTypeID": "16",
            "portfolioID": str(portfolio_id),
            "chargeAccountID": "10",
            "amount": str(amount),
            "datePosted": date_posted,
            "isVoided": is_voided,
            "propertyID": None,
            "unitID": None,
        }
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=82, name="90 Moody Ave LLC")


@pytest.fixture
def portfolio_other():
    return Portfolio.objects.create(rentvine_id=99, name="Other LLC")


@pytest.fixture
def prop_a(portfolio):
    return Property.objects.create(
        rentvine_id=146,
        portfolio=portfolio,
        address_line_1="123 Main St",
        is_active=True,
    )


@pytest.fixture
def prop_b(portfolio):
    return Property.objects.create(
        rentvine_id=12,
        portfolio=portfolio,
        address_line_1="456 Oak Ave",
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Rent selector tests
# ---------------------------------------------------------------------------


class TestGetRentByProperty:
    def test_groups_by_property_sums_correctly(self, portfolio, prop_a, prop_b):
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "2300.00"),
            _rent_tx(2, 82, 146, "100.00", "50.00"),   # second charge on same property
            _rent_tx(3, 82, 12, "1450.00", "1450.00"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)

        assert len(result) == 2
        by_id = {r["property_id"]: r for r in result}

        assert by_id[146]["expected"] == Decimal("2400.00")
        assert by_id[146]["collected"] == Decimal("2350.00")
        assert by_id[12]["expected"] == Decimal("1450.00")
        assert by_id[12]["collected"] == Decimal("1450.00")

    def test_partially_paid_charge_expected_differs_from_collected(
        self, portfolio, prop_a
    ):
        """
        A charge with amountPaid < amount must show different expected
        and collected — proves the two fields are read independently.
        """
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "0.00"),  # charged, not paid
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)

        assert len(result) == 1
        assert result[0]["expected"] == Decimal("2300.00")
        assert result[0]["collected"] == Decimal("0.00")

    def test_excludes_voided_string(self, portfolio, prop_a):
        """isVoided as string "1" — excluded."""
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "2300.00", is_voided="1"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_excludes_voided_int(self, portfolio, prop_a):
        """isVoided as int 1 — excluded (type-safe coercion)."""
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "2300.00", is_voided=1),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_filters_by_month_excludes_forward_posted(self, portfolio, prop_a):
        """A charge datePosted in September should be excluded for June."""
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "0.00", date_posted="2026-09-01"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_filters_by_month_excludes_prior(self, portfolio, prop_a):
        """A charge from May excluded for June."""
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "2300.00", date_posted="2026-05-31"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_filters_by_portfolio(self, portfolio, portfolio_other, prop_a):
        """Records for a different portfolio are ignored."""
        txs = [
            _rent_tx(1, 99, 146, "2300.00", "2300.00"),  # wrong portfolio
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_skips_no_property_id_with_logging(self, portfolio, caplog):
        """Records with no propertyID are skipped and logged."""
        txs = [
            _rent_tx(1, 82, None, "2300.00", "2300.00"),
        ]
        with caplog.at_level("WARNING"):
            result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)

        assert result == []
        assert any(
            "comms_distribution_rent_no_property_id" in r.message
            for r in caplog.records
        )

    def test_resolves_property_address(self, portfolio, prop_a):
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "2300.00"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result[0]["property_address"] == "123 Main St"

    def test_unknown_property_id_gets_fallback_address(self, portfolio):
        """Property not in local DB gets a fallback label."""
        txs = [
            _rent_tx(1, 82, 999, "2300.00", "2300.00"),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result[0]["property_address"] == "Property 999"

    def test_includes_4105_government_assistance(self, portfolio, prop_a):
        """chargeAccountID 14 (#4105 HACA) is included in rent."""
        txs = [
            _rent_tx(1, 82, 146, "1450.00", "1450.00", charge_account_id=14),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert len(result) == 1
        assert result[0]["expected"] == Decimal("1450.00")

    def test_excludes_non_rent_account(self, portfolio, prop_a):
        """chargeAccountID 27 (#4500 Late Fee) is NOT included."""
        txs = [
            _rent_tx(1, 82, 146, "50.00", "50.00", charge_account_id=27),
        ]
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, txs)
        assert result == []

    def test_empty_transactions_returns_empty(self, portfolio):
        result = get_rent_by_property(portfolio, MONTH_START, MONTH_END, [])
        assert result == []


# ---------------------------------------------------------------------------
# Distribution selector tests
# ---------------------------------------------------------------------------


class TestGetPortfolioDistribution:
    def test_sums_and_takes_latest_date(self, portfolio):
        txs = [
            _dist_tx(1, 82, "500.00", date_posted="2026-06-01"),
            _dist_tx(2, 82, "200.00", date_posted="2026-06-11"),
        ]
        result = get_portfolio_distribution(portfolio, MONTH_START, MONTH_END, txs)
        assert result["amount"] == Decimal("700.00")
        assert result["date"] == "2026-06-11"
        assert result["count"] == 2

    def test_returns_zero_when_none(self, portfolio):
        result = get_portfolio_distribution(portfolio, MONTH_START, MONTH_END, [])
        assert result["amount"] == Decimal("0")
        assert result["date"] is None
        assert result["count"] == 0

    def test_excludes_voided_distribution(self, portfolio):
        txs = [
            _dist_tx(1, 82, "500.00", is_voided="1"),
        ]
        result = get_portfolio_distribution(portfolio, MONTH_START, MONTH_END, txs)
        assert result["amount"] == Decimal("0")

    def test_filters_by_portfolio(self, portfolio, portfolio_other):
        txs = [
            _dist_tx(1, 99, "500.00"),  # wrong portfolio
        ]
        result = get_portfolio_distribution(portfolio, MONTH_START, MONTH_END, txs)
        assert result["amount"] == Decimal("0")

    def test_filters_by_month(self, portfolio):
        txs = [
            _dist_tx(1, 82, "500.00", date_posted="2026-05-15"),
        ]
        result = get_portfolio_distribution(portfolio, MONTH_START, MONTH_END, txs)
        assert result["amount"] == Decimal("0")


# ---------------------------------------------------------------------------
# Service / snapshot tests
# ---------------------------------------------------------------------------


class TestBuildDistributionSnapshot:
    def test_assembles_payload_from_selectors(self, portfolio, prop_a, prop_b):
        txs = [
            _rent_tx(1, 82, 146, "2300.00", "1800.00"),
            _rent_tx(2, 82, 12, "1450.00", "1450.00"),
            _dist_tx(10, 82, "3000.00", date_posted="2026-06-11"),
        ]
        payload = build_distribution_snapshot(
            portfolio, MONTH_START, MONTH_END, txs
        )

        assert payload["portfolio"] == portfolio
        assert payload["period_start"] == MONTH_START
        assert payload["period_end"] == MONTH_END
        assert payload["distribution_amount"] == Decimal("3000.00")
        assert payload["distribution_date"] == date(2026, 6, 11)
        assert len(payload["line_items"]) == 2

        # Verify string serialization of decimals
        item = next(i for i in payload["line_items"] if i["property_id"] == 146)
        assert item["expected"] == "2300.00"
        assert item["collected"] == "1800.00"


class TestUpsertDistributionSnapshot:
    def test_creates_snapshot(self, portfolio, prop_a):
        payload = {
            "portfolio": portfolio,
            "period_start": MONTH_START,
            "period_end": MONTH_END,
            "distribution_amount": Decimal("3000.00"),
            "distribution_date": date(2026, 6, 11),
            "line_items": [
                {
                    "property_id": 146,
                    "property_address": "123 Main St",
                    "expected": "2300.00",
                    "collected": "2300.00",
                }
            ],
        }
        snapshot, created = upsert_distribution_snapshot(payload)

        assert created is True
        assert snapshot.distribution_amount == Decimal("3000.00")
        assert snapshot.distribution_date == date(2026, 6, 11)
        assert snapshot.period_type == "monthly"
        assert len(snapshot.line_items) == 1

    def test_upsert_idempotent_overwrites(self, portfolio, prop_a):
        """Calling twice with same portfolio+month overwrites, doesn't duplicate."""
        payload_v1 = {
            "portfolio": portfolio,
            "period_start": MONTH_START,
            "period_end": MONTH_END,
            "distribution_amount": Decimal("3000.00"),
            "distribution_date": date(2026, 6, 11),
            "line_items": [
                {
                    "property_id": 146,
                    "property_address": "123 Main St",
                    "expected": "2300.00",
                    "collected": "2300.00",
                }
            ],
        }
        upsert_distribution_snapshot(payload_v1)

        # Re-run with updated numbers
        payload_v2 = {
            **payload_v1,
            "distribution_amount": Decimal("3500.00"),
            "line_items": [
                {
                    "property_id": 146,
                    "property_address": "123 Main St",
                    "expected": "2300.00",
                    "collected": "1500.00",
                }
            ],
        }
        snapshot, created = upsert_distribution_snapshot(payload_v2)

        assert created is False
        assert snapshot.distribution_amount == Decimal("3500.00")
        assert snapshot.line_items[0]["collected"] == "1500.00"
        assert PortfolioDistributionSnapshot.objects.count() == 1

    def test_zero_distribution_stored(self, portfolio):
        """Portfolio with no distribution gets amount=0, date=None."""
        payload = {
            "portfolio": portfolio,
            "period_start": MONTH_START,
            "period_end": MONTH_END,
            "distribution_amount": Decimal("0"),
            "distribution_date": None,
            "line_items": [],
        }
        snapshot, created = upsert_distribution_snapshot(payload)

        assert created is True
        assert snapshot.distribution_amount == Decimal("0")
        assert snapshot.distribution_date is None

    def test_upsert_stores_undeposited_fields(self, portfolio):
        """Payload with undeposited fields → stored on snapshot."""
        payload = {
            "portfolio": portfolio,
            "period_start": MONTH_START,
            "period_end": MONTH_END,
            "distribution_amount": Decimal("3000.00"),
            "distribution_date": date(2026, 6, 11),
            "line_items": [],
            "undeposited_amount": Decimal("1250.00"),
            "undeposited_as_of": date(2026, 6, 11),
        }
        snapshot, created = upsert_distribution_snapshot(payload)

        assert created is True
        assert snapshot.undeposited_amount == Decimal("1250.00")
        assert snapshot.undeposited_as_of == date(2026, 6, 11)

    def test_upsert_without_undeposited_defaults_zero(self, portfolio):
        """Payload without undeposited fields → model defaults apply."""
        payload = {
            "portfolio": portfolio,
            "period_start": MONTH_START,
            "period_end": MONTH_END,
            "distribution_amount": Decimal("3000.00"),
            "distribution_date": date(2026, 6, 11),
            "line_items": [],
        }
        snapshot, created = upsert_distribution_snapshot(payload)

        assert created is True
        assert snapshot.undeposited_amount == Decimal("0")
        assert snapshot.undeposited_as_of is None


class TestGetUndepositedRent:
    """Tests for the get_undeposited_rent selector."""

    def test_sums_receipts_and_prepayment(self):
        """Sums undepositedReceiptsBalance + undepositedPrepaymentReceiptsBalance."""
        from comms.selectors import get_undeposited_rent

        balances = {
            "undepositedReceiptsBalance": "1250.00",
            "undepositedPrepaymentReceiptsBalance": "5.25",
            "undepositedDepositReceiptsBalance": "500.00",
            "undepositedLiabilityReceiptsBalance": "200.00",
            "undepositedManualReceiptsBalance": "100.00",
            "undepositedPropertyReceiptsBalance": "75.00",
            "undepositedRetainedReceiptsBalance": "50.00",
            "undepositedPortfolioReceiptsBalance": "25.00",
            "undepositedManagementReceiptsBalance": "10.00",
        }
        result = get_undeposited_rent(balances)
        assert result == Decimal("1255.25")

    def test_ignores_non_rent_fields(self):
        """Only receipts + prepayment included; other 7 sub-fields excluded."""
        from comms.selectors import get_undeposited_rent

        balances = {
            "undepositedReceiptsBalance": "0",
            "undepositedPrepaymentReceiptsBalance": "0",
            "undepositedDepositReceiptsBalance": "999.99",
            "undepositedLiabilityReceiptsBalance": "888.88",
        }
        result = get_undeposited_rent(balances)
        assert result == Decimal("0")

    def test_handles_missing_fields(self):
        """Missing keys → treated as 0."""
        from comms.selectors import get_undeposited_rent

        result = get_undeposited_rent({})
        assert result == Decimal("0")

    def test_handles_none_values(self):
        """None values → treated as 0."""
        from comms.selectors import get_undeposited_rent

        balances = {
            "undepositedReceiptsBalance": None,
            "undepositedPrepaymentReceiptsBalance": None,
        }
        result = get_undeposited_rent(balances)
        assert result == Decimal("0")

"""
Tests for owner distribution email assembly.

Covers:
  - Single-LLC renders fragment without grand total
  - Multi-LLC renders grand total section
  - No distribution (amount=0 or date=None) returns None
  - Rent but no distribution excluded
  - Mixed portfolios — only portfolios with distribution included
  - No snapshot returns None
  - Portal URL renders CTA when set, hidden when blank
  - Owner dedup by email
  - Statement reminder date computation
  - December → January wrap
  - Undeposited clearing line renders when positive, hidden when zero
  - Undeposited never affects grand totals
"""

from datetime import date
from decimal import Decimal

import pytest
from django.test import override_settings

from comms.models import PortfolioDistributionSnapshot
from comms.services import assemble_owner_distribution_email
from core.models import Owner, Portfolio

pytestmark = pytest.mark.django_db

PERIOD_START = date(2026, 6, 1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio_a():
    return Portfolio.objects.create(rentvine_id=10, name="Alpha LLC")


@pytest.fixture
def portfolio_b():
    return Portfolio.objects.create(rentvine_id=11, name="Beta LLC")


@pytest.fixture
def owner_single(portfolio_a):
    owner = Owner.objects.create(
        rentvine_contact_id=100,
        name="Alice Smith",
        first_name="Alice",
        email="alice@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_a)
    return owner


@pytest.fixture
def owner_multi(portfolio_a, portfolio_b):
    owner = Owner.objects.create(
        rentvine_contact_id=101,
        name="Bob Jones",
        first_name="Bob",
        email="bob@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_a, portfolio_b)
    return owner


@pytest.fixture
def owner_shared_1(portfolio_a):
    owner = Owner.objects.create(
        rentvine_contact_id=200,
        name="Carol Davis",
        first_name="Carol",
        email="shared@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_a)
    return owner


@pytest.fixture
def owner_shared_2(portfolio_b):
    owner = Owner.objects.create(
        rentvine_contact_id=201,
        name="Dan Davis",
        first_name="Dan",
        email="shared@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_b)
    return owner


@pytest.fixture
def snapshot_a(portfolio_a):
    return PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("2500.00"),
        distribution_date=date(2026, 6, 11),
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
            {"property_id": 2, "property_address": "456 Oak Ave", "expected": "1200.00", "collected": "1000.00"},
        ],
    )


@pytest.fixture
def snapshot_b(portfolio_b):
    return PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_b,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("1800.00"),
        distribution_date=date(2026, 6, 12),
        line_items=[
            {"property_id": 3, "property_address": "789 Elm Dr", "expected": "2000.00", "collected": "2000.00"},
        ],
    )


@pytest.fixture
def snapshot_no_dist(portfolio_a):
    """Snapshot with distribution_amount=0 and no date."""
    return PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("0"),
        distribution_date=None,
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_llc_renders_fragment_no_grand_total(owner_single, snapshot_a):
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    assert result["owner_name"] == "Alice"
    assert len(result["portfolios"]) == 1
    assert result["portfolios"][0]["name"] == "Alpha LLC"

    html = result["body_html"]
    # Fragment content
    assert "Alpha LLC" in html
    assert "123 Main St" in html
    assert "456 Oak Ave" in html
    assert "$2,500.00" in html  # distribution amount
    # No grand total for single LLC
    assert "Grand Total" not in html


def test_multi_llc_renders_grand_total(owner_multi, snapshot_a, snapshot_b):
    result = assemble_owner_distribution_email("bob@example.com", PERIOD_START)

    assert result is not None
    assert len(result["portfolios"]) == 2

    html = result["body_html"]
    assert "Alpha LLC" in html
    assert "Beta LLC" in html
    # Grand total section
    assert "Grand Total" in html
    # Grand total distribution = 2500 + 1800 = 4300
    assert "$4,300.00" in html


def test_no_distribution_returns_none(owner_single, snapshot_no_dist):
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)
    assert result is None


def test_rent_but_no_distribution_excluded(owner_single, portfolio_a):
    """Portfolio has line_items but distribution_amount=0 → no email."""
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("0"),
        distribution_date=None,
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
    )
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)
    assert result is None


def test_mixed_portfolios_only_includes_with_distribution(
    owner_multi, snapshot_b, portfolio_a
):
    """Owner with 2 portfolios, one has distribution, one doesn't → one fragment."""
    # portfolio_a has no snapshot with distribution
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("0"),
        distribution_date=None,
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
    )

    result = assemble_owner_distribution_email("bob@example.com", PERIOD_START)

    assert result is not None
    assert len(result["portfolios"]) == 1
    assert result["portfolios"][0]["name"] == "Beta LLC"
    assert "Grand Total" not in result["body_html"]  # single qualifying LLC


def test_no_snapshot_returns_none(owner_single):
    """No snapshot for period → None."""
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)
    assert result is None


@override_settings(OWNER_PORTAL_URL="https://vestapm.rentvine.com/portals/owner/")
def test_portal_url_renders_when_set(owner_single, snapshot_a):
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    assert "View Your Ledger" in result["body_html"]
    assert "https://vestapm.rentvine.com/portals/owner/" in result["body_html"]


@override_settings(OWNER_PORTAL_URL="")
def test_portal_url_hidden_when_blank(owner_single, snapshot_a):
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    assert "View Your Ledger" not in result["body_html"]


def test_owner_dedup_by_email(owner_shared_1, owner_shared_2, snapshot_a, snapshot_b):
    """Two Owner records with same email → one assembled email."""
    result = assemble_owner_distribution_email("shared@example.com", PERIOD_START)

    assert result is not None
    # Both portfolios included (union)
    assert len(result["portfolios"]) == 2


def test_statement_reminder_date(owner_single, snapshot_a):
    """period_start=June 1 → reminder says 'July 5'."""
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    assert "July 5" in result["body_html"]


def test_statement_reminder_december_wraps():
    """period_start=December 1 → 'January 5' (year wraps)."""
    portfolio = Portfolio.objects.create(rentvine_id=50, name="Dec LLC")
    owner = Owner.objects.create(
        rentvine_contact_id=500,
        name="Eve December",
        first_name="Eve",
        email="eve@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio)

    dec_start = date(2026, 12, 1)
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio,
        period_type="monthly",
        period_start=dec_start,
        period_end=date(2026, 12, 31),
        distribution_amount=Decimal("1000.00"),
        distribution_date=date(2026, 12, 15),
        line_items=[
            {"property_id": 10, "property_address": "100 Winter Ln", "expected": "1200.00", "collected": "1200.00"},
        ],
    )

    result = assemble_owner_distribution_email("eve@example.com", dec_start)
    assert result is not None
    assert "January 5" in result["body_html"]


# ---------------------------------------------------------------------------
# Undeposited clearing line
# ---------------------------------------------------------------------------


def test_undeposited_renders_when_positive(owner_single, portfolio_a):
    """Snapshot with undeposited_amount > 0 → clearing line in HTML."""
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("2500.00"),
        distribution_date=date(2026, 6, 11),
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
        undeposited_amount=Decimal("1250.00"),
        undeposited_as_of=date(2026, 6, 11),
    )
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    html = result["body_html"]
    assert "still clearing" in html
    assert "$1,250.00" in html
    assert "Jun 11, 2026" in html


def test_undeposited_hidden_when_zero(owner_single, portfolio_a):
    """Snapshot with undeposited_amount=0 → no clearing line."""
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("2500.00"),
        distribution_date=date(2026, 6, 11),
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
        undeposited_amount=Decimal("0"),
        undeposited_as_of=None,
    )
    result = assemble_owner_distribution_email("alice@example.com", PERIOD_START)

    assert result is not None
    assert "still clearing" not in result["body_html"]


def test_undeposited_never_affects_grand_total(owner_multi, portfolio_a, portfolio_b):
    """Grand total is the same regardless of undeposited values."""
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_a,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("2500.00"),
        distribution_date=date(2026, 6, 11),
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
        undeposited_amount=Decimal("1250.00"),
        undeposited_as_of=date(2026, 6, 11),
    )
    PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio_b,
        period_type="monthly",
        period_start=PERIOD_START,
        period_end=date(2026, 6, 30),
        distribution_amount=Decimal("1800.00"),
        distribution_date=date(2026, 6, 12),
        line_items=[
            {"property_id": 3, "property_address": "789 Elm Dr", "expected": "2000.00", "collected": "2000.00"},
        ],
        undeposited_amount=Decimal("0"),
        undeposited_as_of=None,
    )

    result = assemble_owner_distribution_email("bob@example.com", PERIOD_START)

    assert result is not None
    html = result["body_html"]
    # Grand total = 2500 + 1800 = 4300 — undeposited $1,250 never appears in any total
    assert "Grand Total" in html
    assert "$4,300.00" in html
    # The clearing line appears for Alpha LLC only
    assert "still clearing" in html
    assert "$1,250.00" in html

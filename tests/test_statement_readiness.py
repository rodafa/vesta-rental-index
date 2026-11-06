"""
Tests for accounting.selectors.statement_readiness.
"""

from datetime import date

import pytest

from accounting.models import PortfolioStatement
from accounting.selectors import statement_readiness
from core.models import Portfolio


pytestmark = pytest.mark.django_db

SEP_START = date(2026, 9, 1)
SEP_END = date(2026, 9, 30)
OCT_START = date(2026, 10, 1)
OCT_END = date(2026, 10, 31)


@pytest.fixture
def two_portfolios():
    p1 = Portfolio.objects.create(rentvine_id=1, name="Alpha Portfolio", is_active=True)
    p2 = Portfolio.objects.create(rentvine_id=2, name="Beta Portfolio", is_active=True)
    return p1, p2


@pytest.fixture
def sep_and_oct_statements(two_portfolios):
    """Both portfolios have Sep statements; only Alpha has Oct."""
    p1, p2 = two_portfolios
    PortfolioStatement.objects.create(
        portfolio=p1, rentvine_statement_id=100,
        period_start=SEP_START, period_end=SEP_END, status="2",
    )
    PortfolioStatement.objects.create(
        portfolio=p2, rentvine_statement_id=101,
        period_start=SEP_START, period_end=SEP_END, status="2",
    )
    PortfolioStatement.objects.create(
        portfolio=p1, rentvine_statement_id=102,
        period_start=OCT_START, period_end=OCT_END, status="2",
    )


class TestStatementReadiness:
    def test_october_readiness(self, two_portfolios, sep_and_oct_statements):
        p1, p2 = two_portfolios
        result = statement_readiness(list(Portfolio.objects.filter(
            is_active=True,
        ).order_by("name")), OCT_START, OCT_END)

        assert result["total_portfolios"] == 2
        assert result["with_statement"] == 1
        assert result["missing"] == ["Beta Portfolio"]
        assert result["duplicates"] == []
        assert result["last_synced_at"] is not None

    def test_sep_all_present(self, two_portfolios, sep_and_oct_statements):
        result = statement_readiness(list(Portfolio.objects.filter(
            is_active=True,
        ).order_by("name")), SEP_START, SEP_END)

        assert result["total_portfolios"] == 2
        assert result["with_statement"] == 2
        assert result["missing"] == []

    def test_duplicate_detected(self, two_portfolios):
        p1, _ = two_portfolios
        PortfolioStatement.objects.create(
            portfolio=p1, rentvine_statement_id=200,
            period_start=OCT_START, period_end=OCT_END, status="2",
        )
        PortfolioStatement.objects.create(
            portfolio=p1, rentvine_statement_id=201,
            period_start=OCT_START, period_end=OCT_END, status="2",
        )

        result = statement_readiness([p1], OCT_START, OCT_END)

        assert result["duplicates"] == ["Alpha Portfolio"]
        assert result["with_statement"] == 0

    def test_empty_portfolio_list(self):
        result = statement_readiness([], OCT_START, OCT_END)

        assert result["total_portfolios"] == 0
        assert result["with_statement"] == 0
        assert result["missing"] == []
        assert result["duplicates"] == []

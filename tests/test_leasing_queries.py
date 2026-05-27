"""
Tests for leasing query layer.

Covers: get_unit_metrics window sums, inclusive boundaries,
state field selection, portfolio filter, and get_portfolio_kpis
prior-period computation.
"""

from datetime import date

import pytest

from leasing.models import DailyLeasingMetric
from leasing.services.leasing_deltas import recompute_deltas_for_unit
from leasing.services.leasing_queries import get_portfolio_kpis, get_unit_metrics
from properties.models import Portfolio, Property, Unit


@pytest.fixture
def portfolio(db):
    return Portfolio.objects.create(rentvine_id=1, name="Test Portfolio")


@pytest.fixture
def prop(portfolio):
    return Property.objects.create(
        rentvine_id=1,
        name="123 Test St",
        address_line_1="123 Test St",
        portfolio=portfolio,
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=100,
        rentengine_id=5001,
        name="",
        address_line_1="123 Test St",
    )


@pytest.fixture
def unit_b(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=101,
        rentengine_id=5002,
        name="Unit B",
        address_line_1="123 Test St",
    )


def _metric(unit, day, **kwargs):
    defaults = {
        "new_prospects": 0,
        "showings_completed": 0,
        "applications_submitted": 0,
        "showings_missed_or_failed": 0,
        "source": "rentengine_api",
    }
    defaults.update(kwargs)
    m = DailyLeasingMetric.objects.create(unit=unit, date=day, **defaults)
    return m


def _seed_and_compute(unit, rows):
    """Create metrics and compute deltas."""
    for day, kwargs in rows:
        _metric(unit, day, **kwargs)
    recompute_deltas_for_unit(unit)


@pytest.mark.django_db
class TestGetUnitMetrics:
    def test_window_sum_correctness(self, unit):
        """SUMming raw per-day counts over a window gives correct totals."""
        _seed_and_compute(unit, [
            (date(2026, 5, 1), {"new_prospects": 2, "showings_completed": 1}),
            (date(2026, 5, 2), {"new_prospects": 3, "showings_completed": 2}),
            (date(2026, 5, 3), {"new_prospects": 0, "showings_completed": 3}),
        ])

        results = get_unit_metrics(date(2026, 5, 1), date(2026, 5, 3))
        assert len(results) == 1
        row = results[0]
        # Total leads = 2 + 3 + 0 = 5
        assert row["leads"] == 5
        # Total showings = 1 + 2 + 3 = 6
        assert row["showings"] == 6

    def test_inclusive_boundaries(self, unit):
        """Rows on date_from and date_to are both included."""
        _seed_and_compute(unit, [
            (date(2026, 5, 1), {"new_prospects": 3}),
            (date(2026, 5, 2), {"new_prospects": 2}),
            (date(2026, 5, 3), {"new_prospects": 3}),
        ])

        # Query just 5/1 to 5/2
        results = get_unit_metrics(date(2026, 5, 1), date(2026, 5, 2))
        row = results[0]
        assert row["leads"] == 5  # 3 + 2

    def test_state_field_reads_nearest_to_date_to(self, unit):
        """State fields come from the row closest to date_to."""
        _seed_and_compute(unit, [
            (date(2026, 5, 1), {
                "new_prospects": 1,
                "days_on_market": 5,
                "property_health": "Healthy",
            }),
            (date(2026, 5, 3), {
                "new_prospects": 3,
                "days_on_market": 10,
                "property_health": "At-risk",
            }),
        ])

        # Query 5/1-5/3: state should come from 5/3
        results = get_unit_metrics(date(2026, 5, 1), date(2026, 5, 3))
        assert results[0]["dom"] == 10
        assert results[0]["health"] == "At-risk"

        # Query 5/1-5/1: state should come from 5/1
        results = get_unit_metrics(date(2026, 5, 1), date(2026, 5, 1))
        assert results[0]["dom"] == 5
        assert results[0]["health"] == "Healthy"

    def test_portfolio_filter(self, unit, unit_b, portfolio, db):
        """Portfolio filter restricts results to units in that portfolio."""
        other_portfolio = Portfolio.objects.create(
            rentvine_id=2, name="Other Portfolio"
        )
        other_prop = Property.objects.create(
            rentvine_id=2,
            name="999 Other St",
            address_line_1="999 Other St",
            portfolio=other_portfolio,
        )
        other_unit = Unit.objects.create(
            property=other_prop,
            rentvine_id=200,
            rentengine_id=6001,
            name="",
            address_line_1="999 Other St",
        )

        _seed_and_compute(unit, [
            (date(2026, 5, 1), {"new_prospects": 5}),
        ])
        _seed_and_compute(other_unit, [
            (date(2026, 5, 1), {"new_prospects": 10}),
        ])

        # Filter by first portfolio — should only see unit
        results = get_unit_metrics(
            date(2026, 5, 1), date(2026, 5, 1), portfolio=portfolio
        )
        assert len(results) == 1
        assert results[0]["unit_id"] == unit.pk

        # No filter — should see both
        results = get_unit_metrics(date(2026, 5, 1), date(2026, 5, 1))
        assert len(results) == 2


@pytest.mark.django_db
class TestGetPortfolioKpis:
    def test_prior_window_computation(self, unit):
        """Prior window is the same length, immediately before current."""
        # Prior window: 5/1-5/3 (3 days)
        _seed_and_compute(unit, [
            (date(2026, 5, 1), {"new_prospects": 2}),
            (date(2026, 5, 2), {"new_prospects": 2}),
            (date(2026, 5, 3), {"new_prospects": 2}),
            # Current window: 5/4-5/6 (3 days)
            (date(2026, 5, 4), {"new_prospects": 3}),
            (date(2026, 5, 5), {"new_prospects": 2}),
            (date(2026, 5, 6), {"new_prospects": 3}),
        ])

        kpis = get_portfolio_kpis(date(2026, 5, 4), date(2026, 5, 6))

        # Current: 3 + 2 + 3 = 8
        assert kpis["current"]["total_leads"] == 8
        # Prior: 2 + 2 + 2 = 6
        assert kpis["prior"]["total_leads"] == 6
        # Delta: 8 - 6 = 2
        assert kpis["deltas"]["total_leads"] == 2

    def test_single_day_window(self, unit):
        """A single-day window should have a single-day prior window."""
        _seed_and_compute(unit, [
            (date(2026, 5, 4), {"new_prospects": 3}),
            (date(2026, 5, 5), {"new_prospects": 4}),
        ])

        kpis = get_portfolio_kpis(date(2026, 5, 5), date(2026, 5, 5))

        # Current: raw count for 5/5 = 4
        assert kpis["current"]["total_leads"] == 4
        # Prior window is 5/4-5/4: raw count = 3
        assert kpis["prior"]["total_leads"] == 3

"""
Tests for leasing delta computation.

Covers: normal consecutive days, first row, resets, date gaps,
idempotency, and None carry-forward.
"""

from datetime import date

import pytest

from leasing.models import DailyLeasingMetric
from leasing.services.leasing_deltas import recompute_deltas_for_unit
from properties.models import Property, Unit


@pytest.fixture
def prop(db):
    return Property.objects.create(
        rentvine_id=1,
        name="123 Test St",
        address_line_1="123 Test St",
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=100,
        rentengine_id=5001,
        name="Unit A",
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
    return DailyLeasingMetric.objects.create(unit=unit, date=day, **defaults)


@pytest.mark.django_db
class TestNormalConsecutiveDays:
    def test_delta_equals_diff(self, unit):
        """Delta = current cumulative - prior cumulative."""
        _metric(unit, date(2026, 5, 1), new_prospects=3, showings_completed=1)
        _metric(unit, date(2026, 5, 2), new_prospects=5, showings_completed=2)
        _metric(unit, date(2026, 5, 3), new_prospects=5, showings_completed=4)

        recompute_deltas_for_unit(unit)

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )
        assert rows[0].new_prospects_delta == 3  # first row
        assert rows[1].new_prospects_delta == 2  # 5 - 3
        assert rows[2].new_prospects_delta == 0  # 5 - 5
        assert rows[1].showings_completed_delta == 1  # 2 - 1
        assert rows[2].showings_completed_delta == 2  # 4 - 2


@pytest.mark.django_db
class TestFirstRow:
    def test_delta_equals_cumulative(self, unit):
        """First row ever: delta = cumulative value itself."""
        _metric(unit, date(2026, 5, 1), new_prospects=7)

        recompute_deltas_for_unit(unit)

        row = DailyLeasingMetric.objects.get(unit=unit)
        assert row.new_prospects_delta == 7


@pytest.mark.django_db
class TestReset:
    def test_reset_clamps_delta(self, unit):
        """When current < prior (reset), delta = current."""
        _metric(unit, date(2026, 5, 1), new_prospects=10)
        _metric(unit, date(2026, 5, 2), new_prospects=3)  # reset

        recompute_deltas_for_unit(unit)

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )
        assert rows[0].new_prospects_delta == 10
        assert rows[1].new_prospects_delta == 3  # clamped, not -7


@pytest.mark.django_db
class TestDateGap:
    def test_gap_accumulates_to_first_row_after(self, unit):
        """Activity during a gap is attributed to the next row."""
        _metric(unit, date(2026, 5, 1), new_prospects=2)
        # gap on 5/2 and 5/3
        _metric(unit, date(2026, 5, 4), new_prospects=8)

        recompute_deltas_for_unit(unit)

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )
        assert rows[0].new_prospects_delta == 2
        assert rows[1].new_prospects_delta == 6  # 8 - 2


@pytest.mark.django_db
class TestIdempotency:
    def test_running_twice_same_result(self, unit):
        """Running recompute twice produces identical deltas."""
        _metric(unit, date(2026, 5, 1), new_prospects=3)
        _metric(unit, date(2026, 5, 2), new_prospects=7)

        result1 = recompute_deltas_for_unit(unit)
        result2 = recompute_deltas_for_unit(unit)

        # Second run should update 0 rows
        assert result2["rows_updated"] == 0

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )
        assert rows[0].new_prospects_delta == 3
        assert rows[1].new_prospects_delta == 4


@pytest.mark.django_db
class TestNoneCarryForward:
    def test_none_interspersed(self, unit):
        """None values between real values don't cause phantom resets."""
        _metric(
            unit, date(2026, 5, 1),
            new_prospects=2,
            applications_requested=5,
            outbound_texts=10,
        )
        _metric(
            unit, date(2026, 5, 2),
            new_prospects=4,
            applications_requested=None,  # None day
            outbound_texts=None,
        )
        _metric(
            unit, date(2026, 5, 3),
            new_prospects=6,
            applications_requested=8,
            outbound_texts=15,
        )

        recompute_deltas_for_unit(unit)

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )

        # applications_requested: 5, (carry 5), 8
        assert rows[0].applications_requested_delta == 5
        assert rows[1].applications_requested_delta == 0  # carried 5 - 5
        assert rows[2].applications_requested_delta == 3  # 8 - 5

        # outbound_texts: 10, (carry 10), 15
        assert rows[0].outbound_texts_delta == 10
        assert rows[1].outbound_texts_delta == 0  # carried 10 - 10
        assert rows[2].outbound_texts_delta == 5  # 15 - 10

        # Window SUM should equal the final cumulative minus the first
        total_apps = sum(r.applications_requested_delta for r in rows)
        assert total_apps == 8  # correct: 5 + 0 + 3

    def test_all_none_series(self, unit):
        """A field that is always None produces all-zero deltas."""
        _metric(
            unit, date(2026, 5, 1),
            new_prospects=1,
            total_calls=None,
        )
        _metric(
            unit, date(2026, 5, 2),
            new_prospects=2,
            total_calls=None,
        )

        recompute_deltas_for_unit(unit)

        rows = list(
            DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
        )
        assert rows[0].total_calls_delta == 0
        assert rows[1].total_calls_delta == 0


@pytest.mark.django_db
class TestFromDate:
    def test_from_date_partial_recompute(self, unit):
        """from_date limits recomputation to rows >= from_date."""
        _metric(unit, date(2026, 5, 1), new_prospects=3)
        _metric(unit, date(2026, 5, 2), new_prospects=5)
        _metric(unit, date(2026, 5, 3), new_prospects=9)

        # Full compute first
        recompute_deltas_for_unit(unit)

        # Now change day 3 and recompute from day 3 only
        m = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 3))
        m.new_prospects = 12
        m.save()

        result = recompute_deltas_for_unit(unit, from_date=date(2026, 5, 3))
        assert result["rows_examined"] == 1

        m.refresh_from_db()
        assert m.new_prospects_delta == 7  # 12 - 5

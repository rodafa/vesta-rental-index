"""
Tests for the Weekly Report Detail API (get_property_detail).

Covers: Total Apps reads from DailyLeasingMetric, not DailyLeasingSummary.
"""

from datetime import date

import pytest

from leasing.models import DailyLeasingMetric
from market.models import DailyLeasingSummary
from properties.models import Property, Unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prop(db):
    return Property.objects.create(
        rentvine_id=1,
        name="240 Ridgeview Lane",
        address_line_1="240 Ridgeview Lane",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=100,
        rentengine_id=5001,
        name="",
        address_line_1="240 Ridgeview Lane",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTotalAppsSource:
    """Total Apps should come from DailyLeasingMetric.applications_submitted,
    not DailyLeasingSummary.applications_count."""

    def test_total_apps_reads_from_daily_leasing_metric(self, unit):
        # DailyLeasingSummary has 0 apps (BoomPay unit resolution is broken)
        DailyLeasingSummary.objects.create(
            summary_date=date(2026, 5, 20),
            unit=unit,
            leads_count=31,
            showings_completed_count=11,
            applications_count=0,
        )

        # DailyLeasingMetric has the correct value from RentEngine
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            new_prospects=31,
            showings_completed=11,
            applications_submitted=1,
            source="rentengine_api",
        )

        from weekly_reports.api import get_property_detail

        result = get_property_detail(None, unit.id)

        # Total Apps should be 1 (from DailyLeasingMetric), not 0 (DailyLeasingSummary)
        assert result["alltime"]["apps"] == 1
        # Leads and showings still come from DailyLeasingSummary
        assert result["alltime"]["leads"] == 31
        assert result["alltime"]["showings"] == 11

    def test_total_apps_sums_across_all_dates(self, unit):
        """Total Apps is an all-time sum, not just the latest row."""
        DailyLeasingSummary.objects.create(
            summary_date=date(2026, 5, 22),
            unit=unit,
        )

        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            applications_submitted=2,
            source="rentengine_api",
        )
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 21),
            applications_submitted=3,
            source="rentengine_api",
        )

        from weekly_reports.api import get_property_detail

        result = get_property_detail(None, unit.id)

        assert result["alltime"]["apps"] == 5

    def test_total_apps_zero_when_no_metrics(self, unit):
        """No DailyLeasingMetric rows → Total Apps is 0, not an error."""
        DailyLeasingSummary.objects.create(
            summary_date=date(2026, 5, 20),
            unit=unit,
            applications_count=99,  # should be ignored
        )

        from weekly_reports.api import get_property_detail

        result = get_property_detail(None, unit.id)

        assert result["alltime"]["apps"] == 0

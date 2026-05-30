"""
Tests for DLMApplicationCountService — BoomPay as single source of truth
for DailyLeasingMetric.applications_submitted.

Covers: Eastern timezone boundaries (EDT/EST), null unit/submitted_at
handling, idempotency, overwrite, zero-fill, and csv_backfill protection.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from integrations.boompay.services import DLMApplicationCountService
from leasing.models import DailyLeasingMetric
from properties.models import Property, Unit
from screening.models import ScreeningApplication

EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prop(db):
    return Property.objects.create(
        rentvine_id=1,
        name="10 Test Lane",
        address_line_1="10 Test Lane",
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
        name="Unit A",
        address_line_1="10 Test Lane",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit_b(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=101,
        rentengine_id=5002,
        name="Unit B",
        address_line_1="10 Test Lane",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def service():
    return DLMApplicationCountService()


# ---------------------------------------------------------------------------
# EDT late-night boundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEDTLateNightBoundary:
    """App at 11:30 PM ET (03:30 UTC next day) counts on the ET date."""

    def test_late_night_edt_app_counts_on_eastern_date(self, unit, service):
        # 2026-05-22 23:30 ET = 2026-05-23 03:30 UTC (EDT = UTC-4)
        ScreeningApplication.objects.create(
            boompay_id="app-edt-late",
            unit=unit,
            submitted_at=datetime(2026, 5, 23, 3, 30, tzinfo=UTC),
            applicant_name="Late Night EDT",
        )

        result = service.sync(
            start_date=date(2026, 5, 22),
            end_date=date(2026, 5, 22),
        )

        assert result["created"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 22))
        assert metric.applications_submitted == 1

        # Should NOT appear on May 23
        assert not DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 5, 23)
        ).exists()


# ---------------------------------------------------------------------------
# EST boundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestESTBoundary:
    """Apps at 4:59 UTC vs 5:00 UTC land on Jan 14 vs Jan 15 (EST = UTC-5)."""

    def test_est_boundary_459_utc_is_jan14(self, unit, service):
        # 4:59 UTC on Jan 15 = 11:59 PM ET on Jan 14
        ScreeningApplication.objects.create(
            boompay_id="app-est-before",
            unit=unit,
            submitted_at=datetime(2026, 1, 15, 4, 59, tzinfo=UTC),
            applicant_name="Before Midnight EST",
        )

        result = service.sync(
            start_date=date(2026, 1, 14),
            end_date=date(2026, 1, 14),
        )

        assert result["created"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 1, 14))
        assert metric.applications_submitted == 1

    def test_est_boundary_500_utc_is_jan15(self, unit, service):
        # 5:00 UTC on Jan 15 = 12:00 AM ET on Jan 15
        ScreeningApplication.objects.create(
            boompay_id="app-est-after",
            unit=unit,
            submitted_at=datetime(2026, 1, 15, 5, 0, tzinfo=UTC),
            applicant_name="After Midnight EST",
        )

        # Should NOT appear on Jan 14
        service.sync(start_date=date(2026, 1, 14), end_date=date(2026, 1, 14))
        assert not DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 1, 14)
        ).exists()

        # Should appear on Jan 15
        result = service.sync(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
        )
        assert result["created"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 1, 15))
        assert metric.applications_submitted == 1


# ---------------------------------------------------------------------------
# Null unit skipped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNullUnitSkipped:
    """ScreeningApplication with unit=None excluded; warning logged."""

    def test_null_unit_excluded(self, unit, service, caplog):
        # App with a unit — should count
        ScreeningApplication.objects.create(
            boompay_id="app-with-unit",
            unit=unit,
            submitted_at=datetime(2026, 5, 20, 15, 0, tzinfo=UTC),
            applicant_name="Has Unit",
        )
        # App without a unit — should be skipped
        ScreeningApplication.objects.create(
            boompay_id="app-no-unit",
            unit=None,
            submitted_at=datetime(2026, 5, 20, 15, 0, tzinfo=UTC),
            applicant_name="No Unit",
        )

        with caplog.at_level("WARNING"):
            result = service.sync(
                start_date=date(2026, 5, 20),
                end_date=date(2026, 5, 20),
            )

        assert result["skipped_null_unit"] == 1
        assert result["created"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 1
        assert "null unit" in caplog.text


# ---------------------------------------------------------------------------
# Null submitted_at skipped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNullSubmittedAtSkipped:
    """ScreeningApplication with submitted_at=None excluded; warning logged."""

    def test_null_submitted_at_excluded(self, unit, service, caplog):
        ScreeningApplication.objects.create(
            boompay_id="app-no-date",
            unit=unit,
            submitted_at=None,
            applicant_name="No Date",
        )

        with caplog.at_level("WARNING"):
            result = service.sync(
                start_date=date(2026, 5, 20),
                end_date=date(2026, 5, 20),
            )

        assert result["skipped_null_submitted_at"] == 1
        assert result["created"] == 0
        assert "null submitted_at" in caplog.text


# ---------------------------------------------------------------------------
# Idempotent (run twice)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIdempotent:
    """Second run produces identical results, no duplicates."""

    def test_rerun_is_idempotent(self, unit, service):
        ScreeningApplication.objects.create(
            boompay_id="app-idem",
            unit=unit,
            submitted_at=datetime(2026, 5, 20, 14, 0, tzinfo=UTC),
            applicant_name="Idempotent Test",
        )

        result1 = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )
        result2 = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )

        assert result1["created"] == 1
        assert result2["created"] == 0
        assert result2["updated"] == 0
        assert DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 5, 20)
        ).count() == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 1


# ---------------------------------------------------------------------------
# Overwrites stale value
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOverwritesStaleValue:
    """Existing DLM row with applications_submitted=99 gets corrected."""

    def test_overwrites_incorrect_value(self, unit, service):
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            applications_submitted=99,
            source="rentengine_api",
        )

        ScreeningApplication.objects.create(
            boompay_id="app-overwrite",
            unit=unit,
            submitted_at=datetime(2026, 5, 20, 14, 0, tzinfo=UTC),
            applicant_name="Overwrite Test",
        )

        result = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )

        assert result["updated"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 1


# ---------------------------------------------------------------------------
# Zero-fill
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZeroFill:
    """Day with no apps zeros out previously-nonzero DLM row."""

    def test_zeros_out_stale_count(self, unit, service):
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            applications_submitted=3,
            source="rentengine_api",
        )

        # No ScreeningApplications for this day
        result = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )

        assert result["zero_filled"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 0


# ---------------------------------------------------------------------------
# Zero-fill skips csv_backfill
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZeroFillSkipsCsvBackfill:
    """DLM row with source='csv_backfill' is NOT zeroed when unit has no BoomPay apps."""

    def test_csv_backfill_preserved(self, unit, service):
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            applications_submitted=5,
            source="csv_backfill",
        )

        # No ScreeningApplications for this day
        result = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )

        assert result["zero_filled"] == 0
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 5

    def test_csv_backfill_overwritten_when_boompay_apps_exist(self, unit, service):
        """When BoomPay has apps for a date, it correctly wins over csv_backfill."""
        DailyLeasingMetric.objects.create(
            unit=unit,
            date=date(2026, 5, 20),
            applications_submitted=5,
            source="csv_backfill",
        )

        ScreeningApplication.objects.create(
            boompay_id="app-overlap",
            unit=unit,
            submitted_at=datetime(2026, 5, 20, 14, 0, tzinfo=UTC),
            applicant_name="BoomPay Wins",
        )

        result = service.sync(
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
        )

        assert result["updated"] == 1
        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.applications_submitted == 1

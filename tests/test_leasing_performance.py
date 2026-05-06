"""
Tests for the Leasing Directory service layer.

Covers: upsert_daily_metric, upsert_showing_feedback, import_historical_csv,
ingest_day (including 429 handling and atomic rollback).
"""

import os
import tempfile
from datetime import date, datetime, timezone as dt_tz
from unittest.mock import patch

import pytest
import responses

from django.conf import settings

from leasing.models import DailyLeasingMetric, ShowingFeedback
from leasing.services.leasing_performance import (
    import_historical_csv,
    ingest_day,
    upsert_daily_metric,
    upsert_showing_feedback,
)
from market.models import DailyUnitSnapshot
from properties.models import Property, Unit


@pytest.fixture(autouse=True)
def _rentengine_env(monkeypatch):
    """Ensure RentEngine settings are populated for all tests."""
    monkeypatch.setitem(
        settings.RENTENGINE, "API_TOKEN", "test-token-for-pytest"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prop(db):
    """Create a minimal Property."""
    return Property.objects.create(
        rentvine_id=1,
        name="123 Test St",
        address_line_1="123 Test St",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit(prop):
    """Create a Unit linked to the test property with a rentengine_id."""
    return Unit.objects.create(
        property=prop,
        rentvine_id=100,
        rentengine_id=5001,
        name="Unit A",
        address_line_1="123 Test St",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit_b(prop):
    """A second unit for multi-unit tests."""
    return Unit.objects.create(
        property=prop,
        rentvine_id=101,
        rentengine_id=5002,
        name="Unit B",
        address_line_1="123 Test St",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def api_payload():
    """Sample API response payload."""
    return {
        "unit_id": 5001,
        "days_on_market": 14,
        "property_health": "Healthy",
        "benchmark_leads_since_last_price_change": 3,
        "new_prospects": 5,
        "showings_scheduled": 4,
        "showings_completed": 2,
        "applications_requested": 1,
        "applications_submitted": 1,
        "active_prospects": 3,
        "upcoming_showings": 1,
        "outbound_texts": 10,
        "total_calls": 7,
        "showing_feedback": [
            {
                "prospect_id": 101,
                "prospect_name": "Alice Smith",
                "created_at": "2026-05-04T14:30:00Z",
                "feedback": "Loved the kitchen",
            },
            {
                "prospect_id": 102,
                "prospect_name": "Bob Jones",
                "created_at": "2026-05-04T16:00:00Z",
                "feedback": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# upsert_daily_metric
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertDailyMetric:
    def test_creates_new_row(self, unit, api_payload):
        metric, created = upsert_daily_metric(
            unit, date(2026, 5, 4), api_payload
        )

        assert created is True
        assert metric.unit == unit
        assert metric.date == date(2026, 5, 4)
        assert metric.new_prospects == 5
        assert metric.showings_completed == 2
        assert metric.applications_submitted == 1
        assert metric.days_on_market == 14
        assert metric.property_health == "Healthy"
        assert metric.source == "rentengine_api"
        assert metric.raw_payload == api_payload

    def test_overwrites_existing(self, unit, api_payload):
        """Late-arriving event scenario: second call overwrites first."""
        upsert_daily_metric(unit, date(2026, 5, 4), api_payload)

        updated_payload = {**api_payload, "new_prospects": 8}
        metric, created = upsert_daily_metric(
            unit, date(2026, 5, 4), updated_payload
        )

        assert created is False
        assert metric.new_prospects == 8
        assert DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 5, 4)
        ).count() == 1

    def test_computes_missed_correctly(self, unit):
        """missed = max(0, scheduled - completed - upcoming)"""
        payload = {
            "showings_scheduled": 5,
            "showings_completed": 2,
            "upcoming_showings": 1,
            "new_prospects": 0,
            "applications_submitted": 0,
        }
        metric, _ = upsert_daily_metric(unit, date(2026, 5, 4), payload)
        # 5 - 2 - 1 = 2
        assert metric.showings_missed_or_failed == 2

    def test_missed_floors_at_zero(self, unit):
        """missed never goes negative."""
        payload = {
            "showings_scheduled": 2,
            "showings_completed": 2,
            "upcoming_showings": 1,
            "new_prospects": 0,
            "applications_submitted": 0,
        }
        metric, _ = upsert_daily_metric(unit, date(2026, 5, 4), payload)
        assert metric.showings_missed_or_failed == 0

    def test_populates_feedback_summary(self, unit, api_payload):
        """showing_feedback_summary matches payload['showing_feedback']."""
        metric, _ = upsert_daily_metric(unit, date(2026, 5, 4), api_payload)

        assert len(metric.showing_feedback_summary) == 2
        assert metric.showing_feedback_summary[0] == {
            "prospect_id": 101,
            "prospect_name": "Alice Smith",
            "showing_completed_at": "2026-05-04T14:30:00Z",
            "feedback": "Loved the kitchen",
        }
        assert metric.showing_feedback_summary[1]["feedback"] is None

    def test_feedback_summary_empty_when_no_showings(self, unit):
        """No showing_feedback in payload -> empty list."""
        payload = {
            "new_prospects": 1,
            "showings_scheduled": 0,
            "showings_completed": 0,
            "upcoming_showings": 0,
            "applications_submitted": 0,
        }
        metric, _ = upsert_daily_metric(unit, date(2026, 5, 4), payload)
        assert metric.showing_feedback_summary == []


# ---------------------------------------------------------------------------
# upsert_showing_feedback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertShowingFeedback:
    def test_idempotent(self, unit):
        feedback = [
            {
                "prospect_id": 201,
                "prospect_name": "Carol Davis",
                "created_at": "2026-05-04T10:00:00Z",
                "feedback": "Great location",
            },
        ]

        count1 = upsert_showing_feedback(unit, feedback)
        count2 = upsert_showing_feedback(unit, feedback)

        assert count1 == 1
        assert count2 == 1  # upsert, not insert-only
        assert ShowingFeedback.objects.count() == 1

    def test_skips_missing_prospect_id(self, unit):
        feedback = [
            {
                "prospect_name": "No ID",
                "created_at": "2026-05-04T10:00:00Z",
                "feedback": "text",
            },
        ]
        assert upsert_showing_feedback(unit, feedback) == 0

    def test_skips_missing_created_at(self, unit):
        feedback = [
            {
                "prospect_id": 301,
                "prospect_name": "No Date",
                "feedback": "text",
            },
        ]
        assert upsert_showing_feedback(unit, feedback) == 0


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


def _write_csv(path, rows, headers=None):
    """Helper to write a CSV file."""
    import csv

    if headers is None:
        headers = [
            "Date",
            "Unit ID",
            "Property Address",
            "New Prospects",
            "Showings Completed",
            "Applications Submitted",
            "Showings Missed or Failed",
        ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.mark.django_db
class TestCSVImport:
    def test_resolves_existing_units(self, unit):
        """CSV rows for known rentengine_ids create DailyLeasingMetric rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            _write_csv(csv_path, [
                {
                    "Date": "2026-04-01",
                    "Unit ID": str(unit.rentengine_id),
                    "Property Address": "123 Test St",
                    "New Prospects": "3",
                    "Showings Completed": "2",
                    "Applications Submitted": "1",
                    "Showings Missed or Failed": "0",
                },
            ])

            result = import_historical_csv(csv_path)

        assert result["rows_imported"] == 1
        assert result["units_matched"] == 1
        assert result["units_unmatched"] == 0

        metric = DailyLeasingMetric.objects.get(
            unit=unit, date=date(2026, 4, 1)
        )
        assert metric.new_prospects == 3
        assert metric.source == "csv_backfill"

    def test_logs_unmatched_units(self, unit):
        """CSV rows with unknown rentengine_ids are logged, not imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            _write_csv(csv_path, [
                {
                    "Date": "2026-04-01",
                    "Unit ID": "99999",
                    "Property Address": "Unknown Addr",
                    "New Prospects": "1",
                    "Showings Completed": "0",
                    "Applications Submitted": "0",
                    "Showings Missed or Failed": "0",
                },
            ])

            result = import_historical_csv(csv_path)

            assert result["rows_imported"] == 0
            assert result["units_unmatched"] == 1
            assert 99999 in result["unmatched_unit_ids"]

            # Verify unmatched_units.txt was written
            txt_path = os.path.join(tmpdir, "unmatched_units.txt")
            assert os.path.exists(txt_path)
            with open(txt_path) as f:
                assert "99999" in f.read()

    def test_is_idempotent(self, unit):
        """Running import twice produces zero duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            _write_csv(csv_path, [
                {
                    "Date": "2026-04-01",
                    "Unit ID": str(unit.rentengine_id),
                    "Property Address": "123 Test St",
                    "New Prospects": "2",
                    "Showings Completed": "1",
                    "Applications Submitted": "0",
                    "Showings Missed or Failed": "1",
                },
            ])

            import_historical_csv(csv_path)
            result2 = import_historical_csv(csv_path)

        assert result2["rows_imported"] == 1
        assert DailyLeasingMetric.objects.filter(unit=unit).count() == 1

    def test_sets_empty_feedback_summary(self, unit):
        """CSV backfill rows have showing_feedback_summary=[]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            _write_csv(csv_path, [
                {
                    "Date": "2026-04-01",
                    "Unit ID": str(unit.rentengine_id),
                    "Property Address": "123 Test St",
                    "New Prospects": "1",
                    "Showings Completed": "0",
                    "Applications Submitted": "0",
                    "Showings Missed or Failed": "0",
                },
            ])

            import_historical_csv(csv_path)

        metric = DailyLeasingMetric.objects.get(
            unit=unit, date=date(2026, 4, 1)
        )
        assert metric.showing_feedback_summary == []

    def test_handles_url_addresses(self, unit):
        """Rows where address starts with http still import; address ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            _write_csv(csv_path, [
                {
                    "Date": "2026-04-01",
                    "Unit ID": str(unit.rentengine_id),
                    "Property Address": "https://rentvine.com/listings/12345",
                    "New Prospects": "1",
                    "Showings Completed": "0",
                    "Applications Submitted": "0",
                    "Showings Missed or Failed": "0",
                },
            ])

            result = import_historical_csv(csv_path)

        assert result["rows_imported"] == 1
        assert result["units_matched"] == 1


# ---------------------------------------------------------------------------
# ingest_day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIngestDay:
    @responses.activate
    def test_handles_429(self, unit):
        """ingest_day respects retry-after on 429."""
        base_url = settings.RENTENGINE["BASE_URL"]
        url = f"{base_url}/reporting/leasing-performance/units/{unit.rentengine_id}"

        # First call: 429 with Retry-After
        responses.add(
            responses.GET,
            url,
            json={"error": "rate limited", "retryAfter": 1},
            status=429,
        )
        # Second call: 429 again
        responses.add(
            responses.GET,
            url,
            json={"error": "rate limited", "retryAfter": 1},
            status=429,
        )
        # Third call: 429 again
        responses.add(
            responses.GET,
            url,
            json={"error": "rate limited", "retryAfter": 1},
            status=429,
        )
        # Fourth call: 429 (exhausts retries)
        responses.add(
            responses.GET,
            url,
            json={"error": "rate limited", "retryAfter": 1},
            status=429,
        )

        # Create a DailyUnitSnapshot so the unit is "active"
        DailyUnitSnapshot.objects.create(
            unit=unit,
            snapshot_date=date(2026, 5, 4),
            status="active",
        )

        result = ingest_day(date(2026, 5, 4))

        # Should have 1 error (exhausted retries), 0 processed
        assert result["units_processed"] == 0
        assert len(result["errors"]) == 1

    @responses.activate
    def test_atomic_failure_rolls_back_both(self, unit):
        """
        If ShowingFeedback upsert fails, DailyLeasingMetric upsert
        also rolls back.
        """
        base_url = settings.RENTENGINE["BASE_URL"]
        url = f"{base_url}/reporting/leasing-performance/units/{unit.rentengine_id}"

        payload = {
            "new_prospects": 1,
            "showings_scheduled": 1,
            "showings_completed": 1,
            "upcoming_showings": 0,
            "applications_submitted": 0,
            "showing_feedback": [
                {
                    "prospect_id": 501,
                    "prospect_name": "Test Person",
                    "created_at": "2026-05-04T10:00:00Z",
                    "feedback": "Nice place",
                },
            ],
        }
        responses.add(responses.GET, url, json=payload, status=200)

        DailyUnitSnapshot.objects.create(
            unit=unit,
            snapshot_date=date(2026, 5, 4),
            status="active",
        )

        with patch(
            "leasing.services.leasing_performance.upsert_showing_feedback",
            side_effect=RuntimeError("deliberate failure"),
        ):
            result = ingest_day(date(2026, 5, 4))

        assert result["units_processed"] == 0
        assert len(result["errors"]) == 1
        # Both should be rolled back
        assert DailyLeasingMetric.objects.count() == 0
        assert ShowingFeedback.objects.count() == 0


# ---------------------------------------------------------------------------
# showing_feedback idempotent (end-to-end via ingest_day)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIngestDayIdempotent:
    @responses.activate
    def test_rerun_produces_no_duplicates(self, unit):
        """Running ingest_day twice for the same date produces no duplicates."""
        base_url = settings.RENTENGINE["BASE_URL"]
        url = f"{base_url}/reporting/leasing-performance/units/{unit.rentengine_id}"

        payload = {
            "new_prospects": 2,
            "showings_scheduled": 1,
            "showings_completed": 1,
            "upcoming_showings": 0,
            "applications_submitted": 0,
            "showing_feedback": [
                {
                    "prospect_id": 601,
                    "prospect_name": "Rerun Test",
                    "created_at": "2026-05-04T12:00:00Z",
                    "feedback": "Feedback text",
                },
            ],
        }
        # Register response twice (one per call)
        responses.add(responses.GET, url, json=payload, status=200)
        responses.add(responses.GET, url, json=payload, status=200)

        DailyUnitSnapshot.objects.create(
            unit=unit,
            snapshot_date=date(2026, 5, 4),
            status="active",
        )

        ingest_day(date(2026, 5, 4))
        result2 = ingest_day(date(2026, 5, 4))

        assert result2["units_processed"] == 1
        assert DailyLeasingMetric.objects.count() == 1
        assert ShowingFeedback.objects.count() == 1


# ---------------------------------------------------------------------------
# Unit.display_address (canonical unit-level address rendering)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnitDisplayAddress:
    """Verify Unit.display_address renders unit-level grain correctly."""

    def test_multifamily_renders_per_unit(self, db):
        """A fourplex should produce four distinct display lines."""
        prop = Property.objects.create(
            rentvine_id=900,
            name="100 Elm St",
            address_line_1="100 Elm St",
            is_multi_unit=True,
        )
        labels = ["A", "B", "C", "D"]
        units = [
            Unit.objects.create(
                property=prop,
                rentvine_id=900 + i,
                address_line_1="100 Elm St",
                address_line_2=label,
            )
            for i, label in enumerate(labels)
        ]

        addresses = [u.display_address for u in units]

        assert len(set(addresses)) == 4, f"Expected 4 distinct addresses, got {addresses}"
        for label in labels:
            assert any(label in a for a in addresses), f"Missing unit '{label}' in {addresses}"

    def test_single_family_no_suffix(self, db):
        """Single-family with empty name/address_line_2 shows one clean line."""
        prop = Property.objects.create(
            rentvine_id=901,
            name="456 Oak Ave",
            address_line_1="456 Oak Ave",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=950,
            address_line_1="456 Oak Ave",
            name="",
        )

        assert unit.display_address == "456 Oak Ave"

    def test_single_family_name_matches_address(self, db):
        """When name equals the address (case-insensitive), no suffix appended."""
        prop = Property.objects.create(
            rentvine_id=902,
            name="456 Oak Ave",
            address_line_1="456 Oak Ave",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=951,
            address_line_1="456 Oak Ave",
            name="456 oak ave",
        )

        assert unit.display_address == "456 Oak Ave"

    def test_name_empty_no_suffix(self, db):
        """name="" (empty string) should not produce a suffix."""
        prop = Property.objects.create(
            rentvine_id=903,
            name="789 Pine Rd",
            address_line_1="789 Pine Rd",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=952,
            address_line_1="789 Pine Rd",
            name="",
        )

        assert unit.display_address == "789 Pine Rd"

    def test_suppresses_name_matching_address_line_2(self, db):
        """name == address_line_2 should not double the suffix."""
        prop = Property.objects.create(
            rentvine_id=904,
            name="588 Ray Hill Road",
            address_line_1="588 Ray Hill Road",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=953,
            address_line_1="588 Ray Hill Road",
            address_line_2="D",
            name="D",
        )

        assert unit.display_address == "588 Ray Hill Road - D"

    def test_suppresses_word_subset_of_address_line_1(self, db):
        """name whose words are a subset of address_line_1 should be suppressed."""
        prop = Property.objects.create(
            rentvine_id=905,
            name="555 Baldwin Avenue",
            address_line_1="555 Baldwin Avenue",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=954,
            address_line_1="555 Baldwin Avenue",
            name="Baldwin Avenue 555",
        )

        assert unit.display_address == "555 Baldwin Avenue"

    def test_unit_str_uses_display_address(self, db):
        """Unit.__str__() should return the same value as display_address."""
        prop = Property.objects.create(
            rentvine_id=906,
            name="100 Elm St",
            address_line_1="100 Elm St",
        )
        unit = Unit.objects.create(
            property=prop,
            rentvine_id=955,
            address_line_1="100 Elm St",
            address_line_2="B",
            name="B",
        )

        assert str(unit) == unit.display_address
        assert str(unit) == "100 Elm St - B"


# ---------------------------------------------------------------------------
# Slack message format
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPostLeasingSummaryFormat:
    """Verify Slack message matches Apps Script format."""

    def test_post_leasing_summary_format_matches_apps_script(self, db):
        from leasing.management.commands.post_leasing_summary import (
            _build_summary,
            _format_slack_message,
        )

        prop1 = Property.objects.create(
            rentvine_id=910,
            name="123 Main St",
            address_line_1="123 Main St",
        )
        unit1 = Unit.objects.create(
            property=prop1,
            rentvine_id=960,
            address_line_1="123 Main St",
            name="",
        )

        prop2 = Property.objects.create(
            rentvine_id=911,
            name="456 Oak Ave",
            address_line_1="456 Oak Ave",
        )
        unit2 = Unit.objects.create(
            property=prop2,
            rentvine_id=961,
            address_line_1="456 Oak Ave",
            address_line_2="Apt A",
            name="Apt A",
        )

        DailyLeasingMetric.objects.create(
            unit=unit1,
            date=date(2026, 5, 4),
            new_prospects=2,
            showings_completed=1,
            applications_submitted=0,
            showings_missed_or_failed=0,
            source="rentengine_api",
        )
        DailyLeasingMetric.objects.create(
            unit=unit2,
            date=date(2026, 5, 4),
            new_prospects=1,
            showings_completed=0,
            applications_submitted=0,
            showings_missed_or_failed=1,
            source="rentengine_api",
        )

        summary = _build_summary(date(2026, 5, 4))
        payload = _format_slack_message(summary)
        text = payload["text"]

        # Header
        assert "\u2705 Daily Leasing Summary" in text
        assert "Monday, May 4, 2026" in text

        # Grand totals with emojis
        assert "\U0001f525 New Leads: 3" in text
        assert "\U0001f440 Showings Completed: 1" in text
        assert "\U0001f6ab Showings Missed/Failed: 1" in text
        assert "\U0001f4dd Applications Received: 0" in text

        # Field order: Missed BEFORE Apps in grand totals
        missed_pos = text.index("Missed/Failed")
        apps_pos = text.index("Applications Received")
        assert missed_pos < apps_pos

        # Per-property breakdown with word labels
        assert "\u2022 *123 Main St*: 2 Leads | 1 Showings | 0 Missed | 0 Apps" in text
        assert "\u2022 *456 Oak Ave - Apt A*: 1 Leads | 0 Showings | 1 Missed | 0 Apps" in text

        # No single-letter abbreviations
        assert "P /" not in text
        assert "S /" not in text
        assert "A /" not in text
        assert "M" not in text.split("Property Breakdown")[1].split("\n")[0]  # no bare M

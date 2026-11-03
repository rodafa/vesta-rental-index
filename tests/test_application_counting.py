"""
Tests for group-based application counting.

Covers is_group_paid(), build_unit_applications(), and the privacy guard.
"""

from datetime import date

import pytest

from integrations.rentengine.mappers import (
    build_unit_applications,
    is_group_paid,
)


# ---------------------------------------------------------------------------
# is_group_paid
# ---------------------------------------------------------------------------

def _make_group(applicant_screenings, **overrides):
    """Build a minimal group dict with the given screening states."""
    applicants = []
    for screening in applicant_screenings:
        applicants.append({"screening": screening})
    group = {
        "id": "aaa-111",
        "unit_id": 100,
        "status": "Submitted",
        "submitted_at": "2026-10-20",
        "applicants": applicants,
    }
    group.update(overrides)
    return group


class TestIsGroupPaid:
    def test_paid_when_screening_has_credit_status(self):
        group = _make_group([{"credit_status": "ReportCompleted"}])
        assert is_group_paid(group) is True

    def test_not_paid_when_screening_is_null(self):
        group = _make_group([None])
        assert is_group_paid(group) is False

    def test_not_paid_when_credit_status_is_null(self):
        group = _make_group([{"credit_status": None}])
        assert is_group_paid(group) is False

    def test_paid_when_one_of_two_applicants_has_screening(self):
        group = _make_group([None, {"credit_status": "ReportCompleted"}])
        assert is_group_paid(group) is True

    def test_not_paid_when_no_applicants(self):
        group = _make_group([])
        assert is_group_paid(group) is False

    def test_not_paid_when_applicants_key_missing(self):
        group = {"id": "x", "unit_id": 1, "status": "Submitted"}
        assert is_group_paid(group) is False

    def test_archived_group_still_paid(self):
        group = _make_group(
            [{"credit_status": "ReportCompleted"}],
            status="Archived",
        )
        assert is_group_paid(group) is True

    def test_rejected_group_still_paid(self):
        group = _make_group(
            [{"credit_status": "ReportCompleted"}],
            status="Rejected",
        )
        assert is_group_paid(group) is True


# ---------------------------------------------------------------------------
# build_unit_applications
# ---------------------------------------------------------------------------

PERIOD_START = date(2026, 10, 21)
PERIOD_END = date(2026, 10, 27)


def _paid_group(group_id, unit_id, submitted_at, status="Submitted",
                num_applicants=1):
    """Build a paid group with the given number of applicants."""
    applicants = [
        {"screening": {"credit_status": "ReportCompleted"}}
        for _ in range(num_applicants)
    ]
    return {
        "id": group_id,
        "unit_id": unit_id,
        "status": status,
        "submitted_at": submitted_at,
        "applicants": applicants,
    }


def _unpaid_group(group_id, unit_id, submitted_at, status="Submitted"):
    """Build an unpaid group (screening is null)."""
    return {
        "id": group_id,
        "unit_id": unit_id,
        "status": status,
        "submitted_at": submitted_at,
        "applicants": [{"screening": None}],
    }


class TestBuildUnitApplications:
    def test_paid_group_in_period_counted(self):
        groups = [_paid_group("g1", 100, "2026-10-22")]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts[100] == 1
        assert len(apps[100]) == 1
        assert apps[100][0]["group_id"] == "g1"

    def test_unpaid_group_excluded(self):
        groups = [_unpaid_group("g1", 100, "2026-10-22")]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts == {}
        assert apps == {}

    def test_two_applicants_one_group_counts_as_one(self):
        groups = [_paid_group("g1", 100, "2026-10-22", num_applicants=2)]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts[100] == 1
        assert len(apps[100]) == 1
        assert apps[100][0]["num_applicants"] == 2

    def test_group_before_period_not_in_count(self):
        groups = [_paid_group("g1", 100, "2026-10-15")]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        # Not in period count
        assert counts.get(100, 0) == 0

    def test_carried_over_open_group_in_apps_list(self):
        """A paid group from before the period with status=Submitted
        should appear in apps but NOT in period_counts."""
        groups = [_paid_group("g1", 100, "2026-10-15", status="Submitted")]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts.get(100, 0) == 0
        assert len(apps[100]) == 1

    def test_carried_over_resolved_group_excluded(self):
        """A paid group from before the period with status=Approved
        should NOT appear in apps."""
        for status in ["Approved", "Rejected", "Archived", "Moved In"]:
            groups = [_paid_group("g1", 100, "2026-10-15", status=status)]
            counts, apps = build_unit_applications(
                groups, PERIOD_START, PERIOD_END,
            )
            assert counts.get(100, 0) == 0
            assert apps.get(100, []) == [], f"status={status} should be excluded"

    def test_resolved_group_in_period_still_counted(self):
        """A paid group with status=Archived but submitted_at in period
        IS counted (resolved status does not exclude from period count)."""
        groups = [_paid_group("g1", 100, "2026-10-22", status="Archived")]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts[100] == 1
        assert len(apps[100]) == 1

    def test_multiple_groups_same_unit(self):
        groups = [
            _paid_group("g1", 100, "2026-10-22"),
            _paid_group("g2", 100, "2026-10-23"),
        ]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts[100] == 2
        assert len(apps[100]) == 2

    def test_workflow_step_enriched_from_reporting(self):
        groups = [_paid_group("g1", 100, "2026-10-22")]
        reporting = [
            {"application_group_id": "g1", "current_workflow_step": "Lease"},
        ]
        _, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END, reporting_rows=reporting,
        )
        assert apps[100][0]["workflow_step"] == "Lease"

    def test_privacy_guard_exact_keys(self):
        groups = [_paid_group("g1", 100, "2026-10-22")]
        _, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        expected_keys = {"group_id", "status", "submitted_at",
                         "num_applicants", "workflow_step"}
        assert set(apps[100][0].keys()) == expected_keys

    def test_missing_submitted_at_excluded(self):
        groups = [_paid_group("g1", 100, None)]
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts == {}
        assert apps == {}

    def test_missing_unit_id_excluded(self):
        groups = [_paid_group("g1", None, "2026-10-22")]
        # unit_id=None should be skipped
        counts, apps = build_unit_applications(
            groups, PERIOD_START, PERIOD_END,
        )
        assert counts == {}
        assert apps == {}

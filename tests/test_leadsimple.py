"""
Tests for the LeadSimple integration: client, services, selectors.

Fixtures derived from real API probe output (PII anonymized).
"""

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest
import zoneinfo

from core.models import Owner, Portfolio, Property, Unit
from integrations.leadsimple.client import DEGRADED, fetch_processes
from integrations.leadsimple.services import (
    PROCESS_TYPE_MAP,
    ET,
    _parse_dt_et,
    build_owner_data,
    build_unit_lookup,
    classify_processes,
    cross_check_owner,
    filter_for_monthly,
    match_to_units,
    normalize_unit_number,
)
from integrations.leadsimple.selectors import get_owner_pipeline_data


# ---------------------------------------------------------------------------
# Realistic process fixtures (anonymized from live probe)
# ---------------------------------------------------------------------------

def _make_process(
    process_id, process_type_id, process_type_name, stage_name, stage_status,
    created_at, closed_at, address, unit_number, owner_contacts=None,
    properties_present=True,
):
    """Build a realistic process dict matching the live API shape."""
    props = []
    if properties_present:
        unit_obj = {"unit_number": unit_number} if unit_number is not None else {"unit_number": None}
        props = [{
            "address": address,
            "city": "Asheville",
            "state": "NC",
            "zip_code": "28801",
            "full_address": {
                "line_1": address,
                "locality": "Asheville",
                "region": "NC",
                "postal_code": "28801",
            },
            "unit": unit_obj,
        }]

    contact_roles = []
    if owner_contacts:
        contact_roles.append({
            "key": "owners",
            "name": "Owners",
            "contacts": owner_contacts,
        })

    return {
        "id": process_id,
        "process_type": {
            "id": process_type_id,
            "name": process_type_name,
        },
        "process_type_id": process_type_id,
        "stage": {
            "name": stage_name,
            "status": stage_status,
        },
        "name": f"Test Process {process_id[:8]}",
        "created_at": created_at,
        "closed_at": closed_at,
        "properties": props,
        "contact_roles": contact_roles,
        "comments": None,
    }


# 1. Renewal (monthly) — open
FIXTURE_RENEWAL_OPEN = _make_process(
    process_id="aaaa1111-0000-0000-0000-000000000001",
    process_type_id="1865ad03-f740-4a99-b1a2-e2440430f3f4",
    process_type_name="06 Lease Renewals",
    stage_name="Renewal Offer Sent",
    stage_status="working",
    created_at="2026-04-15T14:00:00Z",
    closed_at=None,
    address="100 Main Street",
    unit_number=None,
)

# 2. Move Out (monthly) — closed within May
FIXTURE_MOVEOUT_CLOSED_MAY = _make_process(
    process_id="aaaa2222-0000-0000-0000-000000000002",
    process_type_id="e0dfad31-5459-4578-a0a1-b062180e769b",
    process_type_name="08 Move Out",
    stage_name="Move Out Complete",
    stage_status="won",
    created_at="2026-03-01T10:00:00Z",
    closed_at="2026-05-20T16:30:00Z",
    address="200 Oak Avenue",
    unit_number=None,
)

# 3. Issues (monthly) — open
FIXTURE_ISSUES_OPEN = _make_process(
    process_id="aaaa3333-0000-0000-0000-000000000003",
    process_type_id="98e21401-58d3-4a6a-b6b0-f22dd11dc831",
    process_type_name="Issues",
    stage_name="Issue Type - Tenant Support Ticket",
    stage_status="working",
    created_at="2026-05-27T18:37:09Z",
    closed_at=None,
    address="300 Elm Drive",
    unit_number="Unit 3",
)

# 4. Rehab to Turn (monthly) — closed within May
FIXTURE_REHAB_CLOSED_MAY = _make_process(
    process_id="aaaa4444-0000-0000-0000-000000000004",
    process_type_id="83f4af06-ad96-4ca7-9d21-5a1b00087ac6",
    process_type_name="09 Rehab to Turn",
    stage_name="Rehab Complete",
    stage_status="won",
    created_at="2026-02-10T12:00:00Z",
    closed_at="2026-05-15T09:00:00Z",
    address="400 Pine Lane",
    unit_number="Apt A",
)

# 5. Applications (weekly — excluded from monthly)
FIXTURE_APPLICATION_WEEKLY = _make_process(
    process_id="aaaa5555-0000-0000-0000-000000000005",
    process_type_id="c9927e92-5c41-4c33-8a9e-8dbd6312eeb6",
    process_type_name="04 Applications",
    stage_name="Application Process",
    stage_status="working",
    created_at="2026-05-28T20:34:49Z",
    closed_at=None,
    address="500 Cedar Court",
    unit_number=None,
)

# 6. Unknown process type
FIXTURE_UNKNOWN_TYPE = _make_process(
    process_id="aaaa6666-0000-0000-0000-000000000006",
    process_type_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    process_type_name="99 Unknown Pipeline",
    stage_name="Some Stage",
    stage_status="working",
    created_at="2026-05-01T10:00:00Z",
    closed_at=None,
    address="600 Birch Boulevard",
    unit_number=None,
)

# 7. Zero-property process
FIXTURE_NO_PROPERTY = _make_process(
    process_id="aaaa7777-0000-0000-0000-000000000007",
    process_type_id="864f1bf2-53c8-4135-9196-9f6ecf15cf16",
    process_type_name="05 Move Ins",
    stage_name="Move In Scheduled",
    stage_status="working",
    created_at="2026-05-10T12:00:00Z",
    closed_at=None,
    address="",
    unit_number=None,
    properties_present=False,
)

# 8. Move Out closed in APRIL (outside May window)
FIXTURE_MOVEOUT_CLOSED_APRIL = _make_process(
    process_id="aaaa8888-0000-0000-0000-000000000008",
    process_type_id="e0dfad31-5459-4578-a0a1-b062180e769b",
    process_type_name="08 Move Out",
    stage_name="Move Out Complete",
    stage_status="won",
    created_at="2026-02-01T10:00:00Z",
    closed_at="2026-04-28T14:00:00Z",
    address="700 Maple Way",
    unit_number=None,
)

ALL_FIXTURES = [
    FIXTURE_RENEWAL_OPEN,
    FIXTURE_MOVEOUT_CLOSED_MAY,
    FIXTURE_ISSUES_OPEN,
    FIXTURE_REHAB_CLOSED_MAY,
    FIXTURE_APPLICATION_WEEKLY,
    FIXTURE_UNKNOWN_TYPE,
    FIXTURE_NO_PROPERTY,
    FIXTURE_MOVEOUT_CLOSED_APRIL,
]


# ---------------------------------------------------------------------------
# Tests: normalize_unit_number
# ---------------------------------------------------------------------------


class TestNormalizeUnitNumber:
    def test_unit_prefix(self):
        assert normalize_unit_number("Unit 3") == "3"

    def test_apt_prefix(self):
        assert normalize_unit_number("Apt A") == "a"

    def test_suite_prefix(self):
        assert normalize_unit_number("Suite 200") == "200"

    def test_hash_prefix(self):
        assert normalize_unit_number("#31") == "31"

    def test_plain_number(self):
        assert normalize_unit_number("31") == "31"

    def test_letter_only(self):
        assert normalize_unit_number("Unit E") == "e"

    def test_empty(self):
        assert normalize_unit_number("") == ""

    def test_none(self):
        assert normalize_unit_number(None) == ""

    def test_full_address_as_unit(self):
        """LeadSimple sometimes puts the full address in unit_number."""
        result = normalize_unit_number("Sulphur Springs Road 312")
        assert result == "sulphurspringsroad312"


# ---------------------------------------------------------------------------
# Tests: classify_processes
# ---------------------------------------------------------------------------


class TestClassifyProcesses:
    def test_known_types_classified(self):
        result = classify_processes(ALL_FIXTURES)
        # Unknown type excluded, 7 remaining (including weekly, excluded targets)
        categories = [p["category"] for p in result]
        assert "renewal" in categories
        assert "move_out" in categories
        assert "issues" in categories
        assert "rehab_to_turn" in categories
        assert "application" in categories
        assert "move_in" in categories

    def test_unknown_type_excluded(self):
        result = classify_processes([FIXTURE_UNKNOWN_TYPE])
        assert len(result) == 0

    def test_unknown_type_logged(self):
        with patch("integrations.leadsimple.services.logger") as mock_logger:
            classify_processes([FIXTURE_UNKNOWN_TYPE])
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "leadsimple_unknown_process_type"
            assert call_args[1]["extra"]["process_type_id"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"

    def test_all_13_uuids_mapped(self):
        assert len(PROCESS_TYPE_MAP) == 13

    def test_duplicate_normalization(self):
        """Both '06 Lease Renewals' and '070 Lease Renewals' map to 'renewal'."""
        assert PROCESS_TYPE_MAP["1865ad03-f740-4a99-b1a2-e2440430f3f4"]["category"] == "renewal"
        assert PROCESS_TYPE_MAP["44ac428c-30c3-4548-8a59-6cc5c687e5f8"]["category"] == "renewal"

    def test_move_out_duplicate_normalization(self):
        """Both '08 Move Out' and '09 Move Outs' map to 'move_out'."""
        assert PROCESS_TYPE_MAP["e0dfad31-5459-4578-a0a1-b062180e769b"]["category"] == "move_out"
        assert PROCESS_TYPE_MAP["8311a377-6d68-48a0-855d-145fd1380ff4"]["category"] == "move_out"


# ---------------------------------------------------------------------------
# Tests: filter_for_monthly (ET boundaries)
# ---------------------------------------------------------------------------


class TestFilterForMonthly:
    def setup_method(self):
        self.classified = classify_processes(ALL_FIXTURES)
        self.may_start = date(2026, 5, 1)
        self.may_end = date(2026, 5, 31)

    def test_includes_open_monthly(self):
        result = filter_for_monthly(self.classified, self.may_start, self.may_end)
        ids = [p["id"] for p in result]
        # Open renewal and open issues should be included
        assert FIXTURE_RENEWAL_OPEN["id"] in ids
        assert FIXTURE_ISSUES_OPEN["id"] in ids

    def test_includes_closed_this_month(self):
        result = filter_for_monthly(self.classified, self.may_start, self.may_end)
        ids = [p["id"] for p in result]
        assert FIXTURE_MOVEOUT_CLOSED_MAY["id"] in ids
        assert FIXTURE_REHAB_CLOSED_MAY["id"] in ids

    def test_excludes_closed_last_month(self):
        result = filter_for_monthly(self.classified, self.may_start, self.may_end)
        ids = [p["id"] for p in result]
        assert FIXTURE_MOVEOUT_CLOSED_APRIL["id"] not in ids

    def test_excludes_weekly_targets(self):
        result = filter_for_monthly(self.classified, self.may_start, self.may_end)
        ids = [p["id"] for p in result]
        assert FIXTURE_APPLICATION_WEEKLY["id"] not in ids

    def test_et_boundary_last_moment_of_month(self):
        """Process closed 23:30 ET on May 31 lands IN May, not June."""
        proc = _make_process(
            process_id="boundary-test-001",
            process_type_id="98e21401-58d3-4a6a-b6b0-f22dd11dc831",
            process_type_name="Issues",
            stage_name="Resolved",
            stage_status="won",
            created_at="2026-05-01T10:00:00Z",
            # 23:30 ET on May 31 = 03:30 UTC June 1
            closed_at="2026-06-01T03:30:00Z",
            address="900 Test St",
            unit_number=None,
        )
        classified = classify_processes([proc])
        result = filter_for_monthly(classified, self.may_start, self.may_end)
        assert len(result) == 1

    def test_et_boundary_first_moment_of_next_month(self):
        """Process closed 00:01 ET on June 1 lands in June, NOT May."""
        proc = _make_process(
            process_id="boundary-test-002",
            process_type_id="98e21401-58d3-4a6a-b6b0-f22dd11dc831",
            process_type_name="Issues",
            stage_name="Resolved",
            stage_status="won",
            created_at="2026-05-01T10:00:00Z",
            # 00:01 ET on June 1 = 04:01 UTC June 1
            closed_at="2026-06-01T04:01:00Z",
            address="901 Test St",
            unit_number=None,
        )
        classified = classify_processes([proc])
        result = filter_for_monthly(classified, self.may_start, self.may_end)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: match_to_units
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchToUnits:
    @pytest.fixture
    def portfolio(self):
        return Portfolio.objects.create(rentvine_id=500, name="Test Portfolio")

    @pytest.fixture
    def owner(self, portfolio):
        o = Owner.objects.create(
            rentvine_contact_id=500,
            name="Test Owner",
            first_name="Test",
            email="testowner@example.com",
            is_active=True,
        )
        o.portfolios.add(portfolio)
        return o

    @pytest.fixture
    def prop_main(self, portfolio):
        return Property.objects.create(
            rentvine_id=501,
            portfolio=portfolio,
            address_line_1="100 Main Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            is_active=True,
        )

    @pytest.fixture
    def unit_main(self, prop_main):
        return Unit.objects.create(
            rentvine_id=601,
            property=prop_main,
            name="100 Main St",
            address_line_1="100 Main Street",
            unit_number="",
            is_active=True,
        )

    @pytest.fixture
    def prop_multi(self, portfolio):
        return Property.objects.create(
            rentvine_id=502,
            portfolio=portfolio,
            address_line_1="300 Elm Drive",
            city="Asheville",
            state="NC",
            postal_code="28801",
            is_active=True,
        )

    @pytest.fixture
    def unit_3(self, prop_multi):
        """Core unit with unit_number="3" — should match LeadSimple "Unit 3"."""
        return Unit.objects.create(
            rentvine_id=603,
            property=prop_multi,
            name="300 Elm Dr - 3",
            address_line_1="300 Elm Drive",
            unit_number="3",
            is_active=True,
        )

    def test_single_unit_match(self, unit_main):
        lookup = build_unit_lookup(Unit.objects.filter(pk=unit_main.pk))
        matched = match_to_units([FIXTURE_RENEWAL_OPEN], lookup)
        assert len(matched) == 1
        assert matched[0][1] == unit_main

    def test_multi_unit_match_with_normalization(self, unit_3):
        """'Unit 3' in LeadSimple matches core unit_number='3'."""
        lookup = build_unit_lookup(Unit.objects.filter(pk=unit_3.pk))
        matched = match_to_units([FIXTURE_ISSUES_OPEN], lookup)
        assert len(matched) == 1
        assert matched[0][1] == unit_3

    def test_zero_property_skipped(self, unit_main):
        lookup = build_unit_lookup(Unit.objects.filter(pk=unit_main.pk))
        matched = match_to_units([FIXTURE_NO_PROPERTY], lookup)
        assert len(matched) == 0

    def test_no_match_returns_none(self, unit_main):
        """Process with address not in lookup returns (proc, None)."""
        lookup = build_unit_lookup(Unit.objects.filter(pk=unit_main.pk))
        matched = match_to_units([FIXTURE_MOVEOUT_CLOSED_MAY], lookup)
        assert len(matched) == 1
        assert matched[0][1] is None


# ---------------------------------------------------------------------------
# Tests: client degraded sentinel
# ---------------------------------------------------------------------------


class TestClientDegraded:
    def test_missing_key_returns_degraded(self, settings):
        settings.LEADSIMPLE_API_KEY = ""
        result = fetch_processes()
        assert result is DEGRADED

    def test_missing_key_attr_returns_degraded(self, settings):
        if hasattr(settings, "LEADSIMPLE_API_KEY"):
            delattr(settings, "LEADSIMPLE_API_KEY")
        result = fetch_processes()
        assert result is DEGRADED

    def test_api_failure_returns_degraded(self, settings):
        settings.LEADSIMPLE_API_KEY = "test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        with patch("integrations.leadsimple.client.requests.get", return_value=mock_resp):
            result = fetch_processes()
        assert result is DEGRADED

    def test_success_returns_list(self, settings):
        settings.LEADSIMPLE_API_KEY = "test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [FIXTURE_RENEWAL_OPEN],
            "meta": {"total_pages": 1},
        }
        with patch("integrations.leadsimple.client.requests.get", return_value=mock_resp):
            result = fetch_processes()
        assert isinstance(result, list)
        assert len(result) == 1

    def test_page_cap_logged(self, settings):
        settings.LEADSIMPLE_API_KEY = "test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [FIXTURE_RENEWAL_OPEN],
            "meta": {"total_pages": 100},
        }
        with patch("integrations.leadsimple.client.requests.get", return_value=mock_resp), \
             patch("integrations.leadsimple.client.logger") as mock_logger:
            fetch_processes(max_pages=5)
            mock_logger.warning.assert_any_call(
                "leadsimple_page_cap_hit",
                extra={
                    "total_pages": 100,
                    "max_pages": 5,
                    "fetched_so_far": 1,
                },
            )


# ---------------------------------------------------------------------------
# Tests: selector
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSelector:
    @pytest.fixture
    def portfolio(self):
        return Portfolio.objects.create(rentvine_id=700, name="Selector Portfolio")

    @pytest.fixture
    def owner(self, portfolio):
        o = Owner.objects.create(
            rentvine_contact_id=700,
            name="Selector Owner",
            first_name="Selector",
            email="selector@example.com",
            is_active=True,
        )
        o.portfolios.add(portfolio)
        return o

    @pytest.fixture
    def prop(self, portfolio):
        return Property.objects.create(
            rentvine_id=700,
            portfolio=portfolio,
            address_line_1="100 Main Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            is_active=True,
        )

    @pytest.fixture
    def unit(self, prop):
        return Unit.objects.create(
            rentvine_id=700,
            property=prop,
            name="100 Main St",
            address_line_1="100 Main Street",
            unit_number="",
            is_active=True,
        )

    def test_degraded_propagated(self, owner):
        with patch("integrations.leadsimple.selectors.fetch_processes", return_value=DEGRADED):
            result = get_owner_pipeline_data(owner, date(2026, 5, 1), date(2026, 5, 31))
        assert result["_degraded"] is True
        assert result["_has_data"] is False

    def test_empty_processes_has_data_false(self, owner, unit):
        with patch("integrations.leadsimple.selectors.fetch_processes", return_value=[]):
            result = get_owner_pipeline_data(owner, date(2026, 5, 1), date(2026, 5, 31))
        assert result["_degraded"] is False
        assert result["_has_data"] is False

    def test_matched_processes_has_data_true(self, owner, unit):
        with patch("integrations.leadsimple.selectors.fetch_processes", return_value=[FIXTURE_RENEWAL_OPEN]):
            result = get_owner_pipeline_data(owner, date(2026, 5, 1), date(2026, 5, 31))
        assert result["_degraded"] is False
        assert result["_has_data"] is True
        assert result["total_count"] == 1
        assert "renewal" in result["processes_by_category"]


# ---------------------------------------------------------------------------
# Tests: owner cross-check
# ---------------------------------------------------------------------------


class TestOwnerCrossCheck:
    def test_email_match_no_log(self):
        proc = _make_process(
            "cc-001", "98e21401-58d3-4a6a-b6b0-f22dd11dc831", "Issues",
            "Open", "working", "2026-05-01T10:00:00Z", None, "100 Main St", None,
            owner_contacts=[{"name": "Jane Doe", "emails": ["jane@example.com"], "tags": []}],
        )
        owner = MagicMock(name="Jane Doe", email="jane@example.com")
        with patch("integrations.leadsimple.services.logger") as mock_logger:
            cross_check_owner(proc, owner)
            mock_logger.info.assert_not_called()

    def test_email_mismatch_logged(self):
        proc = _make_process(
            "cc-002", "98e21401-58d3-4a6a-b6b0-f22dd11dc831", "Issues",
            "Open", "working", "2026-05-01T10:00:00Z", None, "100 Main St", None,
            owner_contacts=[{"name": "John Smith", "emails": ["john@other.com"], "tags": []}],
        )
        owner = MagicMock(name="Jane Doe", email="jane@example.com")
        with patch("integrations.leadsimple.services.logger") as mock_logger:
            cross_check_owner(proc, owner)
            mock_logger.info.assert_called_once()
            assert mock_logger.info.call_args[0][0] == "leadsimple_owner_email_mismatch"


# ---------------------------------------------------------------------------
# Tests: ET datetime parsing
# ---------------------------------------------------------------------------


class TestParseDtEt:
    def test_utc_string(self):
        result = _parse_dt_et("2026-05-31T23:30:00Z")
        assert result.tzinfo is not None
        assert result.tzname() in ("EDT", "EST", "ET")

    def test_utc_to_et_conversion(self):
        # 2026-05-31 23:30 UTC = 2026-05-31 19:30 EDT
        result = _parse_dt_et("2026-05-31T23:30:00Z")
        assert result.hour == 19
        assert result.day == 31

    def test_june1_0330_utc_is_may31_et(self):
        """03:30 UTC June 1 = 23:30 EDT May 31 — still in May."""
        result = _parse_dt_et("2026-06-01T03:30:00Z")
        assert result.month == 5
        assert result.day == 31

    def test_june1_0401_utc_is_june1_et(self):
        """04:01 UTC June 1 = 00:01 EDT June 1 — in June."""
        result = _parse_dt_et("2026-06-01T04:01:00Z")
        assert result.month == 6
        assert result.day == 1

    def test_none_returns_none(self):
        assert _parse_dt_et(None) is None

    def test_empty_returns_none(self):
        assert _parse_dt_et("") is None

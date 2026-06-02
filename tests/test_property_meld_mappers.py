"""
Unit tests for Property Meld field mapper.
"""

from datetime import date, datetime, timezone

import pytest

from integrations.property_meld.mappers import (
    map_meld,
    _extract_coordinator_name,
    _extract_vendor_name,
    _extract_scheduled_date,
    _normalise_owner_approval,
)


# ---------------------------------------------------------------------------
# Realistic API record fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_meld_record():
    return {
        "id": 98765,
        "brief_description": "Leaking kitchen faucet",
        "description": "Tenant reports a slow drip from the kitchen faucet.",
        "work_category": "Plumbing",
        "priority": "MEDIUM",
        "status": "PENDING_COMPLETION",
        "reference_id": "M-1234",
        "work_type": "Repair",
        "work_location": "Kitchen",
        "origin": "TENANT",
        "is_active": True,
        "coordinator": {
            "user": {"first_name": "Zach", "last_name": "Miller"},
        },
        "vendor_assignment_requests": [
            {
                "accepted": "2026-05-01T10:00:00Z",
                "rejected": None,
                "canceled": None,
                "vendor": {"name": "ABC Plumbing"},
            },
        ],
        "prop_address": {"full_address": "123 Main St, Denver, CO 80202"},
        "unit_address": {"full_address": "123 Main St Unit A, Denver, CO 80202"},
        "prop": 555,
        "unit": 777,
        "tenant_presence_required": True,
        "vendorappointment": [
            {
                "availability_segment": {
                    "event": {"dtstart": "2026-05-15"},
                },
            },
        ],
        "managementappointment": [],
        "marked_complete": None,
        "completion_date": None,
        "started": "2026-05-01T10:30:00Z",
        "due_date": "2026-05-20",
        "owner_approval_status": "OWNER_APPROVAL_APPROVED",
        "work_entries": [{"id": 1}],
        "maintenance_notes": "Needs new washer.",
        "completion_notes": "",
        "reason_cannot_complete": "",
        "resolution_type": "",
        "parent": None,
        "recurring_meld": None,
        "merged_meld": None,
        "tenant_rating": 4,
        "created": "2026-04-28T08:00:00Z",
        "updated": "2026-05-01T10:30:00Z",
    }


class TestMapMeld:
    def test_basic_fields(self, sample_meld_record):
        pm_id, defaults = map_meld(sample_meld_record)
        assert pm_id == "98765"
        assert defaults["brief_description"] == "Leaking kitchen faucet"
        assert defaults["category"] == "Plumbing"
        assert defaults["priority"] == "MEDIUM"
        assert defaults["status"] == "PENDING_COMPLETION"
        assert defaults["reference_id"] == "M-1234"
        assert defaults["work_type"] == "Repair"
        assert defaults["work_location"] == "Kitchen"
        assert defaults["origin"] == "TENANT"
        assert defaults["is_active"] is True

    def test_coordinator_extraction(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["coordinator_name"] == "Zach Miller"

    def test_vendor_extraction(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["assigned_vendor_name"] == "ABC Plumbing"

    def test_address_prefers_prop(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["property_address"] == "123 Main St, Denver, CO 80202"

    def test_address_falls_back_to_unit(self, sample_meld_record):
        sample_meld_record["prop_address"] = {}
        _, defaults = map_meld(sample_meld_record)
        assert defaults["property_address"] == "123 Main St Unit A, Denver, CO 80202"

    def test_property_and_unit_refs(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["property_meld_property_id"] == "555"
        assert defaults["unit_ref"] == "777"

    def test_scheduled_date(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["scheduled_date"] == date(2026, 5, 15)

    def test_date_fields(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["started"] == datetime(2026, 5, 1, 10, 30, tzinfo=timezone.utc)
        assert defaults["due_date"] == date(2026, 5, 20)
        assert defaults["marked_complete"] is None
        assert defaults["completion_date"] is None

    def test_owner_approval(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["owner_approval_status"] == "Approved"

    def test_has_invoice_from_work_entries(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["has_invoice"] is True

    def test_no_work_entries_means_no_invoice(self, sample_meld_record):
        sample_meld_record["work_entries"] = []
        _, defaults = map_meld(sample_meld_record)
        assert defaults["has_invoice"] is False

    def test_tenant_rating(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["tenant_rating"] == 4

    def test_source_timestamps(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["source_created_at"] == datetime(
            2026, 4, 28, 8, 0, tzinfo=timezone.utc
        )
        assert defaults["source_modified_at"] == datetime(
            2026, 5, 1, 10, 30, tzinfo=timezone.utc
        )

    def test_raw_data_preserved(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["raw_data"]["id"] == 98765

    def test_missing_id_raises(self):
        with pytest.raises(ValueError, match="No id"):
            map_meld({"brief_description": "no id here"})

    def test_resident_presence(self, sample_meld_record):
        _, defaults = map_meld(sample_meld_record)
        assert defaults["resident_presence_required"] is True


class TestExtractHelpers:
    def test_coordinator_empty(self):
        assert _extract_coordinator_name(None) == ""
        assert _extract_coordinator_name({}) == ""

    def test_vendor_fallback_to_first(self):
        reqs = [
            {"accepted": None, "rejected": None, "canceled": None,
             "vendor": {"name": "Fallback Co"}},
        ]
        assert _extract_vendor_name(reqs) == "Fallback Co"

    def test_vendor_prefers_accepted(self):
        reqs = [
            {"accepted": None, "rejected": None, "canceled": None,
             "vendor": {"name": "Unaccepted Co"}},
            {"accepted": "2026-01-01", "rejected": None, "canceled": None,
             "vendor": {"name": "Accepted Co"}},
        ]
        assert _extract_vendor_name(reqs) == "Accepted Co"

    def test_scheduled_date_picks_earliest(self):
        vendor_appts = [
            {"availability_segment": {"event": {"dtstart": "2026-06-10"}}},
            {"availability_segment": {"event": {"dtstart": "2026-06-05"}}},
        ]
        assert _extract_scheduled_date(vendor_appts, []) == date(2026, 6, 5)


class TestOwnerApprovalNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, "Not Requested"),
            ("", "Not Requested"),
            ("OWNER_APPROVAL_NOT_REQUESTED", "Not Requested"),
            ("OWNER_APPROVAL_REQUESTED", "Requested"),
            ("OWNER_APPROVAL_APPROVED", "Approved"),
            ("OWNER_APPROVAL_NOT_APPROVED", "Not Approved"),
            ("something_unknown", "Not Requested"),
        ],
    )
    def test_normalisation(self, raw, expected):
        assert _normalise_owner_approval(raw) == expected

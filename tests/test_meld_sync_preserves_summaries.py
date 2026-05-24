"""
Regression test: daily meld sync must NOT overwrite staff-edited summaries.

The map_meld() function returns a defaults dict that is passed to
Meld.objects.update_or_create(). The fields ai_summary, staff_summary,
and summary_status must NEVER appear in that dict, so they survive re-sync.
"""

from unittest.mock import patch

from django.test import TestCase

from integrations.property_meld.mappers import map_meld
from integrations.property_meld.services import MeldSyncService
from maintenance.models import Meld


# Minimal valid API record (mirrors real PM response shape)
SAMPLE_MELD_API_RECORD = {
    "id": 99001,
    "brief_description": "Leaky faucet in kitchen",
    "work_category": "PLUMBING",
    "priority": "NORMAL",
    "status": "PENDING_COMPLETION",
    "coordinator": None,
    "vendor_assignment_requests": [],
    "prop_address": {"full_address": "123 Test St, Asheville, NC 28801"},
    "unit_address": None,
    "prop": 500,
    "unit": 600,
    "tenant_presence_required": False,
    "vendorappointment": [],
    "managementappointment": [],
    "marked_complete": None,
    "completion_date": None,
    "started": None,
    "due_date": None,
    "owner_approval_status": None,
    "work_entries": [],
    "reference_id": "M-1234",
    "description": "Kitchen sink dripping constantly",
    "work_type": "Reactive",
    "work_location": "Kitchen",
    "origin": "TENANT",
    "is_active": True,
    "maintenance_notes": "",
    "completion_notes": "",
    "reason_cannot_complete": None,
    "resolution_type": None,
    "parent": None,
    "recurring_meld": None,
    "merged_meld": None,
    "tenant_rating": None,
    "created": "2026-05-10T12:00:00Z",
    "updated": "2026-05-20T15:00:00Z",
}


class MapMeldExcludesSummaryFieldsTest(TestCase):
    """map_meld() must never include summary fields in its defaults dict."""

    def test_summary_fields_not_in_defaults(self):
        """ai_summary, staff_summary, summary_status must be absent from defaults."""
        _pm_id, defaults = map_meld(SAMPLE_MELD_API_RECORD)

        self.assertNotIn("ai_summary", defaults)
        self.assertNotIn("staff_summary", defaults)
        self.assertNotIn("summary_status", defaults)


class MeldSyncPreservesSummariesTest(TestCase):
    """Full integration test: sync overwrites API fields but not summaries."""

    def test_edited_summary_survives_resync(self):
        """A staff-edited summary must survive a full meld re-sync."""
        # Step 1: Create a meld via initial sync
        pm_id, defaults = map_meld(SAMPLE_MELD_API_RECORD)
        meld, _ = Meld.objects.update_or_create(
            property_meld_id=pm_id, defaults=defaults
        )

        # Step 2: Staff edits the summary (simulates dashboard save)
        meld.ai_summary = "AI generated: Leaky faucet being fixed"
        meld.staff_summary = "Plumber scheduled for Thursday, ~$200 estimate"
        meld.summary_status = "edited"
        meld.save(update_fields=["ai_summary", "staff_summary", "summary_status"])

        # Step 3: Re-sync with updated API data (description changed)
        updated_record = {
            **SAMPLE_MELD_API_RECORD,
            "brief_description": "Leaky faucet in kitchen - URGENT",
            "status": "PENDING_COMPLETION",
            "updated": "2026-05-21T10:00:00Z",
        }

        # Run the sync service with mocked API response
        service = MeldSyncService()
        with patch.object(service.client, "get_all", return_value=[updated_record]):
            result = service.sync()

        self.assertEqual(result["updated"], 1)

        # Step 4: Verify summary fields are UNCHANGED
        meld.refresh_from_db()

        self.assertEqual(meld.ai_summary, "AI generated: Leaky faucet being fixed")
        self.assertEqual(
            meld.staff_summary, "Plumber scheduled for Thursday, ~$200 estimate"
        )
        self.assertEqual(meld.summary_status, "edited")

        # And the API field WAS updated
        self.assertEqual(meld.brief_description, "Leaky faucet in kitchen - URGENT")

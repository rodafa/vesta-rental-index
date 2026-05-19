from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from leasing.services.unit_matching_audit import post_audit_summary, run_audit
from properties.models import Property, Unit


def _make_re_record(re_id, address_line_1="", city="", state="", postal_code="",
                    unit_number="", street_number=""):
    """Build a fake RE API record that map_re_unit can parse."""
    return {
        "unitID": re_id,
        "propertyID": 1,
        "address": address_line_1,
        "address2": unit_number,
        "city": city,
        "stateCode": state,
        "zip": postal_code,
        "unitName": "",
        "bedrooms": 2,
        "bathrooms": 1,
        "sqft": 900,
        "marketRent": "1200.00",
        "dateAvailable": None,
        "status": "Occupied",
        "isDeactivated": False,
    }


def _make_map_re_unit_side_effect(records):
    """
    Build a side_effect function for map_re_unit that returns
    (re_id, defaults) from our fake records.
    """
    lookup = {}
    for r in records:
        re_id = r["unitID"]
        # Build defaults dict matching what the real mapper would return
        lookup[id(r)] = (re_id, {
            "address_line_1": r.get("address", ""),
            "city": r.get("city", ""),
            "state": r.get("stateCode", ""),
            "postal_code": r.get("zip", ""),
            "unit_number": r.get("address2", ""),
            "street_number": r.get("address", "").split(" ")[0] if r.get("address") else "",
        })

    def side_effect(record):
        return lookup[id(record)]

    return side_effect


class RunAuditTests(TestCase):
    """Tests for the run_audit() service function.

    Fixtures match actual prod data shape: Units have address_line_1 but
    NO city/state/postal (RentVine's unit endpoint doesn't return those).
    City/state/postal live on Property only.
    """

    def setUp(self):
        self.prop = Property.objects.create(
            name="Test Property",
            address_line_1="100 Main St",
            city="Nashville",
            state="TN",
            postal_code="37201",
        )

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_returns_correct_shape(self, MockClient, mock_mapper):
        """Result dict contains all expected keys."""
        Unit.objects.create(
            property=self.prop, rentengine_id=1,
            address_line_1="100 Main St",
        )
        Unit.objects.create(
            property=self.prop, rentengine_id=2,
            address_line_1="200 Oak Ave",
        )

        re_records = [
            _make_re_record(1, "100 Main St", "Nashville", "TN", "37201"),
            _make_re_record(2, "200 Oak Ave", "Nashville", "TN", "37201"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertIn("rentengine_unit_count", result)
        self.assertIn("local_linked_count", result)
        self.assertIn("anomalies", result)
        self.assertIn("totals", result)
        self.assertEqual(result["rentengine_unit_count"], 2)
        self.assertEqual(result["local_linked_count"], 2)
        for key in ("stale_link", "unlinked_on_our_side", "address_drift",
                     "multi_unit_ambiguity", "missing_rentengine_address"):
            self.assertIn(key, result["anomalies"])
            self.assertIn(key, result["totals"])

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_detects_stale_link(self, MockClient, mock_mapper):
        """Local Unit pointing to RE id that doesn't exist in RE inventory."""
        Unit.objects.create(
            property=self.prop, rentengine_id=999,
            address_line_1="100 Main St",
        )

        re_records = [
            _make_re_record(1, "100 Main St", "Nashville", "TN", "37201"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertEqual(result["totals"]["stale_link"], 1)
        self.assertEqual(
            result["anomalies"]["stale_link"][0]["rentengine_unit_id"], 999
        )

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_detects_unlinked(self, MockClient, mock_mapper):
        """RE unit exists but no local Unit is linked to it."""
        Unit.objects.create(
            property=self.prop, rentengine_id=1,
            address_line_1="100 Main St",
        )

        re_records = [
            _make_re_record(1, "100 Main St", "Nashville", "TN", "37201"),
            _make_re_record(50, "999 Elm St", "Nashville", "TN", "37201"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertEqual(result["totals"]["unlinked_on_our_side"], 1)
        self.assertEqual(
            result["anomalies"]["unlinked_on_our_side"][0]["rentengine_unit_id"],
            50,
        )

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_detects_address_drift(self, MockClient, mock_mapper):
        """Linked pair whose street addresses genuinely disagree."""
        Unit.objects.create(
            property=self.prop, rentengine_id=1,
            address_line_1="100 Main St",
        )

        # RE has a completely different street address
        re_records = [
            _make_re_record(1, "200 Oak Ave", "Nashville", "TN", "37201"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertEqual(result["totals"]["address_drift"], 1)
        self.assertEqual(
            result["anomalies"]["address_drift"][0]["rentengine_unit_id"], 1
        )

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_no_drift_for_suffix_variation(self, MockClient, mock_mapper):
        """Suffix abbreviation difference (Road vs Rd) is NOT flagged as drift."""
        Unit.objects.create(
            property=self.prop, rentengine_id=1,
            address_line_1="130 Reems Creek Road",
            address_line_2="2",
        )

        # RE has abbreviated suffix + unit indicator + geo tail
        re_records = [
            _make_re_record(1, "130 Reems Creek Rd", "Weaverville", "NC", "28787",
                            unit_number="2"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertEqual(result["totals"]["address_drift"], 0,
                         "Suffix variation should not be flagged as drift")

    @patch("leasing.services.unit_matching_audit.map_re_unit")
    @patch("leasing.services.unit_matching_audit.RentEngineClient")
    def test_run_audit_no_drift_when_local_has_unit_re_does_not(self, MockClient, mock_mapper):
        """Local has unit designator, RE doesn't — base streets match, no drift."""
        Unit.objects.create(
            property=self.prop, rentengine_id=1,
            address_line_1="68 Greeley Street",
            address_line_2="Unit A - Upstairs Unit",
        )

        # RE has no unit number — just the street address
        re_records = [
            _make_re_record(1, "68 Greeley St", "Asheville", "NC", "28806"),
        ]
        MockClient.return_value.get_all.return_value = re_records
        mock_mapper.side_effect = _make_map_re_unit_side_effect(re_records)

        result = run_audit()

        self.assertEqual(result["totals"]["address_drift"], 0,
                         "Unit mismatch with matching base street should not be drift")


class PostAuditSummaryTests(TestCase):
    """Tests for the post_audit_summary() Slack posting function."""

    def _make_result(self, anomalies=None):
        """Build a minimal audit result dict."""
        if anomalies is None:
            anomalies = {
                "stale_link": [],
                "unlinked_on_our_side": [],
                "address_drift": [],
                "multi_unit_ambiguity": [],
                "missing_rentengine_address": [],
            }
        totals = {k: len(v) for k, v in anomalies.items()}
        return {
            "rentengine_unit_count": 100,
            "local_linked_count": 90,
            "anomalies": anomalies,
            "totals": totals,
        }

    @override_settings(SLACK_LEASING_WEBHOOK_URL="https://hooks.slack.com/test")
    @patch("leasing.services.unit_matching_audit.requests.post")
    def test_post_audit_summary_suppresses_zero_anomalies(self, mock_post):
        """Anomaly categories with 0 entries are not mentioned in Slack text."""
        anomalies = {
            "stale_link": [{"anomaly_class": "stale_link", "rentengine_unit_id": 1,
                            "rentengine_address": "", "local_unit_id": 1,
                            "local_unit_address": "100 Main St",
                            "suggested_local_unit_id": "",
                            "suggested_local_unit_address": "", "notes": "test"}],
            "unlinked_on_our_side": [],
            "address_drift": [],
            "multi_unit_ambiguity": [],
            "missing_rentengine_address": [],
        }
        result = self._make_result(anomalies)

        post_audit_summary(result)

        posted_text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("Stale link", posted_text)
        self.assertNotIn("Unlinked on our side", posted_text)
        self.assertNotIn("Address drift", posted_text)

    @override_settings(SLACK_LEASING_WEBHOOK_URL="https://hooks.slack.com/test")
    @patch("leasing.services.unit_matching_audit.requests.post")
    def test_post_audit_summary_clean_state_message(self, mock_post):
        """All anomaly lists empty → 'clean' message."""
        result = self._make_result()

        post_audit_summary(result)

        posted_text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("clean", posted_text)

    @override_settings(SLACK_LEASING_WEBHOOK_URL="https://hooks.slack.com/test")
    @patch("leasing.services.unit_matching_audit.requests.post")
    def test_post_audit_summary_truncates_long_lists(self, mock_post):
        """More than 5 unlinked with suggestions → 'showing first 5' text."""
        unlinked = []
        for i in range(10):
            unlinked.append({
                "anomaly_class": "unlinked_on_our_side",
                "rentengine_unit_id": 100 + i,
                "rentengine_address": f"{i} Test St, Nashville, TN, 37201",
                "local_unit_id": "",
                "local_unit_address": "",
                "suggested_local_unit_id": 200 + i,
                "suggested_local_unit_address": f"{i} Test St",
                "notes": "Candidate found",
            })
        anomalies = {
            "stale_link": [],
            "unlinked_on_our_side": unlinked,
            "address_drift": [],
            "multi_unit_ambiguity": [],
            "missing_rentengine_address": [],
        }
        result = self._make_result(anomalies)

        post_audit_summary(result)

        posted_text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("showing first 5", posted_text)


    @override_settings(SLACK_LEASING_WEBHOOK_URL="https://hooks.slack.com/test")
    @patch("leasing.services.unit_matching_audit.requests.post")
    def test_post_audit_summary_include_samples_false_unchanged(self, mock_post):
        """include_samples=False produces no 'Sample' sections (regression)."""
        anomalies = {
            "stale_link": [],
            "unlinked_on_our_side": [],
            "address_drift": [
                {"anomaly_class": "address_drift", "rentengine_unit_id": 1,
                 "rentengine_address": "200 Oak Ave", "local_unit_id": 1,
                 "local_unit_address": "100 Main St",
                 "suggested_local_unit_id": "",
                 "suggested_local_unit_address": "", "notes": "drift"}
            ],
            "multi_unit_ambiguity": [],
            "missing_rentengine_address": [],
        }
        result = self._make_result(anomalies)

        post_audit_summary(result, include_samples=False)

        posted_text = mock_post.call_args[1]["json"]["text"]
        self.assertNotIn("Sample", posted_text)

    @override_settings(SLACK_LEASING_WEBHOOK_URL="https://hooks.slack.com/test")
    @patch("leasing.services.unit_matching_audit.requests.post")
    def test_post_audit_summary_sample_true_includes_drift_examples(self, mock_post):
        """include_samples=True appends sample drift section with truncation."""
        drift = []
        for i in range(10):
            drift.append({
                "anomaly_class": "address_drift",
                "rentengine_unit_id": 300 + i,
                "rentengine_address": f"{i} Oak Ave",
                "local_unit_id": 400 + i,
                "local_unit_address": f"{i} Oak Avenue",
                "suggested_local_unit_id": "",
                "suggested_local_unit_address": "",
                "notes": "drift",
            })
        anomalies = {
            "stale_link": [],
            "unlinked_on_our_side": [],
            "address_drift": drift,
            "multi_unit_ambiguity": [],
            "missing_rentengine_address": [],
        }
        result = self._make_result(anomalies)

        post_audit_summary(result, include_samples=True)

        posted_text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("Sample address drift", posted_text)
        self.assertIn("showing 5 of 10", posted_text)


@override_settings(VESTA_API_KEY="test-secret-key")
class AuditMatchingAPITests(TestCase):
    """Tests for the POST /leasing/directory/audit-matching endpoint."""

    def test_api_endpoint_requires_auth(self):
        """POST without X-API-Key header returns 401."""
        resp = self.client.post("/api/leasing/directory/audit-matching")
        self.assertEqual(resp.status_code, 401)

    @patch("leasing.api.threading.Thread")
    def test_api_endpoint_spawns_audit(self, MockThread):
        """POST with valid key starts a background thread."""
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread

        resp = self.client.post(
            "/api/leasing/directory/audit-matching",
            HTTP_X_API_KEY="test-secret-key",
        )

        self.assertEqual(resp.status_code, 200)
        MockThread.assert_called_once()
        # Verify the target function is _run_audit_matching
        call_kwargs = MockThread.call_args
        from leasing.api import _run_audit_matching
        self.assertEqual(call_kwargs[1]["target"], _run_audit_matching)
        mock_thread.start.assert_called_once()

    @patch("leasing.api.threading.Thread")
    def test_api_endpoint_sample_true_passes_include_samples(self, MockThread):
        """POST with ?sample=true passes include_samples=True to thread."""
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread

        resp = self.client.post(
            "/api/leasing/directory/audit-matching?sample=true",
            HTTP_X_API_KEY="test-secret-key",
        )

        self.assertEqual(resp.status_code, 200)
        call_kwargs = MockThread.call_args[1]
        self.assertEqual(call_kwargs["kwargs"], {"include_samples": True})

    @patch("leasing.api.threading.Thread")
    def test_api_endpoint_no_sample_param_defaults_false(self, MockThread):
        """POST without ?sample passes include_samples=False to thread."""
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread

        resp = self.client.post(
            "/api/leasing/directory/audit-matching",
            HTTP_X_API_KEY="test-secret-key",
        )

        self.assertEqual(resp.status_code, 200)
        call_kwargs = MockThread.call_args[1]
        self.assertEqual(call_kwargs["kwargs"], {"include_samples": False})

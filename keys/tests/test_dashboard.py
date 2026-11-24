"""Tests for the Key Health dashboard: views, access control, POST actions, filters."""

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import Property, Unit
from keys.models import AccessNote, Checkout, KeyChange, KeyHealthSnapshot, Tag


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


class _DashboardFixture(TestCase):
    """Property → Units → Tags → Notes for dashboard tests."""

    @classmethod
    def setUpTestData(cls):
        # Users
        cls.admin = User.objects.create_user(
            username="admin", password="pw", role="admin",
        )
        cls.leasing = User.objects.create_user(
            username="leasing", password="pw", role="leasing",
        )
        cls.maint = User.objects.create_user(
            username="maint", password="pw", role="maintenance",
            first_name="Zach", last_name="M",
        )

        # Property & units
        cls.prop = Property.objects.create(
            name="10 Elm St", address_line_1="10 Elm St",
            rentvine_id=9000,
        )
        cls.unit_vacant = Unit.objects.create(
            property=cls.prop, address_line_1="10 Elm St",
            unit_number="1", rentvine_id=9001,
            rentengine_status="Available",
        )
        cls.unit_leased = Unit.objects.create(
            property=cls.prop, address_line_1="10 Elm St",
            unit_number="2", rentvine_id=9002,
            rentengine_status="Leased",
        )
        cls.unit_unknown = Unit.objects.create(
            property=cls.prop, address_line_1="10 Elm St",
            unit_number="3", rentvine_id=9003,
            rentengine_status="",
        )

        # Tags
        cls.tag_on_hook = Tag.objects.create(
            box="A", number=1, unit=cls.unit_vacant,
            ring_notes="front door",
        )
        cls.tag_checked_out = Tag.objects.create(
            box="A", number=2, unit=cls.unit_leased,
            status="checked_out",
        )
        cls.tag_missing = Tag.objects.create(
            box="A", number=3, unit=cls.unit_unknown,
            status="missing",
        )

        # Access notes — source_ref empty to avoid PendingKeyChange lookup
        cls.note_visible = AccessNote.objects.create(
            unit=cls.unit_vacant,
            source="rentengine_key_access", source_ref="90001",
            text="lockbox 1234",
        )
        cls.note_hidden = AccessNote.objects.create(
            unit=cls.unit_vacant,
            source="rentengine_key_access", source_ref="90002",
            text="old code", hidden=True,
        )
        # Property-level note
        cls.note_prop = AccessNote.objects.create(
            property=cls.prop,
            source="rentvine_property_note", source_ref="90003",
            text="gate code 5555",
        )


# ---------------------------------------------------------------------------
# Access control: every URL
# ---------------------------------------------------------------------------


class DashboardAccessControlTests(_DashboardFixture):
    """Anonymous → redirect to login; logged-in without keys access → 403."""

    URL_NAMES_GET = ["key-dashboard", "key-search-units"]
    URL_NAMES_POST = [
        "key-set-code", "key-assign", "key-retire", "key-mark-missing",
        "key-found", "key-create-tag", "key-hide-note", "key-edit-ring-notes",
    ]

    def test_anonymous_get_redirects(self):
        for name in self.URL_NAMES_GET:
            url = reverse(name)
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, 302, f"GET {name} should redirect anon",
            )
            self.assertIn("/accounts/login/", resp.url)

    def test_anonymous_post_redirects(self):
        for name in self.URL_NAMES_POST:
            url = reverse(name)
            resp = self.client.post(url)
            self.assertEqual(
                resp.status_code, 302, f"POST {name} should redirect anon",
            )
            self.assertIn("/accounts/login/", resp.url)

    def test_no_keys_access_get_403(self):
        """Leasing role cannot access keys."""
        self.client.login(username="leasing", password="pw")
        for name in self.URL_NAMES_GET:
            url = reverse(name)
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, 403, f"GET {name} should 403 for leasing",
            )

    def test_no_keys_access_post_403(self):
        self.client.login(username="leasing", password="pw")
        for name in self.URL_NAMES_POST:
            url = reverse(name)
            resp = self.client.post(url)
            self.assertEqual(
                resp.status_code, 403, f"POST {name} should 403 for leasing",
            )

    def test_search_units_no_access_json_403(self):
        """AJAX endpoint returns JSON 403."""
        self.client.login(username="leasing", password="pw")
        resp = self.client.get(reverse("key-search-units"), {"q": "elm"})
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access(self):
        self.client.login(username="admin", password="pw")
        resp = self.client.get(reverse("key-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_maintenance_can_access(self):
        self.client.login(username="maint", password="pw")
        resp = self.client.get(reverse("key-dashboard"))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Page render, filters, search
# ---------------------------------------------------------------------------


class DashboardRenderTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_page_renders_200(self):
        resp = self.client.get(reverse("key-dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Key Health")
        self.assertContains(resp, "10 Elm St")

    def test_scorecard_present(self):
        resp = self.client.get(reverse("key-dashboard"))
        ctx = resp.context
        sc = ctx["scorecard"]
        self.assertIn("total_units", sc)
        self.assertIn("with_key", sc)
        self.assertIn("with_code", sc)
        self.assertIn("with_both", sc)
        self.assertGreaterEqual(sc["total_units"], 3)

    def test_filter_problems(self):
        resp = self.client.get(reverse("key-dashboard"), {"filter": "problems"})
        self.assertEqual(resp.status_code, 200)
        # Only rows with flags should appear
        for row in resp.context["page_obj"]:
            self.assertTrue(row["flags"], f"Row {row['address']} has no flags")

    def test_filter_vacant(self):
        resp = self.client.get(reverse("key-dashboard"), {"filter": "vacant"})
        self.assertEqual(resp.status_code, 200)
        for row in resp.context["page_obj"]:
            self.assertEqual(row["vacancy"], "Available")

    def test_filter_occupied(self):
        resp = self.client.get(reverse("key-dashboard"), {"filter": "occupied"})
        self.assertEqual(resp.status_code, 200)
        for row in resp.context["page_obj"]:
            self.assertEqual(row["vacancy"], "Leased")

    def test_filter_unknown(self):
        resp = self.client.get(reverse("key-dashboard"), {"filter": "unknown"})
        self.assertEqual(resp.status_code, 200)
        for row in resp.context["page_obj"]:
            self.assertFalse(row["vacancy"])

    def test_search_by_address(self):
        resp = self.client.get(reverse("key-dashboard"), {"q": "Elm"})
        self.assertEqual(resp.status_code, 200)
        for row in resp.context["page_obj"]:
            self.assertIn("elm", row["address"].lower())

    def test_search_by_key_id(self):
        resp = self.client.get(reverse("key-dashboard"), {"q": "A-001"})
        self.assertEqual(resp.status_code, 200)
        # At least one row should contain the tag
        rows = list(resp.context["page_obj"])
        self.assertTrue(len(rows) >= 1)

    def test_search_no_results(self):
        resp = self.client.get(reverse("key-dashboard"), {"q": "ZZZNOTEXIST"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(list(resp.context["page_obj"])), 0)

    def test_combined_filter_and_search(self):
        resp = self.client.get(
            reverse("key-dashboard"), {"filter": "vacant", "q": "Elm"},
        )
        self.assertEqual(resp.status_code, 200)
        for row in resp.context["page_obj"]:
            self.assertEqual(row["vacancy"], "Available")
            self.assertIn("elm", row["address"].lower())


# ---------------------------------------------------------------------------
# Hidden notes
# ---------------------------------------------------------------------------


class HiddenNoteTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_hidden_notes_excluded_by_default(self):
        resp = self.client.get(reverse("key-dashboard"))
        for row in resp.context["page_obj"]:
            for note in row["notes"]:
                self.assertFalse(note["hidden"], "Hidden note shown by default")

    def test_show_hidden_flag(self):
        resp = self.client.get(reverse("key-dashboard"), {"show_hidden": "1"})
        all_notes = []
        for row in resp.context["page_obj"]:
            all_notes.extend(row["notes"])
        hidden_pks = [n["pk"] for n in all_notes if n["hidden"]]
        self.assertIn(self.note_hidden.pk, hidden_pks)

    def test_property_level_hidden_note_counts_as_no_code(self):
        """A unit whose only code is a hidden PROPERTY-level note has no code."""
        # Create a unit with only a hidden property-level note
        prop2 = Property.objects.create(
            name="99 Test St", address_line_1="99 Test St", rentvine_id=9800,
        )
        unit = Unit.objects.create(
            property=prop2, address_line_1="99 Test St",
            rentvine_id=9801, rentengine_status="Available",
        )
        AccessNote.objects.create(
            property=prop2, source="keybot", source_ref="hid99",
            text="secret code", hidden=True,
        )

        from keys.selectors import key_health
        h = key_health()
        # unit should appear in vacant_no_code or vacant_no_key_no_code
        no_code_pks = {u["unit_id"] for u in h["vacant_no_code"]}
        both_pks = {u["unit_id"] for u in h["vacant_no_key_no_code"]}
        self.assertTrue(
            unit.pk in no_code_pks or unit.pk in both_pks,
            "Unit with only hidden property note should count as no code",
        )

    def test_loader_upsert_does_not_reset_hidden(self):
        """_upsert_access_note updates text but does not touch hidden field."""
        from keys.loader import _upsert_access_note

        note = AccessNote.objects.create(
            unit=self.unit_vacant,
            source="rentvine_unit_note",
            source_ref="loader_test_1",
            text="old text",
            hidden=True,
        )
        result = _upsert_access_note(
            unit=self.unit_vacant, prop=None,
            source="rentvine_unit_note", source_ref="loader_test_1",
            text="new text", source_date=None,
        )
        self.assertEqual(result, "updated")
        note.refresh_from_db()
        self.assertEqual(note.text, "new text")
        self.assertTrue(note.hidden, "Upsert must not reset hidden flag")


# ---------------------------------------------------------------------------
# POST actions
# ---------------------------------------------------------------------------


class ActionSetCodeTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="maint", password="pw")

    def test_set_code_creates_note_and_key_change(self):
        resp = self.client.post(reverse("key-set-code"), {
            "unit_id": self.unit_vacant.pk,
            "property_id": self.prop.pk,
            "code": "9876",
        })
        self.assertEqual(resp.status_code, 302)

        note = AccessNote.objects.filter(
            source="dashboard", text="9876",
        ).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.unit_id, self.unit_vacant.pk)

        kc = KeyChange.objects.filter(field="access_note").last()
        self.assertIsNotNone(kc)
        self.assertEqual(kc.new_value, str(note.pk))
        self.assertIn("dashboard:", kc.source_ref)

    def test_set_code_source_is_dashboard(self):
        self.client.post(reverse("key-set-code"), {
            "unit_id": self.unit_vacant.pk,
            "property_id": self.prop.pk,
            "code": "1111",
        })
        note = AccessNote.objects.filter(text="1111").first()
        self.assertEqual(note.source, "dashboard")

    def test_set_code_empty_refused(self):
        resp = self.client.post(reverse("key-set-code"), {
            "unit_id": self.unit_vacant.pk,
            "property_id": self.prop.pk,
            "code": "",
        })
        self.assertEqual(resp.status_code, 302)
        # No note created
        self.assertFalse(AccessNote.objects.filter(source="dashboard", text="").exists())


class ActionAssignTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")
        self.orphan = Tag.objects.create(box="B", number=99)

    def test_assign_tag_to_unit(self):
        resp = self.client.post(reverse("key-assign"), {
            "tag_id": self.orphan.pk,
            "unit_id": self.unit_vacant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.unit_id, self.unit_vacant.pk)

        kc = KeyChange.objects.filter(tag=self.orphan, field="unit").first()
        self.assertIsNotNone(kc)
        self.assertEqual(kc.new_value, str(self.unit_vacant.pk))

    def test_assign_key_change_has_dashboard_user(self):
        self.client.post(reverse("key-assign"), {
            "tag_id": self.orphan.pk,
            "unit_id": self.unit_vacant.pk,
        })
        kc = KeyChange.objects.filter(tag=self.orphan, field="unit").first()
        self.assertIn("dashboard:", kc.source_ref)
        # changed_by is actor_name from request.user
        self.assertTrue(kc.changed_by)


class ActionRetireTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_retire_on_hook_tag(self):
        resp = self.client.post(reverse("key-retire"), {
            "tag_id": self.tag_on_hook.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_on_hook.refresh_from_db()
        self.assertEqual(self.tag_on_hook.status, "retired")

        kc = KeyChange.objects.filter(tag=self.tag_on_hook, field="status").first()
        self.assertEqual(kc.new_value, "retired")

    def test_retire_checked_out_tag_refused(self):
        resp = self.client.post(reverse("key-retire"), {
            "tag_id": self.tag_checked_out.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_checked_out.refresh_from_db()
        self.assertEqual(self.tag_checked_out.status, "checked_out")


class ActionMarkMissingTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_mark_missing(self):
        resp = self.client.post(reverse("key-mark-missing"), {
            "tag_id": self.tag_on_hook.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_on_hook.refresh_from_db()
        self.assertEqual(self.tag_on_hook.status, "missing")

        kc = KeyChange.objects.filter(tag=self.tag_on_hook, field="status").first()
        self.assertEqual(kc.new_value, "missing")


class ActionFoundTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_found_missing_tag(self):
        resp = self.client.post(reverse("key-found"), {
            "tag_id": self.tag_missing.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_missing.refresh_from_db()
        self.assertEqual(self.tag_missing.status, "on_hook")

    def test_found_non_missing_refused(self):
        resp = self.client.post(reverse("key-found"), {
            "tag_id": self.tag_on_hook.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_on_hook.refresh_from_db()
        self.assertEqual(self.tag_on_hook.status, "on_hook")


class ActionCreateTagTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_create_tag(self):
        resp = self.client.post(reverse("key-create-tag"), {
            "box": "B",
            "number": "150",
            "unit_id": self.unit_vacant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        tag = Tag.objects.get(key_id="B-150")
        self.assertEqual(tag.unit_id, self.unit_vacant.pk)

        kc = KeyChange.objects.filter(tag=tag, field="created").first()
        self.assertIsNotNone(kc)
        self.assertIn("dashboard:", kc.source_ref)

    def test_create_tag_refuses_existing_key_id(self):
        """create_tag refuses when key_id already exists."""
        resp = self.client.post(reverse("key-create-tag"), {
            "box": "A",
            "number": "1",  # A-001 already exists
        })
        self.assertEqual(resp.status_code, 302)
        # Only the original tag, no duplicate
        self.assertEqual(Tag.objects.filter(key_id="A-001").count(), 1)

    def test_create_tag_no_unit_or_property(self):
        """Create orphan tag (no unit_id or property_id)."""
        resp = self.client.post(reverse("key-create-tag"), {
            "box": "B",
            "number": "180",
        })
        self.assertEqual(resp.status_code, 302)
        tag = Tag.objects.get(key_id="B-180")
        self.assertIsNone(tag.unit_id)
        self.assertIsNone(tag.property_id)

    def test_create_tag_bad_box(self):
        resp = self.client.post(reverse("key-create-tag"), {
            "box": "C", "number": "10",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Tag.objects.filter(box="C").exists())

    def test_create_tag_number_out_of_range(self):
        resp = self.client.post(reverse("key-create-tag"), {
            "box": "A", "number": "250",
        })
        self.assertEqual(resp.status_code, 302)
        # Not created
        self.assertFalse(Tag.objects.filter(key_id="A-250").exists())


class ActionHideNoteTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_hide_note(self):
        resp = self.client.post(reverse("key-hide-note"), {
            "note_id": self.note_visible.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.note_visible.refresh_from_db()
        self.assertTrue(self.note_visible.hidden)

        kc = KeyChange.objects.filter(field="access_note_hidden").last()
        self.assertIsNotNone(kc)

    def test_hide_already_hidden_refused(self):
        resp = self.client.post(reverse("key-hide-note"), {
            "note_id": self.note_hidden.pk,
        })
        self.assertEqual(resp.status_code, 302)
        # Still hidden, but no double-hide


class ActionEditRingNotesTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_edit_ring_notes(self):
        resp = self.client.post(reverse("key-edit-ring-notes"), {
            "tag_id": self.tag_on_hook.pk,
            "ring_notes": "front + back",
        })
        self.assertEqual(resp.status_code, 302)
        self.tag_on_hook.refresh_from_db()
        self.assertEqual(self.tag_on_hook.ring_notes, "front + back")

        kc = KeyChange.objects.filter(tag=self.tag_on_hook, field="ring_notes").first()
        self.assertIsNotNone(kc)
        self.assertEqual(kc.old_value, "front door")
        self.assertEqual(kc.new_value, "front + back")


# ---------------------------------------------------------------------------
# Slack confirm path still works through the refactored services
# ---------------------------------------------------------------------------


class SlackConfirmPathTests(_DashboardFixture):
    """apply_* wrappers still work after the do_* refactor."""

    def test_apply_retire_via_pending(self):
        from keys.models import PendingKeyChange
        from keys.updates import apply_retire

        tag = Tag.objects.create(box="B", number=70, unit=self.unit_vacant)
        pending = PendingKeyChange.objects.create(
            requester_slack_id="U_ED", requester_name="Ed",
            action="retire", payload={"tag_id": tag.pk},
        )
        result = apply_retire(pending)
        self.assertIsNone(result)
        tag.refresh_from_db()
        self.assertEqual(tag.status, "retired")

    def test_apply_set_code_via_pending(self):
        from keys.models import PendingKeyChange
        from keys.updates import apply_set_code

        pending = PendingKeyChange.objects.create(
            requester_slack_id="U_ED", requester_name="Ed",
            action="set_code", payload={
                "unit_id": self.unit_vacant.pk,
                "property_id": self.prop.pk,
                "code": "4321",
            },
        )
        result = apply_set_code(pending)
        self.assertIsNone(result)
        note = AccessNote.objects.filter(
            source="keybot", source_ref=str(pending.pk),
        ).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.text, "4321")


# ---------------------------------------------------------------------------
# unit_label dedup
# ---------------------------------------------------------------------------


class UnitLabelDedupTests(TestCase):
    """unit_label suppresses redundant 'La Salle Lane' vs 'La Salle Ln'."""

    def test_la_salle_dedup(self):
        from keys.services import unit_label

        result = unit_label("12 La Salle Lane", "12 La Salle Lane", "12 La Salle Ln")
        self.assertEqual(result, "12 La Salle Lane")

    def test_no_dedup_different_number(self):
        from keys.services import unit_label

        result = unit_label("12 La Salle Lane", "12 La Salle Lane", "14 La Salle Ln")
        # Different street number — should NOT dedup
        self.assertIn("14", result)

    def test_no_dedup_actual_unit_number(self):
        from keys.services import unit_label

        result = unit_label("12 La Salle Lane", "12 La Salle Lane", "2")
        self.assertEqual(result, "12 La Salle Lane - Unit 2")

    def test_key_health_uses_dedup(self):
        """key_health output uses unit_label for addresses."""
        prop = Property.objects.create(
            name="12 La Salle Lane", address_line_1="12 La Salle Lane",
            rentvine_id=8000,
        )
        unit = Unit.objects.create(
            property=prop, address_line_1="12 La Salle Lane",
            unit_number="12 La Salle Ln", rentvine_id=8001,
            rentengine_status="Available",
        )
        from keys.selectors import key_health

        h = key_health()
        # Find this unit in the results
        for category in ["vacant_no_key_no_code", "vacant_no_key", "vacant_no_code"]:
            for item in h[category]:
                if item["unit_id"] == unit.pk:
                    # Should NOT contain the redundant "La Salle Ln" suffix
                    self.assertNotIn("La Salle Ln", item["address"])
                    return
        # Unit should appear in at least one vacant category (no key, no code)
        both_pks = {u["unit_id"] for u in h["vacant_no_key_no_code"]}
        self.assertIn(unit.pk, both_pks)


# ---------------------------------------------------------------------------
# Snapshot counts
# ---------------------------------------------------------------------------


class SnapshotCountsTests(_DashboardFixture):

    def test_snapshot_counts_fields(self):
        from keys.selectors import key_health

        h = key_health()
        self.assertIn("total_units", h)
        self.assertIn("with_key_count", h)
        self.assertIn("with_code_count", h)
        self.assertIn("with_both_count", h)
        self.assertGreaterEqual(h["total_units"], 3)
        self.assertGreaterEqual(h["with_key_count"], 0)
        self.assertGreaterEqual(h["with_code_count"], 0)
        self.assertGreaterEqual(h["with_both_count"], 0)


# ---------------------------------------------------------------------------
# AJAX search endpoint
# ---------------------------------------------------------------------------


class SearchUnitsTests(_DashboardFixture):

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def test_search_returns_json(self):
        resp = self.client.get(reverse("key-search-units"), {"q": "Elm"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertTrue(len(data["results"]) >= 1)

    def test_search_short_query(self):
        resp = self.client.get(reverse("key-search-units"), {"q": "E"})
        data = resp.json()
        self.assertEqual(data["results"], [])

    def test_search_result_has_unit_id_and_address(self):
        resp = self.client.get(reverse("key-search-units"), {"q": "Elm"})
        data = resp.json()
        for item in data["results"]:
            self.assertIn("unit_id", item)
            self.assertIn("address", item)

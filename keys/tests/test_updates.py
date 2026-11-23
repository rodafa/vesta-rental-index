"""Tests for KeyBot update commands: parsing, service layer, and interactions."""

import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Property, Unit
from keys.models import AccessNote, Checkout, KeyChange, PendingKeyChange, Tag
from keys.selectors import parse_query
from keys.updates import (
    apply_assign,
    apply_found,
    apply_mark_missing,
    apply_retire,
    apply_set_code,
)


# ── Parse query tests ──────────────────────────────────────────────


class ParseQueryUpdateTests(TestCase):
    """parse_query recognizes update commands."""

    def test_set_code(self):
        result = parse_query("set code 28 Bass Lane to 1234")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["address"], "28 Bass Lane")
        self.assertEqual(result["value"]["code"], "1234")

    def test_set_lockbox(self):
        result = parse_query("set lockbox 28 Bass Lane to 5678")
        self.assertEqual(result["type"], "set_code")
        self.assertEqual(result["value"]["code"], "5678")

    def test_assign(self):
        result = parse_query("assign A-038 to 28 Bass Lane")
        self.assertEqual(result["type"], "assign")
        self.assertEqual(result["value"]["key_id"], "A-038")
        self.assertEqual(result["value"]["address"], "28 Bass Lane")

    def test_assign_loose_key_id(self):
        result = parse_query("assign a38 to 28 Bass Lane")
        self.assertEqual(result["type"], "assign")
        self.assertEqual(result["value"]["key_id"], "A-038")

    def test_retire(self):
        result = parse_query("retire A-038")
        self.assertEqual(result["type"], "retire")
        self.assertEqual(result["value"]["key_id"], "A-038")

    def test_mark_missing(self):
        result = parse_query("missing A-038")
        self.assertEqual(result["type"], "mark_missing")
        self.assertEqual(result["value"]["key_id"], "A-038")

    def test_bare_missing_is_key_health(self):
        """'missing' alone → key_health, not mark_missing."""
        result = parse_query("missing")
        self.assertEqual(result["type"], "key_health")

    def test_found(self):
        result = parse_query("found A-038")
        self.assertEqual(result["type"], "found")
        self.assertEqual(result["value"]["key_id"], "A-038")


# ── Shared fixture ─────────────────────────────────────────────────


class _UpdateFixture(TestCase):
    """Property → Unit → Tag (on_hook) for update tests."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="10 Elm Street",
            address_line_1="10 Elm Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9000,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="10 Elm Street",
            unit_number="1",
            rentvine_id=9001,
        )
        cls.tag = Tag.objects.create(
            box="A", number=50, unit=cls.unit, ring_notes="front door",
        )

    def _make_pending(self, action, payload, **kwargs):
        defaults = {
            "requester_slack_id": "U_EDITOR",
            "requester_name": "Editor",
            "action": action,
            "payload": payload,
        }
        defaults.update(kwargs)
        return PendingKeyChange.objects.create(**defaults)


# ── apply_set_code ─────────────────────────────────────────────────


class ApplySetCodeTests(_UpdateFixture):

    def test_unit_level_code(self):
        pending = self._make_pending("set_code", {
            "unit_id": self.unit.pk,
            "property_id": self.prop.pk,
            "code": "9999",
        })
        result = apply_set_code(pending)
        self.assertIsNone(result)

        note = AccessNote.objects.get(source="keybot", source_ref=str(pending.pk))
        self.assertEqual(note.text, "9999")
        self.assertEqual(note.unit_id, self.unit.pk)
        self.assertIsNotNone(note.source_date)

        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertEqual(kc.field, "access_note")
        self.assertEqual(kc.new_value, str(note.pk))

    def test_property_level_code(self):
        pending = self._make_pending("set_code", {
            "unit_id": None,
            "property_id": self.prop.pk,
            "code": "1234",
        })
        result = apply_set_code(pending)
        self.assertIsNone(result)

        note = AccessNote.objects.get(source="keybot", source_ref=str(pending.pk))
        self.assertEqual(note.text, "1234")
        self.assertIsNone(note.unit_id)
        self.assertEqual(note.property_id, self.prop.pk)


# ── apply_assign ───────────────────────────────────────────────────


class ApplyAssignTests(_UpdateFixture):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.unit2 = Unit.objects.create(
            property=cls.prop,
            address_line_1="10 Elm Street",
            unit_number="2",
            rentvine_id=9002,
        )

    def test_assign_success(self):
        pending = self._make_pending("assign", {
            "tag_id": self.tag.pk,
            "unit_id": self.unit2.pk,
        })
        result = apply_assign(pending)
        self.assertIsNone(result)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.unit_id, self.unit2.pk)
        self.assertIsNone(self.tag.property_id)

        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertEqual(kc.field, "unit")
        self.assertEqual(kc.new_value, str(self.unit2.pk))

    def test_assign_refuses_retired(self):
        self.tag.status = "retired"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("assign", {
            "tag_id": self.tag.pk,
            "unit_id": self.unit2.pk,
        })
        result = apply_assign(pending)
        self.assertIsNotNone(result)
        self.assertIn("retired", result)

        # Tag unchanged.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.unit_id, self.unit.pk)


# ── apply_retire ───────────────────────────────────────────────────


class ApplyRetireTests(_UpdateFixture):

    def test_retire_success(self):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})
        result = apply_retire(pending)
        self.assertIsNone(result)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "retired")

        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertEqual(kc.field, "status")
        self.assertEqual(kc.old_value, "on_hook")
        self.assertEqual(kc.new_value, "retired")

    def test_retire_refuses_checked_out(self):
        self.tag.status = "checked_out"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("retire", {"tag_id": self.tag.pk})
        result = apply_retire(pending)
        self.assertIsNotNone(result)
        self.assertIn("checked out", result)

    def test_retire_refuses_already_retired(self):
        self.tag.status = "retired"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("retire", {"tag_id": self.tag.pk})
        result = apply_retire(pending)
        self.assertIsNotNone(result)
        self.assertIn("already retired", result)


# ── apply_mark_missing ─────────────────────────────────────────────


class ApplyMarkMissingTests(_UpdateFixture):

    def test_mark_missing_on_hook(self):
        pending = self._make_pending("mark_missing", {"tag_id": self.tag.pk})
        result = apply_mark_missing(pending)
        self.assertIsNone(result)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "missing")

        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertEqual(kc.old_value, "on_hook")
        self.assertEqual(kc.new_value, "missing")

    def test_mark_missing_closes_checkout(self):
        """Checked-out tag → missing closes the checkout directly."""
        from keys.checkouts import checkout_tag

        checkout_tag(self.tag, "U_ALICE", "Alice")

        pending = self._make_pending("mark_missing", {"tag_id": self.tag.pk})
        result = apply_mark_missing(pending)
        self.assertIsNone(result)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "missing")

        # Checkout was closed.
        co = Checkout.objects.filter(tag=self.tag).first()
        self.assertIsNotNone(co.returned_at)

        # KeyChange reason mentions the closed checkout.
        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertIn("closed open checkout", kc.reason)
        self.assertIn("Alice", kc.reason)

    def test_mark_missing_refuses_retired(self):
        self.tag.status = "retired"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("mark_missing", {"tag_id": self.tag.pk})
        result = apply_mark_missing(pending)
        self.assertIn("retired", result)

    def test_mark_missing_refuses_already_missing(self):
        self.tag.status = "missing"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("mark_missing", {"tag_id": self.tag.pk})
        result = apply_mark_missing(pending)
        self.assertIn("already marked missing", result)


# ── apply_found ────────────────────────────────────────────────────


class ApplyFoundTests(_UpdateFixture):

    def test_found_success(self):
        self.tag.status = "missing"
        self.tag.save(update_fields=["status"])

        pending = self._make_pending("found", {"tag_id": self.tag.pk})
        result = apply_found(pending)
        self.assertIsNone(result)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "on_hook")

        kc = KeyChange.objects.get(source_ref=str(pending.pk))
        self.assertEqual(kc.old_value, "missing")
        self.assertEqual(kc.new_value, "on_hook")

    def test_found_refuses_on_hook(self):
        pending = self._make_pending("found", {"tag_id": self.tag.pk})
        result = apply_found(pending)
        self.assertIsNotNone(result)
        self.assertIn("not marked missing", result)


# ── Interaction tests: confirm / cancel ────────────────────────────


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    KEYBOT_EDITORS=["U_EDITOR"],
    ROOT_URLCONF="config.urls",
)
class ConfirmCancelTests(_UpdateFixture):

    def _interaction_post(self, action_id, value, user_id="U_EDITOR"):
        payload = {
            "type": "block_actions",
            "user": {"id": user_id, "username": "editor", "name": "Editor"},
            "channel": {"id": "C_TEST"},
            "message": {"ts": "1234567890.123456"},
            "actions": [{"action_id": action_id, "value": value}],
        }
        return self.client.post(
            "/slack/keybot/interactions/",
            urlencode({"payload": json.dumps(payload)}),
            content_type="application/x-www-form-urlencoded",
        )

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_confirm_applies_retire(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})

        resp = self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )
        self.assertEqual(resp.status_code, 200)

        # Tag retired.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "retired")

        # Pending resolved.
        pending.refresh_from_db()
        self.assertEqual(pending.outcome, "confirmed")
        self.assertIsNotNone(pending.resolved_at)

        # Slack message updated to "Done", no ephemeral.
        mock_update.assert_called_once()
        self.assertIn("Done", mock_update.call_args[0][2])
        mock_eph.assert_not_called()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_cancel(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})

        resp = self._interaction_post(
            f"keybot_cancel:{pending.pk}", str(pending.pk),
        )
        self.assertEqual(resp.status_code, 200)

        # Tag unchanged.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "on_hook")

        # Pending cancelled.
        pending.refresh_from_db()
        self.assertEqual(pending.outcome, "cancelled")

        # Slack message updated to "Cancelled".
        mock_update.assert_called_once()
        self.assertIn("Cancelled", mock_update.call_args[0][2])

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_double_press_refused(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})

        # First confirm.
        self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )

        # Second confirm.
        mock_eph.reset_mock()
        mock_update.reset_mock()
        self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )

        # Ephemeral "Already confirmed".
        mock_eph.assert_called_once()
        self.assertIn("Already confirmed", mock_eph.call_args[0][2])

        # No second update_message.
        mock_update.assert_not_called()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_expired_confirm(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})
        # Backdate created_at past expiry.
        PendingKeyChange.objects.filter(pk=pending.pk).update(
            created_at=timezone.now() - timedelta(minutes=20),
        )

        resp = self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )
        self.assertEqual(resp.status_code, 200)

        # Tag unchanged.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "on_hook")

        # Pending marked expired.
        pending.refresh_from_db()
        self.assertEqual(pending.outcome, "expired")

        # Slack message updated to "Expired".
        mock_update.assert_called_once()
        self.assertIn("Expired", mock_update.call_args[0][2])

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_wrong_user_refused(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("retire", {"tag_id": self.tag.pk})

        resp = self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
            user_id="U_STRANGER",
        )
        self.assertEqual(resp.status_code, 200)

        # Tag unchanged.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "on_hook")

        # Ephemeral refusal.
        mock_eph.assert_called_once()
        self.assertIn("Only", mock_eph.call_args[0][2])

        # Pending still open.
        pending.refresh_from_db()
        self.assertIsNone(pending.resolved_at)

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_confirm_set_code(self, mock_eph, mock_update, mock_sig):
        pending = self._make_pending("set_code", {
            "unit_id": self.unit.pk,
            "property_id": self.prop.pk,
            "code": "5555",
        })

        resp = self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )
        self.assertEqual(resp.status_code, 200)

        # AccessNote created.
        note = AccessNote.objects.filter(
            source="keybot", source_ref=str(pending.pk),
        ).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.text, "5555")

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.update_message")
    @patch("keys.views.post_ephemeral")
    def test_slack_failure_does_not_rollback(self, mock_eph, mock_update, mock_sig):
        """update_message failure after confirm must NOT roll back the DB change."""
        mock_update.side_effect = RuntimeError("slack_down")

        pending = self._make_pending("retire", {"tag_id": self.tag.pk})

        resp = self._interaction_post(
            f"keybot_confirm:{pending.pk}", str(pending.pk),
        )
        self.assertEqual(resp.status_code, 200)

        # DB change committed despite Slack failure.
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.status, "retired")
        pending.refresh_from_db()
        self.assertEqual(pending.outcome, "confirmed")


# ── Permission tests ───────────────────────────────────────────────


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    KEYBOT_EDITORS=["U_EDITOR"],
    ROOT_URLCONF="config.urls",
)
class UpdatePermissionTests(_UpdateFixture):

    def _event_body(self, text, user_id="U_NON_EDITOR"):
        return json.dumps({
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C_TEST",
                "user": user_id,
                "text": f"<@UBOT> {text}",
                "ts": "111.222",
            },
        }).encode()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.post_ephemeral")
    @patch("keys.views.post_message")
    def test_non_editor_refused(self, mock_post, mock_eph, mock_sig):
        resp = self.client.post(
            "/slack/keybot/events/",
            self._event_body("retire A-050"),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # Ephemeral permission refusal.
        mock_eph.assert_called_once()
        self.assertIn("permission", mock_eph.call_args[0][2])

        # No PendingKeyChange created.
        self.assertEqual(PendingKeyChange.objects.count(), 0)

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.post_ephemeral")
    @patch("keys.views.post_message")
    @patch("keys.views.get_display_name", return_value="Editor")
    def test_editor_allowed(self, mock_name, mock_post, mock_eph, mock_sig):
        resp = self.client.post(
            "/slack/keybot/events/",
            self._event_body("retire A-050", user_id="U_EDITOR"),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # PendingKeyChange created (preview posted).
        self.assertEqual(PendingKeyChange.objects.count(), 1)
        pending = PendingKeyChange.objects.first()
        self.assertEqual(pending.action, "retire")

        # post_message called with confirm/cancel blocks.
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertIn("blocks", call_kwargs)

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.post_ephemeral")
    @patch("keys.views.post_message")
    def test_empty_editors_refuses_all(self, mock_post, mock_eph, mock_sig):
        with self.settings(KEYBOT_EDITORS=[]):
            resp = self.client.post(
                "/slack/keybot/events/",
                self._event_body("retire A-050", user_id="U_EDITOR"),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        mock_eph.assert_called_once()
        self.assertIn("permission", mock_eph.call_args[0][2])


# ── End-to-end event → handler tests ──────────────────────────────


@override_settings(
    KEYBOT_CHANNEL_ID="C_TEST",
    SLACK_SIGNING_SECRET="test-secret",
    KEYBOT_EDITORS=["U_EDITOR"],
    ROOT_URLCONF="config.urls",
)
class SetCodeEndToEndTests(TestCase):
    """Hit slack_events with a real address resolve — no mocked resolver."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="90 Moody Ave",
            address_line_1="90 Moody Ave",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=9200,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="90 Moody Ave",
            unit_number="2",
            rentvine_id=9201,
        )

    def _event_body(self, text, user_id="U_EDITOR"):
        return json.dumps({
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C_TEST",
                "user": user_id,
                "text": f"<@UBOT> {text}",
                "ts": "111.222",
            },
        }).encode()

    @patch("keys.views.verify_slack_signature", return_value=True)
    @patch("keys.views.get_display_name", return_value="Editor")
    @patch("keys.views.post_message")
    @patch("keys.views.post_ephemeral")
    def test_set_code_unit_level_creates_pending(
        self, mock_eph, mock_post, mock_name, mock_sig,
    ):
        """Unit-level set_code through slack_events — the path that was broken."""
        resp = self.client.post(
            "/slack/keybot/events/",
            self._event_body(
                "set code 90 moody ave unit 2 to 1776, lockbox on porch",
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # PendingKeyChange created with correct payload.
        self.assertEqual(PendingKeyChange.objects.count(), 1)
        pending = PendingKeyChange.objects.first()
        self.assertEqual(pending.action, "set_code")
        self.assertEqual(pending.payload["unit_id"], self.unit.pk)
        self.assertEqual(pending.payload["property_id"], self.prop.pk)
        self.assertEqual(pending.payload["code"], "1776, lockbox on porch")

        # Preview posted with confirm/cancel blocks.
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertIn("blocks", call_kwargs)
        self.assertIsNotNone(call_kwargs["blocks"])

        # No ephemeral error.
        mock_eph.assert_not_called()

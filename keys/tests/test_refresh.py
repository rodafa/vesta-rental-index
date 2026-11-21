"""Tests for the keys_refresh management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Property, Unit
from keys.models import AccessNote, KeyChange, Tag


class _RefreshFixture(TestCase):
    """Property with one unit, one tag, one key-change row."""

    @classmethod
    def setUpTestData(cls):
        cls.prop = Property.objects.create(
            name="50 Elm Street",
            address_line_1="50 Elm Street",
            city="Asheville",
            state="NC",
            postal_code="28801",
            rentvine_id=8000,
        )
        cls.unit = Unit.objects.create(
            property=cls.prop,
            address_line_1="50 Elm Street",
            unit_number="1",
            rentvine_id=8001,
        )
        cls.tag = Tag.objects.create(
            box="A", number=50, unit=cls.unit,
            ring_notes="front door",
        )
        cls.key_change = KeyChange.objects.create(
            tag=cls.tag,
            unit=cls.unit,
            field="created",
            old_value="",
            new_value="A-050 → 50 Elm Street 1",
            changed_by="import",
        )


def _noop_sync(*args, **kwargs):
    """Replacement for call_command that skips real syncs."""
    pass


# Patches applied to every test: suppress the three sync commands,
# stub the two fetch functions, and block Slack posts.
_COMMON_PATCHES = {
    "keys.management.commands.keys_refresh.call_command": _noop_sync,
    "keys.management.commands.keys_refresh.fetch_rentvine_key_notes":
        lambda db_units: ({}, {}, {}),
    "keys.management.commands.keys_refresh.fetch_rentengine_index":
        lambda db_units: ({}, {}),
    "keys.management.commands.keys_refresh.post_message": lambda *a, **k: None,
}


@override_settings(KEYBOT_CHANNEL_ID="C_TEST")
class RefreshSuccessTests(_RefreshFixture):

    def test_tags_and_keychanges_unchanged(self):
        """keys_refresh never creates, updates, or deletes Tag or KeyChange rows."""
        tag_count_before = Tag.objects.count()
        kc_count_before = KeyChange.objects.count()

        # Snapshot tag field values.
        tag_before = Tag.objects.values_list(
            "pk", "box", "number", "ring_notes", "unit_id",
        ).get(pk=self.tag.pk)

        with (
            patch(
                "keys.management.commands.keys_refresh.call_command",
                _noop_sync,
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentvine_key_notes",
                return_value=({}, {}, {}),
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentengine_index",
                return_value=({}, {}),
            ),
            patch(
                "keys.management.commands.keys_refresh.post_message",
            ) as mock_post,
        ):
            call_command("keys_refresh", stdout=StringIO())

        self.assertEqual(Tag.objects.count(), tag_count_before)
        self.assertEqual(KeyChange.objects.count(), kc_count_before)

        tag_after = Tag.objects.values_list(
            "pk", "box", "number", "ring_notes", "unit_id",
        ).get(pk=self.tag.pk)
        self.assertEqual(tag_before, tag_after)

        # Success path: no Slack post.
        mock_post.assert_not_called()

    def test_success_no_slack_post(self):
        """On success, post_message is never called."""
        with (
            patch(
                "keys.management.commands.keys_refresh.call_command",
                _noop_sync,
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentvine_key_notes",
                return_value=({}, {}, {}),
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentengine_index",
                return_value=({}, {}),
            ),
            patch(
                "keys.management.commands.keys_refresh.post_message",
            ) as mock_post,
        ):
            out = StringIO()
            call_command("keys_refresh", stdout=out)

        mock_post.assert_not_called()
        self.assertIn("Done:", out.getvalue())


@override_settings(KEYBOT_CHANNEL_ID="C_TEST")
class RefreshFetchErrorTests(_RefreshFixture):

    def test_fetch_error_no_notes_written_slack_alert(self):
        """Non-empty fetch_errors → no notes written, Slack alert, non-zero exit."""
        note_count_before = AccessNote.objects.count()

        with (
            patch(
                "keys.management.commands.keys_refresh.call_command",
                _noop_sync,
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentvine_key_notes",
                return_value=({}, {}, {8001: ["timeout on unit 8001"]}),
            ),
            patch(
                "keys.management.commands.keys_refresh.fetch_rentengine_index",
                return_value=({}, {}),
            ),
            patch(
                "keys.management.commands.keys_refresh.post_message",
            ) as mock_post,
            self.assertRaises(SystemExit) as ctx,
        ):
            call_command("keys_refresh", stdout=StringIO())

        # Non-zero exit.
        self.assertEqual(ctx.exception.code, 1)

        # No notes written.
        self.assertEqual(AccessNote.objects.count(), note_count_before)

        # Exactly one Slack alert.
        mock_post.assert_called_once()
        alert_text = mock_post.call_args[0][1]
        self.assertIn("refresh failed", alert_text)
        self.assertIn("Fetch errors on 1 unit(s)", alert_text)

    def test_sync_exception_no_notes_written_slack_alert(self):
        """Exception during sync → no notes written, Slack alert, non-zero exit."""
        note_count_before = AccessNote.objects.count()

        def _exploding_sync(cmd, *args, **kwargs):
            raise RuntimeError("API down")

        with (
            patch(
                "keys.management.commands.keys_refresh.call_command",
                _exploding_sync,
            ),
            patch(
                "keys.management.commands.keys_refresh.post_message",
            ) as mock_post,
            self.assertRaises(SystemExit) as ctx,
        ):
            call_command("keys_refresh", stdout=StringIO())

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(AccessNote.objects.count(), note_count_before)
        mock_post.assert_called_once()
        alert_text = mock_post.call_args[0][1]
        self.assertIn("refresh failed", alert_text)
        self.assertIn("API down", alert_text)

    def test_re_sync_abort_detected(self):
        """sync_rentengine_units writing 'aborting' to stderr → failure path."""
        note_count_before = AccessNote.objects.count()

        def _mock_sync(cmd, *args, **kwargs):
            stderr = kwargs.get("stderr")
            if stderr and "rentengine" in cmd:
                stderr.write("DUPLICATE MAPPING DETECTED — aborting.\n")

        with (
            patch(
                "keys.management.commands.keys_refresh.call_command",
                _mock_sync,
            ),
            patch(
                "keys.management.commands.keys_refresh.post_message",
            ) as mock_post,
            self.assertRaises(SystemExit) as ctx,
        ):
            call_command("keys_refresh", stdout=StringIO())

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(AccessNote.objects.count(), note_count_before)
        mock_post.assert_called_once()
        alert_text = mock_post.call_args[0][1]
        self.assertIn("aborting", alert_text.lower())

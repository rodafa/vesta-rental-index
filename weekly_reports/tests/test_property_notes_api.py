"""Tests for the weekly_reports property-notes API endpoints."""

from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase

from dashboard.models import PropertyWeeklyNote
from properties.models import Property, Unit


class DeleteNoteAPITest(TestCase):
    """Tests for DELETE /api/reports/weekly-update/property-notes/{note_id}."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="testpass"
        )
        prop = Property.objects.create(name="Test Property")
        self.unit = Unit.objects.create(property=prop, name="Unit A")
        self.note = PropertyWeeklyNote.objects.create(
            unit_id=self.unit.id,
            week_date=date(2026, 5, 12),
            author="AI",
            note_text="Test note for deletion.",
            approved=False,
        )
        self.client = Client()

    def test_delete_requires_auth(self):
        """Unauthenticated requests get 401/403."""
        resp = self.client.delete(
            f"/api/reports/weekly-update/property-notes/{self.note.id}"
        )
        self.assertIn(resp.status_code, (401, 403))
        self.assertTrue(PropertyWeeklyNote.objects.filter(id=self.note.id).exists())

    def test_delete_success(self):
        """Authenticated DELETE removes the note and returns 200."""
        self.client.force_login(self.user)
        resp = self.client.delete(
            f"/api/reports/weekly-update/property-notes/{self.note.id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PropertyWeeklyNote.objects.filter(id=self.note.id).exists())

    def test_delete_missing_note_returns_404(self):
        """DELETE on a non-existent note returns 404."""
        self.client.force_login(self.user)
        resp = self.client.delete(
            "/api/reports/weekly-update/property-notes/99999"
        )
        self.assertEqual(resp.status_code, 404)

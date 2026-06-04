"""
Tests for the monthly owner notes API endpoints (comms/api.py).

Covers:
- GET list notes for a month
- POST /generate spawns generation, returns {ok: true}
- POST /approve sets status=approved
- POST /approve-all bulk-approves and returns count
- PUT updates generated_note and re-renders body_html
- POST /send calls send_draft() and transitions status
- DELETE removes draft; 403 if already sent
- dry_run=True runs selector+AI but writes no rows
- Re-run guard: approved draft is not overwritten; draft-status draft is overwritten
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from accounts.models import User
from comms.models import EmailDraft
from comms.services import generate_drafts
from core.models import Owner, Portfolio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=50, name="Sunset Portfolio")


@pytest.fixture
def owner(portfolio):
    o = Owner.objects.create(
        rentvine_contact_id=50,
        name="Alice Smith",
        first_name="Alice",
        email="alice@example.com",
        is_active=True,
    )
    o.portfolios.add(portfolio)
    return o


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username="rodrigo_api", password="testpass", role=User.Role.ADMIN
    )


@pytest.fixture
def leasing_user():
    return User.objects.create_user(
        username="bill_api", password="testpass", role=User.Role.LEASING
    )


@pytest.fixture
def draft(owner):
    return EmailDraft.objects.create(
        product="monthly_owner_notes",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Your May 2026 Owner Update",
        body_html="<p>Hello Alice</p>",
        generated_note="This month we processed 1 lease renewal.",
        status="draft",
        period_type="monthly",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )


@pytest.fixture
def approved_draft(owner):
    return EmailDraft.objects.create(
        product="monthly_owner_notes",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Your May 2026 Owner Update",
        body_html="<p>Hello Alice</p>",
        generated_note="This month we processed 1 lease renewal.",
        status="approved",
        period_type="monthly",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )


@pytest.fixture
def sent_draft(owner):
    return EmailDraft.objects.create(
        product="monthly_owner_notes",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Your April 2026 Owner Update",
        body_html="<p>Hello Alice</p>",
        generated_note="April notes.",
        status="sent",
        period_type="monthly",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )


@pytest.fixture
def auth_client(admin_user):
    c = Client()
    c.login(username="rodrigo_api", password="testpass")
    return c


@pytest.fixture
def leasing_client(leasing_user):
    c = Client()
    c.login(username="bill_api", password="testpass")
    return c


# ---------------------------------------------------------------------------
# GET list notes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListNotes:
    def test_returns_notes_for_month(self, auth_client, draft):
        resp = auth_client.get("/api/reports/owner-notes?month=2026-05")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        note = data[0]
        assert note["id"] == draft.pk
        assert note["portfolio_name"] == "Sunset Portfolio"
        assert note["owner_name"] == "Alice Smith"
        assert note["status"] == "pending"  # draft -> pending mapping
        assert note["generated_note"] == "This month we processed 1 lease renewal."
        assert note["word_count"] == 7

    def test_empty_for_wrong_month(self, auth_client, draft):
        resp = auth_client.get("/api/reports/owner-notes?month=2026-06")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_unauthenticated_returns_401(self, draft):
        c = Client()
        resp = c.get("/api/reports/owner-notes?month=2026-05")
        # Django login_required redirects to login, but our _require_access returns 401
        assert resp.status_code == 401

    def test_leasing_user_returns_403(self, leasing_client, draft):
        resp = leasing_client.get("/api/reports/owner-notes?month=2026-05")
        assert resp.status_code == 403

    def test_financial_fields_are_null(self, auth_client, draft):
        resp = auth_client.get("/api/reports/owner-notes?month=2026-05")
        note = resp.json()[0]
        assert note["statement_period"] is None
        assert note["total_income"] is None
        assert note["total_expenses"] is None
        assert note["total_distribution"] is None
        assert note["ending_balance"] is None


# ---------------------------------------------------------------------------
# POST /approve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApproveNote:
    def test_approve_sets_status(self, auth_client, draft):
        resp = auth_client.post(
            f"/api/reports/owner-notes/{draft.pk}/approve",
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"

        draft.refresh_from_db()
        assert draft.status == "approved"

    def test_approve_sent_draft_returns_400(self, auth_client, sent_draft):
        resp = auth_client.post(
            f"/api/reports/owner-notes/{sent_draft.pk}/approve",
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /approve-all
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApproveAll:
    def test_bulk_approve(self, auth_client, owner):
        # Create 3 drafts
        for i in range(3):
            p = Portfolio.objects.create(rentvine_id=100 + i, name=f"Portfolio {i}")
            o = Owner.objects.create(
                rentvine_contact_id=100 + i,
                name=f"Owner {i}",
                first_name=f"Owner{i}",
                email=f"o{i}@example.com",
                is_active=True,
            )
            o.portfolios.add(p)
            EmailDraft.objects.create(
                product="monthly_owner_notes",
                owner=o,
                recipient_email=f"o{i}@example.com",
                subject="Test",
                body_html="<p>Test</p>",
                generated_note="Test note",
                status="draft",
                period_type="monthly",
                period_start=date(2026, 5, 1),
                period_end=date(2026, 5, 31),
            )

        resp = auth_client.post(
            "/api/reports/owner-notes/approve-all?month=2026-05",
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] == 3

        assert EmailDraft.objects.filter(
            product="monthly_owner_notes", status="approved"
        ).count() == 3


# ---------------------------------------------------------------------------
# PUT /{id} (update note)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateNote:
    def test_updates_generated_note_and_body_html(self, auth_client, draft):
        new_text = "Updated: This month we did great things."
        resp = auth_client.put(
            f"/api/reports/owner-notes/{draft.pk}",
            data=json.dumps({"generated_note": new_text}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated_note"] == new_text
        assert data["word_count"] == 7

        draft.refresh_from_db()
        assert draft.generated_note == new_text
        # body_html was re-rendered from the wrapper template
        assert "Updated: This month we did great things." in draft.body_html
        assert "Hi Alice" in draft.body_html
        assert "Owner Update" in draft.body_html


# ---------------------------------------------------------------------------
# POST /{id}/send
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendNote:
    def test_send_calls_send_draft(self, auth_client, approved_draft):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.headers = {"X-Message-Id": "test123"}

        with patch("comms.services.sendgrid.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            resp = auth_client.post(
                f"/api/reports/owner-notes/{approved_draft.pk}/send",
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "sent"

        approved_draft.refresh_from_db()
        assert approved_draft.status == "sent"

    def test_send_already_sent_returns_400(self, auth_client, sent_draft):
        resp = auth_client.post(
            f"/api/reports/owner-notes/{sent_draft.pk}/send",
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteNote:
    def test_delete_removes_draft(self, auth_client, draft):
        draft_id = draft.pk
        resp = auth_client.delete(f"/api/reports/owner-notes/{draft_id}")
        assert resp.status_code == 200
        assert not EmailDraft.objects.filter(pk=draft_id).exists()

    def test_delete_sent_returns_403(self, auth_client, sent_draft):
        resp = auth_client.delete(f"/api/reports/owner-notes/{sent_draft.pk}")
        assert resp.status_code == 403
        assert EmailDraft.objects.filter(pk=sent_draft.pk).exists()

    def test_delete_nonexistent_returns_404(self, auth_client):
        resp = auth_client.delete("/api/reports/owner-notes/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateEndpoint:
    def test_generate_returns_ok(self, auth_client):
        with patch("comms.api.generate_monthly_notes") as mock_gen:
            resp = auth_client.post(
                "/api/reports/owner-notes/generate",
                data=json.dumps({
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-31",
                    "dry_run": False,
                }),
                content_type="application/json",
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_generate_missing_dates_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/reports/owner-notes/generate",
            data=json.dumps({"dry_run": False}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDryRun:
    def test_dry_run_writes_no_rows(self, owner):
        """dry_run=True runs the pipeline but writes no EmailDraft rows."""
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text='{"intro": "Test intro.", "process_summaries": {"101": "Test summary."}}'
            )
        ]
        mock_client = MagicMock()
        mock_client.return_value.messages.create.return_value = mock_message

        selector_data = {
            "owner_first_name": "Alice",
            "processes_by_category": {
                "renewal": [
                    {
                        "process_id": 101,
                        "name": "Lease Renewal - 456 Oak Ave",
                        "category": "renewal",
                        "stage_name": "Negotiation",
                        "stage_status": "active",
                        "address": "456 Oak Ave",
                        "unit_number": "",
                    },
                ],
            },
            "total_count": 1,
            "_has_data": True,
            "_degraded": False,
        }

        with patch("comms.services.anthropic.Anthropic", mock_client), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import _build_monthly_context, _build_monthly_prompt
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: selector_data
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            result = generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
                dry_run=True,
            )

        assert result["generated"] == 1
        assert EmailDraft.objects.count() == 0
        # Anthropic was called (selector + AI run normally)
        mock_client.return_value.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Re-run guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRerunGuard:
    def test_approved_draft_not_overwritten(self, owner):
        """Re-running generate_drafts skips an approved draft."""
        # Create an approved draft first
        EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=owner,
            recipient_email="alice@example.com",
            subject="Your May 2026 Owner Update",
            body_html="<p>Original</p>",
            generated_note="Original note.",
            status="approved",
            period_type="monthly",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )

        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text='{"intro": "New intro.", "process_summaries": {"101": "New summary."}}'
            )
        ]
        mock_client = MagicMock()
        mock_client.return_value.messages.create.return_value = mock_message

        selector_data = {
            "owner_first_name": "Alice",
            "processes_by_category": {
                "renewal": [
                    {
                        "process_id": 101,
                        "name": "Lease Renewal",
                        "category": "renewal",
                        "stage_name": "Negotiation",
                        "stage_status": "active",
                        "address": "456 Oak Ave",
                        "unit_number": "",
                    },
                ],
            },
            "total_count": 1,
            "_has_data": True,
            "_degraded": False,
        }

        with patch("comms.services.anthropic.Anthropic", mock_client), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import _build_monthly_context, _build_monthly_prompt
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: selector_data
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            result = generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
            )

        assert result["generated"] == 0
        assert result["skipped"] == 1

        draft = EmailDraft.objects.get(
            product="monthly_owner_notes", owner=owner,
            period_start=date(2026, 5, 1),
        )
        assert draft.status == "approved"
        assert draft.generated_note == "Original note."

    def test_draft_status_draft_is_overwritten(self, owner):
        """Re-running generate_drafts overwrites a draft-status draft."""
        EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=owner,
            recipient_email="alice@example.com",
            subject="Old subject",
            body_html="<p>Old</p>",
            generated_note="Old note.",
            status="draft",
            period_type="monthly",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )

        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text='{"intro": "New intro.", "process_summaries": {"101": "New summary."}}'
            )
        ]
        mock_client = MagicMock()
        mock_client.return_value.messages.create.return_value = mock_message

        selector_data = {
            "owner_first_name": "Alice",
            "processes_by_category": {
                "renewal": [
                    {
                        "process_id": 101,
                        "name": "Lease Renewal",
                        "category": "renewal",
                        "stage_name": "Negotiation",
                        "stage_status": "active",
                        "address": "456 Oak Ave",
                        "unit_number": "",
                    },
                ],
            },
            "total_count": 1,
            "_has_data": True,
            "_degraded": False,
        }

        with patch("comms.services.anthropic.Anthropic", mock_client), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import _build_monthly_context, _build_monthly_prompt
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: selector_data
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            result = generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
            )

        assert result["generated"] == 1
        assert EmailDraft.objects.count() == 1

        draft = EmailDraft.objects.get(
            product="monthly_owner_notes", owner=owner,
            period_start=date(2026, 5, 1),
        )
        assert draft.status == "draft"
        assert "New intro." in draft.generated_note


# ---------------------------------------------------------------------------
# DELETE /delete-all
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAll:
    def test_deletes_non_sent_drafts(self, auth_client, draft, sent_draft):
        resp = auth_client.post(
            "/api/reports/owner-notes/delete-all?month=2026-05",
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 1  # only the draft, not the sent one
        assert not EmailDraft.objects.filter(pk=draft.pk).exists()
        # sent draft from April is untouched (different month)
        assert EmailDraft.objects.filter(pk=sent_draft.pk).exists()


# ---------------------------------------------------------------------------
# POST /send-all
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendAll:
    def test_sends_approved_drafts(self, auth_client, approved_draft):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.headers = {"X-Message-Id": "bulk123"}

        with patch("comms.services.sendgrid.SendGridAPIClient") as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            resp = auth_client.post(
                "/api/reports/owner-notes/send-all?month=2026-05",
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] == 1
        assert data["failed"] == 0

        approved_draft.refresh_from_db()
        assert approved_draft.status == "sent"

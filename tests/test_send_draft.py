"""
Tests for the send_draft() service function and the send_maintenance_draft
management command.  SendGrid is always mocked — no network calls.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from comms.models import EmailDraft
from comms.services import send_draft
from core.models import Owner, Portfolio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=99, name="Test Portfolio")


@pytest.fixture
def owner(portfolio):
    o = Owner.objects.create(
        rentvine_contact_id=99,
        name="Test Owner",
        first_name="Test",
        email="owner@example.com",
        is_active=True,
    )
    o.portfolios.add(portfolio)
    return o


@pytest.fixture
def draft(owner):
    return EmailDraft.objects.create(
        product="maintenance",
        owner=owner,
        recipient_email="owner@example.com",
        subject="Weekly Maintenance Update",
        body_html="<p>Hello</p>",
        status="draft",
        period_type="weekly",
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
    )


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username="rodrigo", password="testpass", role=User.Role.ADMIN
    )


@pytest.fixture
def maintenance_user():
    return User.objects.create_user(
        username="zach", password="testpass", role=User.Role.MAINTENANCE
    )


@pytest.fixture
def leasing_user():
    return User.objects.create_user(
        username="bill", password="testpass", role=User.Role.LEASING
    )


@pytest.fixture
def mock_sg_success():
    """Patch SendGridAPIClient to return a 202 Accepted."""
    response = MagicMock()
    response.status_code = 202
    response.headers = {"X-Message-Id": "abc123"}

    client = MagicMock()
    client.return_value.send.return_value = response

    with patch("comms.services.sendgrid.SendGridAPIClient", client) as m:
        yield m


@pytest.fixture
def mock_sg_failure():
    """Patch SendGridAPIClient to return a 403 Forbidden."""
    response = MagicMock()
    response.status_code = 403
    response.headers = {}

    client = MagicMock()
    client.return_value.send.return_value = response

    with patch("comms.services.sendgrid.SendGridAPIClient", client) as m:
        yield m


@pytest.fixture
def mock_sg_exception():
    """Patch SendGridAPIClient so .send() raises an exception."""
    client = MagicMock()
    client.return_value.send.side_effect = ConnectionError("network down")

    with patch("comms.services.sendgrid.SendGridAPIClient", client) as m:
        yield m


# ---------------------------------------------------------------------------
# Service function tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendDraft:
    def test_admin_can_send(self, draft, admin_user, mock_sg_success):
        result = send_draft(draft, admin_user)

        draft.refresh_from_db()
        assert draft.status == "sent"
        assert draft.sent_at is not None
        assert draft.sent_by == admin_user
        assert result["mode"] == "live"
        assert result["sendgrid_status"] == 202

    def test_maintenance_can_send(self, draft, maintenance_user, mock_sg_success):
        result = send_draft(draft, maintenance_user)

        draft.refresh_from_db()
        assert draft.status == "sent"
        assert result["mode"] == "live"

    def test_leasing_blocked(self, draft, leasing_user, mock_sg_success):
        with pytest.raises(PermissionError):
            send_draft(draft, leasing_user)

        draft.refresh_from_db()
        assert draft.status == "draft"

    def test_already_sent_refused(self, draft, admin_user, mock_sg_success):
        draft.status = "sent"
        draft.save(update_fields=["status"])

        with pytest.raises(ValueError, match="already been sent"):
            send_draft(draft, admin_user)

    def test_empty_subject_refused(self, draft, admin_user, mock_sg_success):
        draft.subject = ""
        draft.save(update_fields=["subject"])

        with pytest.raises(ValueError, match="empty subject"):
            send_draft(draft, admin_user)

    def test_empty_body_refused(self, draft, admin_user, mock_sg_success):
        draft.body_html = ""
        draft.save(update_fields=["body_html"])

        with pytest.raises(ValueError, match="empty body"):
            send_draft(draft, admin_user)

    def test_sendgrid_failure_leaves_draft(
        self, draft, admin_user, mock_sg_exception
    ):
        with pytest.raises(ConnectionError):
            send_draft(draft, admin_user)

        draft.refresh_from_db()
        assert draft.status == "draft"

    def test_recipient_override(self, draft, admin_user, mock_sg_success):
        result = send_draft(
            draft, admin_user, recipient_override="test@example.com"
        )

        assert result["recipient"] == "test@example.com"
        assert result["mode"] == "test"

    def test_sandbox_does_not_mark_sent(self, draft, admin_user, mock_sg_success):
        result = send_draft(draft, admin_user, sandbox=True)

        draft.refresh_from_db()
        assert draft.status == "draft"
        assert draft.sent_at is None
        assert result["mode"] == "sandbox"

    def test_test_email_does_not_mark_sent(
        self, draft, admin_user, mock_sg_success
    ):
        result = send_draft(
            draft, admin_user, recipient_override="test@example.com"
        )

        draft.refresh_from_db()
        assert draft.status == "draft"
        assert draft.sent_at is None
        assert result["mode"] == "test"

    def test_live_marks_sent(self, draft, admin_user, mock_sg_success):
        result = send_draft(draft, admin_user)

        draft.refresh_from_db()
        assert draft.status == "sent"
        assert draft.sent_at is not None
        assert draft.sent_by == admin_user
        assert result["mode"] == "live"
        assert result["sendgrid_status"] == 202

    def test_non_2xx_leaves_draft(self, draft, admin_user, mock_sg_failure):
        with pytest.raises(RuntimeError, match="403"):
            send_draft(draft, admin_user)

        draft.refresh_from_db()
        assert draft.status == "draft"


# ---------------------------------------------------------------------------
# Management command test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendMaintenanceDraftCommand:
    def test_command_refuses_without_flag(self, draft, admin_user):
        with pytest.raises(CommandError, match="must specify one of"):
            call_command(
                "send_maintenance_draft",
                str(draft.pk),
                "--acting-user",
                admin_user.username,
            )

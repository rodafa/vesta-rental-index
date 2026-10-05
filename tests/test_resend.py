"""
Tests for the resend monthly owner email feature.

Covers: send_draft resend flag, resend_monthly_owner_emails service function,
dry run, cutoff filtering, limit, and failure-stops-run.
SendGrid is always mocked — no network calls.
"""

import json
from datetime import date, datetime, timezone as _tz
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import User
from comms.models import EmailDraft
from comms.services import resend_monthly_owner_emails, send_draft
from core.models import Owner, Portfolio

pytestmark = pytest.mark.django_db

BODY_HTML = json.dumps({
    "financials_html": "<p>Financials</p>",
    "notes_html": "<p>Notes</p>",
})

# Cutoff: drafts sent before this are eligible for resend.
SENT_BEFORE = datetime(2026, 10, 3, 0, 0, 0, tzinfo=_tz.utc)


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
def admin_user():
    return User.objects.create_user(
        username="rodrigo_resend", password="testpass", role=User.Role.ADMIN
    )


@pytest.fixture
def sent_draft(owner):
    """A monthly owner notes draft that has already been sent (before the cutoff)."""
    return EmailDraft.objects.create(
        product="monthly_owner_notes",
        owner=owner,
        recipient_email="owner@example.com",
        subject="Monthly Owner Update — Sep 1 – Sep 30, 2026",
        body_html=BODY_HTML,
        generated_note="",
        status="sent",
        period_type="monthly",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        sent_at=datetime(2026, 10, 1, 12, 0, 0, tzinfo=_tz.utc),
    )


@pytest.fixture
def mock_sg_success():
    """Patch SendGridAPIClient to return a 202 Accepted."""
    response = MagicMock()
    response.status_code = 202
    response.headers = {"X-Message-Id": "resend-abc123"}

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
# send_draft resend flag
# ---------------------------------------------------------------------------


class TestSendDraftResend:
    def test_resend_bypasses_sent_guard(self, sent_draft, admin_user, mock_sg_success):
        """resend=True lets a sent draft through; sent_at updated, status stays 'sent'."""
        original_sent_at = sent_draft.sent_at

        result = send_draft(sent_draft, admin_user, resend=True)

        sent_draft.refresh_from_db()
        assert sent_draft.status == "sent"
        assert sent_draft.sent_at > original_sent_at
        assert sent_draft.sent_by == admin_user
        assert result["mode"] == "resend"
        assert result["sendgrid_status"] == 202

    def test_resend_no_cc(self, sent_draft, admin_user, mock_sg_success):
        """resend=True skips the accounting CC."""
        send_draft(sent_draft, admin_user, resend=True)

        sg_instance = mock_sg_success.return_value
        mail_obj = sg_instance.send.call_args[0][0]

        for p in mail_obj.personalizations:
            assert p.ccs is None or len(p.ccs) == 0

    def test_resend_false_still_raises_on_sent(self, sent_draft, admin_user, mock_sg_success):
        """Default resend=False on a sent draft still raises ValueError."""
        with pytest.raises(ValueError, match="already been sent"):
            send_draft(sent_draft, admin_user)


# ---------------------------------------------------------------------------
# resend_monthly_owner_emails service function
# ---------------------------------------------------------------------------


class TestResendService:
    def test_dry_run_sends_nothing(self, sent_draft, admin_user, mock_sg_success):
        """dry_run=True yields 'would_send' and never calls SendGrid."""
        results = list(resend_monthly_owner_emails(
            period_start=date(2026, 9, 1),
            sent_before=SENT_BEFORE,
            acting_user=admin_user,
            dry_run=True,
        ))

        assert len(results) == 1
        assert results[0]["action"] == "would_send"
        assert results[0]["draft"].pk == sent_draft.pk

        # SendGrid was never called
        mock_sg_success.return_value.send.assert_not_called()

    def test_draft_after_cutoff_skipped(self, owner, admin_user, mock_sg_success):
        """A draft with sent_at >= sent_before is not eligible."""
        EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=owner,
            recipient_email="owner@example.com",
            subject="Monthly Owner Update",
            body_html=BODY_HTML,
            status="sent",
            period_type="monthly",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            # sent_at is AT the cutoff — not before it
            sent_at=datetime(2026, 10, 3, 0, 0, 0, tzinfo=_tz.utc),
        )

        results = list(resend_monthly_owner_emails(
            period_start=date(2026, 9, 1),
            sent_before=SENT_BEFORE,
            acting_user=admin_user,
            dry_run=True,
        ))

        assert len(results) == 0

    def test_limit_honored(self, admin_user, mock_sg_success):
        """With 5 eligible drafts and limit=2, only 2 are processed."""
        for i in range(5):
            p = Portfolio.objects.create(rentvine_id=200 + i, name=f"Port {i}")
            o = Owner.objects.create(
                rentvine_contact_id=200 + i,
                name=f"Owner {i}",
                first_name=f"Owner{i}",
                email=f"batch{i}@example.com",
                is_active=True,
            )
            o.portfolios.add(p)
            EmailDraft.objects.create(
                product="monthly_owner_notes",
                owner=o,
                recipient_email=f"batch{i}@example.com",
                subject="Monthly Owner Update",
                body_html=BODY_HTML,
                status="sent",
                period_type="monthly",
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
                sent_at=datetime(2026, 10, 1, 12, 0, 0, tzinfo=_tz.utc),
            )

        results = list(resend_monthly_owner_emails(
            period_start=date(2026, 9, 1),
            sent_before=SENT_BEFORE,
            acting_user=admin_user,
            limit=2,
        ))

        assert len(results) == 2
        assert all(r["action"] == "sent" for r in results)

    def test_failure_stops_run_and_leaves_draft(self, sent_draft, admin_user, mock_sg_exception):
        """On failure: yields 'failed', stops, and sent_at is unchanged."""
        # Create a second eligible draft that should NOT be reached
        p2 = Portfolio.objects.create(rentvine_id=200, name="Port 2")
        o2 = Owner.objects.create(
            rentvine_contact_id=200,
            name="Owner 2",
            first_name="Owner2",
            email="owner2@example.com",
            is_active=True,
        )
        o2.portfolios.add(p2)
        draft2 = EmailDraft.objects.create(
            product="monthly_owner_notes",
            owner=o2,
            recipient_email="owner2@example.com",
            subject="Monthly Owner Update",
            body_html=BODY_HTML,
            status="sent",
            period_type="monthly",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            sent_at=datetime(2026, 10, 1, 12, 0, 0, tzinfo=_tz.utc),
        )

        original_sent_at = sent_draft.sent_at

        results = list(resend_monthly_owner_emails(
            period_start=date(2026, 9, 1),
            sent_before=SENT_BEFORE,
            acting_user=admin_user,
        ))

        # Only one result — the failure — then iteration stopped
        assert len(results) == 1
        assert results[0]["action"] == "failed"
        assert "network down" in results[0]["error"]

        # Draft's sent_at is unchanged (send_draft raised before saving)
        sent_draft.refresh_from_db()
        assert sent_draft.sent_at == original_sent_at

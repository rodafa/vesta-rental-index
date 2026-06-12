"""
Tests for distribution email send path.

Covers:
  - Send mode does NOT regenerate snapshots
  - Generate mode does NOT send
  - --live blocked without OWNER_PORTAL_URL
  - --sandbox warns without OWNER_PORTAL_URL
  - Already-sent skipped on live
  - Already-sent skipped via send_distribution_email helper
  - Sent draft never overwritten
  - Test email redirects recipient
"""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from comms.models import EmailDraft, PortfolioDistributionSnapshot
from comms.services import send_distribution_email
from core.models import Owner, Portfolio

pytestmark = pytest.mark.django_db

MONTH_START = date(2026, 6, 1)
MONTH_END = date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username="rodrigo",
        email="rodrigo@vestapm.com",
        password="test",
        role="admin",
    )


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=10, name="Test LLC")


@pytest.fixture
def owner(portfolio):
    o = Owner.objects.create(
        rentvine_contact_id=100,
        name="Alice Smith",
        first_name="Alice",
        email="alice@example.com",
        is_active=True,
    )
    o.portfolios.add(portfolio)
    return o


@pytest.fixture
def snapshot(portfolio):
    return PortfolioDistributionSnapshot.objects.create(
        portfolio=portfolio,
        period_type="monthly",
        period_start=MONTH_START,
        period_end=MONTH_END,
        distribution_amount=Decimal("2500.00"),
        distribution_date=date(2026, 6, 11),
        line_items=[
            {"property_id": 1, "property_address": "123 Main St", "expected": "1500.00", "collected": "1500.00"},
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("comms.management.commands.generate_portfolio_distributions._fetch_month_transactions")
@patch("comms.services.build_distribution_snapshot")
@patch("comms.services.send_draft")
def test_send_mode_does_not_regenerate_snapshots(
    mock_send_draft, mock_build, mock_fetch, admin_user, owner, snapshot
):
    """With --sandbox, should NOT call _fetch_month_transactions or build_distribution_snapshot."""
    mock_send_draft.return_value = {
        "draft_id": 1,
        "owner": "Alice Smith",
        "recipient": "alice@example.com",
        "sent_by": "rodrigo",
        "mode": "sandbox",
        "sendgrid_status": 200,
        "sendgrid_message_id": "",
    }

    out = StringIO()
    call_command(
        "generate_portfolio_distributions",
        "--month", "2026-06",
        "--sandbox",
        "--acting-user", "rodrigo",
        "--owner-email", "alice@example.com",
        stdout=out,
    )

    mock_fetch.assert_not_called()
    mock_build.assert_not_called()
    mock_send_draft.assert_called_once()


@patch("comms.management.commands.generate_portfolio_distributions._fetch_month_transactions")
@patch("comms.services.send_draft")
def test_generate_mode_does_not_send(mock_send_draft, mock_fetch, admin_user, portfolio):
    """No send flags → generate mode, send_draft should NOT be called."""
    mock_fetch.return_value = []  # no transactions

    out = StringIO()
    call_command(
        "generate_portfolio_distributions",
        "--month", "2026-06",
        stdout=out,
    )

    mock_send_draft.assert_not_called()


@patch("comms.management.commands.generate_portfolio_distributions.settings")
def test_live_blocked_without_portal_url(mock_settings, admin_user, owner, snapshot):
    mock_settings.OWNER_PORTAL_URL = ""

    with pytest.raises(CommandError, match="OWNER_PORTAL_URL"):
        call_command(
            "generate_portfolio_distributions",
            "--month", "2026-06",
            "--live",
            "--acting-user", "rodrigo",
        )


@patch("comms.services.send_draft")
@patch("comms.management.commands.generate_portfolio_distributions.settings")
def test_sandbox_warns_without_portal_url(
    mock_settings, mock_send_draft, admin_user, owner, snapshot
):
    mock_settings.OWNER_PORTAL_URL = ""

    mock_send_draft.return_value = {
        "draft_id": 1,
        "owner": "Alice Smith",
        "recipient": "alice@example.com",
        "sent_by": "rodrigo",
        "mode": "sandbox",
        "sendgrid_status": 200,
        "sendgrid_message_id": "",
    }

    out = StringIO()
    err = StringIO()
    # Should succeed (no exception) but warn
    call_command(
        "generate_portfolio_distributions",
        "--month", "2026-06",
        "--sandbox",
        "--acting-user", "rodrigo",
        "--owner-email", "alice@example.com",
        stdout=out,
        stderr=err,
    )

    assert "OWNER_PORTAL_URL" in err.getvalue()


@patch("comms.services.send_draft")
def test_already_sent_skipped_on_live(mock_send_draft, admin_user, owner, snapshot):
    """EmailDraft with status='sent' → owner skipped."""
    # Create a sent draft
    EmailDraft.objects.create(
        product="owner_distributions",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        period_type="monthly",
        period_start=MONTH_START,
        period_end=MONTH_END,
        status="sent",
    )

    out = StringIO()
    call_command(
        "generate_portfolio_distributions",
        "--month", "2026-06",
        "--sandbox",
        "--acting-user", "rodrigo",
        "--owner-email", "alice@example.com",
        stdout=out,
    )

    mock_send_draft.assert_not_called()
    assert "already sent" in out.getvalue()


def test_already_sent_skipped_on_dashboard_send(admin_user, owner, snapshot):
    """send_distribution_email returns 'already_sent' for existing sent draft."""
    EmailDraft.objects.create(
        product="owner_distributions",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Test",
        body_html="<p>Test</p>",
        period_type="monthly",
        period_start=MONTH_START,
        period_end=MONTH_END,
        status="sent",
    )

    result = send_distribution_email(
        recipient_email="alice@example.com",
        period_start=MONTH_START,
        period_type="monthly",
        period_end=MONTH_END,
        acting_user=admin_user,
        sandbox=True,
    )

    assert result == "already_sent"


@patch("comms.services.send_draft")
def test_sent_draft_never_overwritten(mock_send_draft, admin_user, owner, snapshot):
    """Re-running send on an already-sent draft should not overwrite it."""
    original_html = "<p>Original</p>"
    draft = EmailDraft.objects.create(
        product="owner_distributions",
        owner=owner,
        recipient_email="alice@example.com",
        subject="Original Subject",
        body_html=original_html,
        period_type="monthly",
        period_start=MONTH_START,
        period_end=MONTH_END,
        status="sent",
    )

    result = send_distribution_email(
        recipient_email="alice@example.com",
        period_start=MONTH_START,
        period_type="monthly",
        period_end=MONTH_END,
        acting_user=admin_user,
    )

    assert result == "already_sent"

    # Verify original draft is unchanged
    draft.refresh_from_db()
    assert draft.body_html == original_html
    assert draft.status == "sent"
    mock_send_draft.assert_not_called()


@patch("comms.services.send_draft")
def test_test_email_redirects_recipient(mock_send_draft, admin_user, owner, snapshot):
    """recipient_override should be passed to send_draft."""
    mock_send_draft.return_value = {
        "draft_id": 1,
        "owner": "Alice Smith",
        "recipient": "rodrigo@vestapm.com",
        "sent_by": "rodrigo",
        "mode": "test",
        "sendgrid_status": 200,
        "sendgrid_message_id": "",
    }

    result = send_distribution_email(
        recipient_email="alice@example.com",
        period_start=MONTH_START,
        period_type="monthly",
        period_end=MONTH_END,
        acting_user=admin_user,
        recipient_override="rodrigo@vestapm.com",
    )

    assert isinstance(result, dict)
    mock_send_draft.assert_called_once()
    call_kwargs = mock_send_draft.call_args
    assert call_kwargs.kwargs.get("recipient_override") == "rodrigo@vestapm.com" or \
           call_kwargs[1].get("recipient_override") == "rodrigo@vestapm.com" or \
           (len(call_kwargs[0]) > 2 and call_kwargs[0][2] == "rodrigo@vestapm.com")

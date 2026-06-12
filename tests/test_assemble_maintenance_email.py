"""
Tests for Layer 2: owner-grain maintenance email assembly + send path.

Production-shaped fixtures:
  - Multiple portfolios with maintenance notes
  - Owner-email union (two Owner records, same email)
  - Quiet-portfolio omission (portfolio with no note)
  - Zero-activity owner (no notes at all)
  - Envelope wraps fragments correctly
  - Dedup: two owners sharing email → ONE assembled email, ONE send
"""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from comms.models import EmailDraft, PortfolioMaintenanceNote
from comms.services import assemble_owner_maintenance_email
from core.models import Owner, Portfolio, Property, Unit


pytestmark = pytest.mark.django_db

PERIOD_START = date(2026, 5, 25)
PERIOD_END = date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio_a():
    return Portfolio.objects.create(rentvine_id=10, name="Alpha Portfolio")


@pytest.fixture
def portfolio_b():
    return Portfolio.objects.create(rentvine_id=11, name="Beta Portfolio")


@pytest.fixture
def portfolio_quiet():
    """Portfolio with NO maintenance note for the period."""
    return Portfolio.objects.create(rentvine_id=12, name="Quiet Portfolio")


@pytest.fixture
def owner_single(portfolio_a, portfolio_b):
    """Owner with two portfolios — both have notes."""
    owner = Owner.objects.create(
        rentvine_contact_id=100,
        name="Alice Smith",
        first_name="Alice",
        email="alice@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_a, portfolio_b)
    return owner


@pytest.fixture
def owner_shared_1(portfolio_a):
    """First owner sharing email with owner_shared_2."""
    owner = Owner.objects.create(
        rentvine_contact_id=200,
        name="Bob Jones",
        first_name="Bob",
        email="shared@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_a)
    return owner


@pytest.fixture
def owner_shared_2(portfolio_b):
    """Second owner sharing email with owner_shared_1."""
    owner = Owner.objects.create(
        rentvine_contact_id=201,
        name="Carol Jones",
        first_name="Carol",
        email="shared@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_b)
    return owner


@pytest.fixture
def owner_no_activity(portfolio_quiet):
    """Owner whose only portfolio has no note."""
    owner = Owner.objects.create(
        rentvine_contact_id=300,
        name="Dave Empty",
        first_name="Dave",
        email="dave@example.com",
        is_active=True,
    )
    owner.portfolios.add(portfolio_quiet)
    return owner


@pytest.fixture
def note_alpha(portfolio_a):
    """Approved maintenance note for Alpha Portfolio."""
    return PortfolioMaintenanceNote.objects.create(
        portfolio=portfolio_a,
        period_type="weekly",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        notes_html='<div class="alpha-fragment"><p>Alpha: 2 open, 1 closed.</p></div>',
        generated_note="Alpha: 2 open work orders.",
        status="approved",
    )


@pytest.fixture
def note_beta(portfolio_b):
    """Draft maintenance note for Beta Portfolio."""
    return PortfolioMaintenanceNote.objects.create(
        portfolio=portfolio_b,
        period_type="weekly",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        notes_html='<div class="beta-fragment"><p>Beta: 1 open, 0 closed.</p></div>',
        generated_note="Beta: 1 open work order.",
        status="draft",
    )


@pytest.fixture
def note_beta_approved(portfolio_b):
    """Approved maintenance note for Beta Portfolio."""
    return PortfolioMaintenanceNote.objects.create(
        portfolio=portfolio_b,
        period_type="weekly",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        notes_html='<div class="beta-fragment"><p>Beta: 1 open, 0 closed.</p></div>',
        generated_note="Beta: 1 open work order.",
        status="approved",
    )


# ---------------------------------------------------------------------------
# Assembly tests
# ---------------------------------------------------------------------------


class TestAssembleOwnerMaintenanceEmail:
    def test_concatenates_multiple_portfolio_fragments(
        self, owner_single, note_alpha, note_beta
    ):
        """Multiple portfolio fragments appear under one envelope."""
        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is not None
        assert "alpha-fragment" in result["body_html"]
        assert "beta-fragment" in result["body_html"]
        # Both portfolios listed
        names = {p["name"] for p in result["portfolios"]}
        assert names == {"Alpha Portfolio", "Beta Portfolio"}

    def test_owner_email_union(
        self, owner_shared_1, owner_shared_2, note_alpha, note_beta
    ):
        """Two Owner records sharing an email → both portfolios in one email."""
        result = assemble_owner_maintenance_email(
            "shared@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is not None
        names = {p["name"] for p in result["portfolios"]}
        assert names == {"Alpha Portfolio", "Beta Portfolio"}
        # Uses lowest-PK owner's name
        assert result["owner_name"] == "Bob"

    def test_quiet_portfolio_omitted(
        self, owner_single, portfolio_quiet, note_alpha, note_beta
    ):
        """Portfolio with no note for the period is simply omitted."""
        # Add quiet portfolio to the owner
        owner_single.portfolios.add(portfolio_quiet)

        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is not None
        names = {p["name"] for p in result["portfolios"]}
        # Quiet portfolio NOT in the list
        assert "Quiet Portfolio" not in names
        assert "Alpha Portfolio" in names

    def test_zero_activity_owner_returns_none(self, owner_no_activity):
        """Owner with zero notes for the period → None (no email)."""
        result = assemble_owner_maintenance_email(
            "dave@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is None

    def test_all_approved_false_when_any_unapproved(
        self, owner_single, note_alpha, note_beta
    ):
        """all_approved is False when any portfolio note is not approved."""
        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is not None
        # note_alpha is approved, note_beta is draft
        assert result["all_approved"] is False

    def test_all_approved_true_when_all_approved(
        self, owner_single, note_alpha, note_beta_approved
    ):
        """all_approved is True when all portfolio notes are approved."""
        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is not None
        assert result["all_approved"] is True

    def test_envelope_wraps_correctly(self, owner_single, note_alpha, note_beta):
        """Envelope contains greeting, header, CTA, footer, and fragments."""
        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        html = result["body_html"]
        # Greeting
        assert "Hi Alice," in html
        # Header
        assert "Weekly Maintenance Update" in html
        # CTA
        assert "owner portal" in html.lower() or "vestapm.rentvine.com" in html
        # Footer
        assert "Vesta Property Management" in html
        # Fragments present
        assert "alpha-fragment" in html
        assert "beta-fragment" in html

    def test_nonexistent_email_returns_none(self):
        """Email with no matching owner → None."""
        result = assemble_owner_maintenance_email(
            "nobody@example.com", PERIOD_START, period_type="weekly"
        )
        assert result is None

    def test_blank_email_returns_none(self):
        """Blank email → None."""
        result = assemble_owner_maintenance_email(
            "", PERIOD_START, period_type="weekly"
        )
        assert result is None

    def test_portfolios_ordered_alphabetically(
        self, owner_single, note_alpha, note_beta
    ):
        """Portfolios are listed in alphabetical order."""
        result = assemble_owner_maintenance_email(
            "alice@example.com", PERIOD_START, period_type="weekly"
        )
        portfolio_names = [p["name"] for p in result["portfolios"]]
        assert portfolio_names == ["Alpha Portfolio", "Beta Portfolio"]


# ---------------------------------------------------------------------------
# Dedup test — command-level
# ---------------------------------------------------------------------------


class TestSendMaintenanceEmailsDedup:
    """Two Owner records sharing one email → ONE assembled email, ONE send."""

    @patch("comms.management.commands.send_maintenance_emails.send_draft")
    def test_shared_email_produces_one_send(
        self, mock_send, owner_shared_1, owner_shared_2, note_alpha, note_beta_approved
    ):
        """
        The send command processes each unique email exactly once.
        Two owners with shared@example.com → one call to send_draft.
        """
        from io import StringIO
        from django.core.management import call_command
        from accounts.models import User

        # Create acting user
        user = User.objects.create_user(
            username="testadmin", password="pass",
            email="admin@test.com", role="admin",
        )

        # Mock send_draft to succeed
        mock_send.return_value = {
            "draft_id": 1,
            "owner": "Bob Jones",
            "recipient": "shared@example.com",
            "sent_by": "testadmin",
            "mode": "sandbox",
            "sendgrid_status": 200,
            "sendgrid_message_id": "abc123",
        }

        out = StringIO()
        call_command(
            "send_maintenance_emails",
            "--period-start", "2026-05-25",
            "--acting-user", "testadmin",
            "--sandbox",
            "--owner-email", "shared@example.com",
            stdout=out,
        )

        # Exactly ONE send_draft call (not two)
        assert mock_send.call_count == 1

        # Exactly ONE EmailDraft created
        drafts = EmailDraft.objects.filter(
            product="maintenance",
            recipient_email="shared@example.com",
        )
        assert drafts.count() == 1

        # Draft uses rep_owner (lowest PK)
        draft = drafts.first()
        assert draft.owner == owner_shared_1  # pk=200 < pk=201

    @patch("comms.management.commands.send_maintenance_emails.send_draft")
    def test_all_owners_processed_once_each_email(
        self, mock_send, owner_shared_1, owner_shared_2, owner_no_activity,
        note_alpha, note_beta_approved
    ):
        """
        Without --owner-email, each unique email is processed exactly once.
        shared@example.com → 1 send, dave@example.com → skipped (no activity).
        """
        from io import StringIO
        from django.core.management import call_command
        from accounts.models import User

        user = User.objects.create_user(
            username="testadmin2", password="pass",
            email="admin2@test.com", role="admin",
        )

        mock_send.return_value = {
            "draft_id": 1,
            "owner": "Bob Jones",
            "recipient": "shared@example.com",
            "sent_by": "testadmin2",
            "mode": "sandbox",
            "sendgrid_status": 200,
            "sendgrid_message_id": "abc123",
        }

        out = StringIO()
        call_command(
            "send_maintenance_emails",
            "--period-start", "2026-05-25",
            "--acting-user", "testadmin2",
            "--sandbox",
            stdout=out,
        )

        # Only one send (shared email), dave skipped (no activity)
        assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# LIVE gate test
# ---------------------------------------------------------------------------


class TestLiveGate:
    @patch("comms.management.commands.send_maintenance_emails.send_draft")
    def test_live_blocked_when_not_all_approved(
        self, mock_send, owner_single, note_alpha, note_beta
    ):
        """LIVE send is blocked when any note is unapproved."""
        from io import StringIO
        from django.core.management import call_command
        from accounts.models import User

        user = User.objects.create_user(
            username="testadmin3", password="pass",
            email="admin3@test.com", role="admin",
        )

        out = StringIO()
        err = StringIO()
        call_command(
            "send_maintenance_emails",
            "--period-start", "2026-05-25",
            "--acting-user", "testadmin3",
            "--live",
            "--owner-email", "alice@example.com",
            stdout=out,
            stderr=err,
        )

        # send_draft was NOT called
        mock_send.assert_not_called()

        # No EmailDraft created
        assert not EmailDraft.objects.filter(
            product="maintenance",
            recipient_email="alice@example.com",
        ).exists()

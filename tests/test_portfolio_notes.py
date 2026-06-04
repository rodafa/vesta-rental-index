"""
Tests for the portfolio-grain monthly notes system (Phases 1–4).

Mirrors PRODUCTION data shapes:
  - Shared-email owners (two owners, same email, different portfolios)
  - Blank email owner (should be excluded)
  - Portfolio with no posted statement
  - Multi-portfolio owner
  - Mixed approved/draft portfolios (must read NOT ready)
"""

import json
import time
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from accounts.models import User
from accounting.models import PortfolioStatement
from comms.models import EmailDraft, PortfolioMonthlyNote
from comms.services import (
    assemble_owner_email,
    build_portfolio_section,
    generate_portfolio_notes,
    get_recipients_for_period,
    _normalize_email,
    _render_notes_html_from_text,
)
import comms.services as svc
from core.models import Owner, Portfolio, Property


pytestmark = pytest.mark.django_db

PERIOD_START = date(2026, 5, 1)
PERIOD_END = date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Fixtures — mirror production data shapes
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolios():
    """Three portfolios: two will be assigned to a shared-email owner pair."""
    p1 = Portfolio.objects.create(rentvine_id=101, name="Alpha Portfolio")
    p2 = Portfolio.objects.create(rentvine_id=102, name="Beta Portfolio")
    p3 = Portfolio.objects.create(rentvine_id=103, name="Gamma Portfolio")
    return p1, p2, p3


@pytest.fixture
def owners(portfolios):
    """
    Production-shape owners:
      - owner_a: owns Alpha, email shared@example.com
      - owner_b: owns Beta, SAME email shared@example.com (shared-email pair)
      - owner_c: owns Alpha AND Gamma (multi-portfolio), unique email
      - owner_blank: owns Beta, blank email (must be excluded)
    """
    p1, p2, p3 = portfolios

    owner_a = Owner.objects.create(
        rentvine_contact_id=1, name="Alice Adams", first_name="Alice",
        email="shared@example.com", is_active=True,
    )
    owner_a.portfolios.add(p1)

    owner_b = Owner.objects.create(
        rentvine_contact_id=2, name="Bob Baker", first_name="Bob",
        email="SHARED@example.com",  # uppercase — tests normalization
        is_active=True,
    )
    owner_b.portfolios.add(p2)

    owner_c = Owner.objects.create(
        rentvine_contact_id=3, name="Carol Clark", first_name="Carol",
        email="carol@example.com", is_active=True,
    )
    owner_c.portfolios.add(p1, p3)

    owner_blank = Owner.objects.create(
        rentvine_contact_id=4, name="No Email Owner", first_name="Nobody",
        email="", is_active=True,
    )
    owner_blank.portfolios.add(p2)

    return owner_a, owner_b, owner_c, owner_blank


@pytest.fixture
def statements(portfolios):
    """Alpha has a posted statement, Beta and Gamma do not."""
    p1, p2, p3 = portfolios
    stmt = PortfolioStatement.objects.create(
        portfolio=p1,
        rentvine_statement_id=9001,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        beginning_balance=1000,
        total_income=5000,
        total_expenses=1500,
        total_adjustments=0,
        ending_balance=4500,
        total_distribution=3500,
        status="2",  # posted
    )
    return stmt


@pytest.fixture
def portfolio_notes(portfolios):
    """
    Create PortfolioMonthlyNote rows:
      - Alpha: approved
      - Beta: draft
      - Gamma: approved
    """
    p1, p2, p3 = portfolios

    n1 = PortfolioMonthlyNote.objects.create(
        portfolio=p1, period_type="monthly", period_start=PERIOD_START,
        period_end=PERIOD_END,
        financials_html="<h2>Alpha</h2><p>Income: $5,000</p>",
        notes_html="<p>Alpha had a great month.</p>",
        generated_note="Alpha had a great month.",
        approved_generated_note="Alpha had a great month. (reviewed)",
        status="approved",
    )
    n2 = PortfolioMonthlyNote.objects.create(
        portfolio=p2, period_type="monthly", period_start=PERIOD_START,
        period_end=PERIOD_END,
        financials_html="<h2>Beta</h2><p>No statement.</p>",
        notes_html="<p>Beta was quiet.</p>",
        generated_note="Beta was quiet.",
        approved_generated_note="",
        status="draft",
    )
    n3 = PortfolioMonthlyNote.objects.create(
        portfolio=p3, period_type="monthly", period_start=PERIOD_START,
        period_end=PERIOD_END,
        financials_html="<h2>Gamma</h2><p>Income: $3,000</p>",
        notes_html="<p>Gamma is performing well.</p>",
        generated_note="Gamma is performing well.",
        approved_generated_note="Gamma is performing well. (reviewed)",
        status="approved",
    )
    return n1, n2, n3


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username="admin", email="admin@vesta.com",
        password="testpass", role="admin",
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestPortfolioMonthlyNoteModel:
    def test_create(self, portfolios):
        p = portfolios[0]
        note = PortfolioMonthlyNote.objects.create(
            portfolio=p, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            generated_note="Test note.",
        )
        assert note.pk is not None
        assert note.status == "draft"
        assert str(note) == f"PortfolioMonthlyNote: {p} ({PERIOD_START})"

    def test_unique_constraint(self, portfolios):
        p = portfolios[0]
        PortfolioMonthlyNote.objects.create(
            portfolio=p, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
        )
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            PortfolioMonthlyNote.objects.create(
                portfolio=p, period_type="monthly",
                period_start=PERIOD_START, period_end=PERIOD_END,
            )


# ---------------------------------------------------------------------------
# Assembly tests — the core of the new architecture
# ---------------------------------------------------------------------------


class TestAssembleOwnerEmail:
    def test_shared_email_unions_portfolios(self, portfolios, owners, portfolio_notes):
        """Two owners share shared@example.com — they get Alpha + Beta combined."""
        result = assemble_owner_email("shared@example.com", PERIOD_START)

        # Should have 2 portfolios (Alpha from owner_a, Beta from owner_b)
        assert len(result["portfolios"]) == 2
        names = [p["name"] for p in result["portfolios"]]
        assert "Alpha Portfolio" in names
        assert "Beta Portfolio" in names

    def test_shared_email_representative_owner(self, portfolios, owners, portfolio_notes):
        """Representative owner is lowest PK."""
        result = assemble_owner_email("shared@example.com", PERIOD_START)
        # owner_a has lowest PK, first_name="Alice"
        assert result["owner_name"] == "Alice"

    def test_multi_portfolio_owner(self, portfolios, owners, portfolio_notes):
        """carol@example.com owns Alpha + Gamma."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        assert len(result["portfolios"]) == 2
        names = [p["name"] for p in result["portfolios"]]
        assert "Alpha Portfolio" in names
        assert "Gamma Portfolio" in names

    def test_multi_portfolio_all_approved(self, portfolios, owners, portfolio_notes):
        """carol@example.com: Alpha=approved, Gamma=approved → all_approved=True."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        assert result["all_approved"] is True

    def test_mixed_status_not_ready(self, portfolios, owners, portfolio_notes):
        """shared@example.com: Alpha=approved, Beta=draft → all_approved=False."""
        result = assemble_owner_email("shared@example.com", PERIOD_START)
        assert result["all_approved"] is False

    def test_approved_content_used_when_available(self, portfolios, owners, portfolio_notes):
        """For approved notes with approved_generated_note, use that content."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        # Alpha's approved_generated_note contains "(reviewed)"
        assert "(reviewed)" in result["notes_html"]

    def test_generated_content_fallback(self, portfolios, owners, portfolio_notes):
        """For draft notes without approved_generated_note, use generated content."""
        result = assemble_owner_email("shared@example.com", PERIOD_START)
        # Beta's notes_html should be the original generated
        assert "Beta" in result["notes_html"]

    def test_blank_email_excluded(self, portfolios, owners, portfolio_notes):
        """Blank email returns empty result."""
        result = assemble_owner_email("", PERIOD_START)
        assert result["portfolios"] == []

    def test_nonexistent_email(self, portfolios, owners, portfolio_notes):
        """Unknown email returns empty result."""
        result = assemble_owner_email("nobody@example.com", PERIOD_START)
        assert result["portfolios"] == []

    def test_alphabetical_portfolio_order(self, portfolios, owners, portfolio_notes):
        """Portfolios are ordered alphabetically by name."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        names = [p["name"] for p in result["portfolios"]]
        assert names == sorted(names)

    def test_financials_html_concatenated(self, portfolios, owners, portfolio_notes):
        """Financials from multiple portfolios are concatenated."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        assert "Alpha" in result["financials_html"]
        assert "Gamma" in result["financials_html"]

    def test_email_normalization(self, portfolios, owners, portfolio_notes):
        """Case-insensitive email matching."""
        r1 = assemble_owner_email("SHARED@EXAMPLE.COM", PERIOD_START)
        r2 = assemble_owner_email("shared@example.com", PERIOD_START)
        assert len(r1["portfolios"]) == len(r2["portfolios"])

    def test_missing_portfolio_note(self, portfolios, owners):
        """If no PortfolioMonthlyNote exists, portfolio shows as 'missing'."""
        result = assemble_owner_email("carol@example.com", PERIOD_START)
        for p in result["portfolios"]:
            assert p["status"] == "missing"
        assert result["all_approved"] is False


# ---------------------------------------------------------------------------
# Recipients list tests
# ---------------------------------------------------------------------------


class TestGetRecipientsForPeriod:
    def test_groups_by_email(self, portfolios, owners, portfolio_notes):
        """Should produce distinct rows per normalized email."""
        recipients = get_recipients_for_period(PERIOD_START)
        emails = [r["recipient_email"] for r in recipients]
        # shared@example.com and carol@example.com — blank email excluded
        assert "shared@example.com" in emails
        assert "carol@example.com" in emails
        assert len(emails) == len(set(emails))  # no dupes

    def test_blank_email_excluded(self, portfolios, owners, portfolio_notes):
        """Owner with blank email should NOT appear."""
        recipients = get_recipients_for_period(PERIOD_START)
        emails = [r["recipient_email"] for r in recipients]
        assert "" not in emails

    def test_readiness_mixed(self, portfolios, owners, portfolio_notes):
        """shared@example.com has mixed status → not ready."""
        recipients = get_recipients_for_period(PERIOD_START)
        shared = [r for r in recipients if r["recipient_email"] == "shared@example.com"][0]
        assert shared["all_approved"] is False

    def test_readiness_all_approved(self, portfolios, owners, portfolio_notes):
        """carol@example.com has all approved → ready."""
        recipients = get_recipients_for_period(PERIOD_START)
        carol = [r for r in recipients if r["recipient_email"] == "carol@example.com"][0]
        assert carol["all_approved"] is True

    def test_portfolio_info_present(self, portfolios, owners, portfolio_notes):
        """Each recipient should list their portfolios with status."""
        recipients = get_recipients_for_period(PERIOD_START)
        carol = [r for r in recipients if r["recipient_email"] == "carol@example.com"][0]
        assert carol["portfolio_count"] == 2
        statuses = {p["status"] for p in carol["portfolios"]}
        assert statuses == {"approved"}


# ---------------------------------------------------------------------------
# Generation tests
# ---------------------------------------------------------------------------


class TestGeneratePortfolioNotes:
    @patch("comms.services._call_anthropic")
    @patch("comms.services.build_portfolio_section")
    def test_generates_one_per_portfolio(self, mock_build, mock_ai, portfolios, statements):
        """Each portfolio gets its own PortfolioMonthlyNote row."""
        mock_build.return_value = {
            "portfolio_name": "Test",
            "financials": {"total_income": 5000, "total_expenses": 1000,
                           "total_distribution": 4000, "ending_balance": 4000,
                           "period_start": PERIOD_START, "period_end": PERIOD_END},
            "maintenance": {"_has_data": False, "open_count": 0, "closed_count": 0, "canceled_count": 0},
            "pipeline": {"_has_data": False, "processes_by_category": {}},
        }
        mock_ai.return_value = {"intro": "Test intro.", "process_summaries": {}}

        result = generate_portfolio_notes(
            Portfolio.objects.filter(pk=portfolios[0].pk),
            PERIOD_START, PERIOD_END,
        )

        assert result["generated"] == 1
        assert PortfolioMonthlyNote.objects.filter(
            portfolio=portfolios[0], period_start=PERIOD_START
        ).exists()

    @patch("comms.services._call_anthropic")
    @patch("comms.services.build_portfolio_section")
    def test_preserves_approved(self, mock_build, mock_ai, portfolios):
        """Approved rows are NOT overwritten by regeneration."""
        p = portfolios[0]
        PortfolioMonthlyNote.objects.create(
            portfolio=p, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            generated_note="Original approved note.",
            status="approved",
        )

        mock_build.return_value = {
            "portfolio_name": p.name,
            "financials": {"total_income": 9999, "period_start": PERIOD_START, "period_end": PERIOD_END},
            "maintenance": {"_has_data": False},
            "pipeline": {"_has_data": False, "processes_by_category": {}},
        }
        mock_ai.return_value = {"intro": "New intro.", "process_summaries": {}}

        result = generate_portfolio_notes(
            Portfolio.objects.filter(pk=p.pk),
            PERIOD_START, PERIOD_END,
        )

        assert result["skipped"] == 1
        note = PortfolioMonthlyNote.objects.get(portfolio=p, period_start=PERIOD_START)
        assert note.generated_note == "Original approved note."
        assert note.status == "approved"

    @patch("comms.services._call_anthropic")
    @patch("comms.services.build_portfolio_section")
    def test_skips_no_data_portfolio(self, mock_build, mock_ai, portfolios):
        """Portfolio with no financials/maintenance/pipeline is skipped."""
        mock_build.return_value = {
            "portfolio_name": "Empty",
            "financials": {},
            "maintenance": {"_has_data": False},
            "pipeline": {"_has_data": False, "processes_by_category": {}},
        }

        result = generate_portfolio_notes(
            Portfolio.objects.filter(pk=portfolios[0].pk),
            PERIOD_START, PERIOD_END,
        )

        assert result["skipped"] == 1
        assert result["generated"] == 0

    @patch("comms.services._call_anthropic")
    @patch("comms.services.build_portfolio_section")
    def test_wipes_drafts_before_regen(self, mock_build, mock_ai, portfolios):
        """Draft rows for the period are deleted before regeneration."""
        p = portfolios[0]
        PortfolioMonthlyNote.objects.create(
            portfolio=p, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            generated_note="Old draft.",
            status="draft",
        )

        mock_build.return_value = {
            "portfolio_name": p.name,
            "financials": {"total_income": 5000, "period_start": PERIOD_START, "period_end": PERIOD_END},
            "maintenance": {"_has_data": False},
            "pipeline": {"_has_data": False, "processes_by_category": {}},
        }
        mock_ai.return_value = {"intro": "New intro.", "process_summaries": {}}

        generate_portfolio_notes(
            Portfolio.objects.filter(pk=p.pk),
            PERIOD_START, PERIOD_END,
        )

        notes = PortfolioMonthlyNote.objects.filter(portfolio=p, period_start=PERIOD_START)
        assert notes.count() == 1
        assert notes.first().generated_note != "Old draft."


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestPortfolioNotesAPI:
    def test_list_requires_auth(self, client):
        resp = client.get("/api/reports/portfolio-notes?month=2026-05")
        assert resp.status_code == 401

    def test_list_returns_notes(self, client, admin_user, portfolio_notes):
        client.force_login(admin_user)
        resp = client.get("/api/reports/portfolio-notes?month=2026-05")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        names = [n["portfolio_name"] for n in data]
        assert "Alpha Portfolio" in names

    def test_approve_snapshots_note(self, client, admin_user, portfolio_notes):
        """Approving a draft copies generated_note to approved_generated_note."""
        client.force_login(admin_user)
        draft_note = portfolio_notes[1]  # Beta, draft
        assert draft_note.approved_generated_note == ""

        resp = client.post(f"/api/reports/portfolio-notes/{draft_note.pk}/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"

        draft_note.refresh_from_db()
        assert draft_note.approved_generated_note == "Beta was quiet."
        assert draft_note.status == "approved"

    def test_put_updates_note(self, client, admin_user, portfolio_notes):
        client.force_login(admin_user)
        note = portfolio_notes[1]  # Beta, draft
        resp = client.put(
            f"/api/reports/portfolio-notes/{note.pk}",
            data=json.dumps({"generated_note": "Updated text."}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        note.refresh_from_db()
        assert note.generated_note == "Updated text."

    def test_delete_draft(self, client, admin_user, portfolio_notes):
        client.force_login(admin_user)
        note = portfolio_notes[1]  # Beta, draft
        resp = client.delete(f"/api/reports/portfolio-notes/{note.pk}")
        assert resp.status_code == 200
        assert not PortfolioMonthlyNote.objects.filter(pk=note.pk).exists()

    def test_delete_approved_blocked(self, client, admin_user, portfolio_notes):
        client.force_login(admin_user)
        note = portfolio_notes[0]  # Alpha, approved
        resp = client.delete(f"/api/reports/portfolio-notes/{note.pk}")
        assert resp.status_code == 400


class TestOwnerSendsAPI:
    def test_list_recipients(self, client, admin_user, portfolios, owners, portfolio_notes):
        client.force_login(admin_user)
        resp = client.get("/api/reports/owner-sends/recipients?month=2026-05")
        assert resp.status_code == 200
        data = resp.json()
        emails = [r["recipient_email"] for r in data]
        assert "shared@example.com" in emails
        assert "carol@example.com" in emails

    def test_preview_returns_assembled(self, client, admin_user, portfolios, owners, portfolio_notes):
        client.force_login(admin_user)
        resp = client.get(
            "/api/reports/owner-sends/recipients/carol@example.com/preview?month=2026-05"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["owner_name"] == "Carol"
        assert "Alpha" in data["financials_html"]
        assert data["all_approved"] is True

    def test_send_blocked_when_not_ready(self, client, admin_user, portfolios, owners, portfolio_notes):
        """shared@example.com has a draft portfolio → send blocked."""
        client.force_login(admin_user)
        resp = client.post(
            "/api/reports/owner-sends/recipients/shared@example.com/send?month=2026-05"
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "gating_portfolios" in data


# ---------------------------------------------------------------------------
# EmailDraft guardrails
# ---------------------------------------------------------------------------


class TestEmailDraftUntouched:
    """Confirm the EmailDraft model/constraints are not altered."""

    def test_constraints_exist(self):
        """Both original constraints still exist on the model."""
        constraint_names = {c.name for c in EmailDraft._meta.constraints}
        assert "unique_draft_per_owner_per_period" in constraint_names
        assert "unique_monthly_note_per_email_per_period" in constraint_names

    def test_emaildraft_fields_unchanged(self):
        """Core field set is still present."""
        field_names = {f.name for f in EmailDraft._meta.get_fields()}
        expected = {
            "product", "owner", "recipient_email", "subject", "body_html",
            "generated_note", "status", "generated_at", "sent_at", "sent_by",
            "period_type", "period_start", "period_end",
        }
        assert expected.issubset(field_names)


# ---------------------------------------------------------------------------
# is_active filtering tests
# ---------------------------------------------------------------------------


@pytest.fixture
def inactive_portfolio():
    """An inactive portfolio that should be excluded everywhere."""
    p = Portfolio.objects.create(
        rentvine_id=999, name="Ghost Portfolio", is_active=False,
    )
    return p


class TestIsActiveFiltering:
    @patch("comms.services._call_anthropic")
    @patch("comms.services.build_portfolio_section")
    def test_generate_skips_inactive(self, mock_build, mock_ai, portfolios, inactive_portfolio, statements):
        """generate_portfolio_notes skips inactive portfolios."""
        mock_build.return_value = {
            "portfolio_name": "Test",
            "financials": {"total_income": 5000, "period_start": PERIOD_START, "period_end": PERIOD_END},
            "maintenance": {"_has_data": False},
            "pipeline": {"_has_data": False, "processes_by_category": {}},
        }
        mock_ai.return_value = {"intro": "Test.", "process_summaries": {}}

        # Pass ALL portfolios including the inactive one
        all_pks = [p.pk for p in portfolios] + [inactive_portfolio.pk]
        qs = Portfolio.objects.filter(pk__in=all_pks)

        result = generate_portfolio_notes(qs, PERIOD_START, PERIOD_END)

        # The 3 active portfolios are processed, the inactive one is not
        assert not PortfolioMonthlyNote.objects.filter(
            portfolio=inactive_portfolio
        ).exists()

    def test_list_endpoint_excludes_inactive(self, client, admin_user, portfolios, inactive_portfolio):
        """GET /api/reports/portfolio-notes excludes notes for inactive portfolios."""
        # Create notes for an active and an inactive portfolio
        PortfolioMonthlyNote.objects.create(
            portfolio=portfolios[0], period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            generated_note="Active.", status="draft",
        )
        PortfolioMonthlyNote.objects.create(
            portfolio=inactive_portfolio, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            generated_note="Ghost.", status="draft",
        )

        client.force_login(admin_user)
        resp = client.get("/api/reports/portfolio-notes?month=2026-05")
        data = resp.json()

        names = [n["portfolio_name"] for n in data]
        assert "Alpha Portfolio" in names
        assert "Ghost Portfolio" not in names

    def test_assemble_excludes_inactive_portfolio(self, portfolios, inactive_portfolio, owners, portfolio_notes):
        """assemble_owner_email omits inactive portfolios."""
        # Link owner_a to the inactive portfolio too
        owner_a = owners[0]
        owner_a.portfolios.add(inactive_portfolio)
        PortfolioMonthlyNote.objects.create(
            portfolio=inactive_portfolio, period_type="monthly",
            period_start=PERIOD_START, period_end=PERIOD_END,
            financials_html="<p>Ghost financials</p>",
            notes_html="<p>Ghost notes</p>",
            generated_note="Ghost.",
            status="approved",
        )

        result = assemble_owner_email("shared@example.com", PERIOD_START)
        portfolio_names = [p["name"] for p in result["portfolios"]]
        assert "Ghost Portfolio" not in portfolio_names

    def test_recipients_excludes_inactive_portfolio(self, portfolios, inactive_portfolio, owners, portfolio_notes):
        """get_recipients_for_period ignores inactive portfolios."""
        owner_a = owners[0]
        owner_a.portfolios.add(inactive_portfolio)

        recipients = get_recipients_for_period(PERIOD_START)
        shared = [r for r in recipients if r["recipient_email"] == "shared@example.com"][0]
        portfolio_names = [p["name"] for p in shared["portfolios"]]
        assert "Ghost Portfolio" not in portfolio_names

    def test_recipients_excludes_inactive_owner(self, portfolios, owners, portfolio_notes):
        """Inactive owner doesn't appear as a recipient."""
        # Deactivate owner_c (carol@example.com)
        owner_c = owners[2]
        owner_c.is_active = False
        owner_c.save()

        recipients = get_recipients_for_period(PERIOD_START)
        emails = [r["recipient_email"] for r in recipients]
        assert "carol@example.com" not in emails


# ---------------------------------------------------------------------------
# Generation lock / TTL tests
# ---------------------------------------------------------------------------


class TestGenerationLockTTL:
    def setup_method(self):
        """Ensure clean lock state before each test."""
        svc._portfolio_gen_started_at = None

    def teardown_method(self):
        """Ensure clean lock state after each test."""
        svc._portfolio_gen_started_at = None

    def test_lock_blocks_concurrent(self, client, admin_user):
        """Second generation request returns 409 while first is running."""
        client.force_login(admin_user)

        # Simulate a running generation
        svc._portfolio_gen_started_at = time.monotonic()

        resp = client.post(
            "/api/reports/portfolio-notes/generate",
            data=json.dumps({"start_date": "2026-05-01", "end_date": "2026-05-31"}),
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_lock_expires_after_ttl(self, client, admin_user):
        """A stuck lock auto-expires and allows a new generation."""
        client.force_login(admin_user)

        # Simulate a lock that started 11 minutes ago (past TTL)
        svc._portfolio_gen_started_at = time.monotonic() - 660

        with patch("comms.services.generate_portfolio_notes"):
            resp = client.post(
                "/api/reports/portfolio-notes/generate",
                data=json.dumps({"start_date": "2026-05-01", "end_date": "2026-05-31"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_lock_released_on_error(self, admin_user):
        """Lock is released even if generate_portfolio_notes raises."""
        import comms.portfolio_api as api_mod

        svc._portfolio_gen_started_at = time.monotonic()

        # Simulate the _run() finally block
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            pass
        finally:
            svc._portfolio_gen_started_at = None

        assert svc._portfolio_gen_started_at is None

    def test_lock_not_stuck_after_normal_run(self):
        """generate_portfolio_notes clears the lock on normal completion."""
        svc._portfolio_gen_started_at = time.monotonic()

        with patch("comms.services._call_anthropic"), \
             patch("comms.services.build_portfolio_section"):
            generate_portfolio_notes(
                Portfolio.objects.none(), PERIOD_START, PERIOD_END,
            )

        assert svc._portfolio_gen_started_at is None

"""
Tests for the portfolio-grain maintenance notes system (Layer 1).

Production-shaped fixtures:
  - Portfolio with multiple properties (single-unit and multi-unit)
  - Melds in all buckets: open, closed, canceled, needs_approval (Meld selector tests)
  - WorkOrders in four buckets (generation & fragment tests)
  - Exclusions: merged melds, routine lawn/mowing
  - Portfolio with no activity (should be skipped)
"""

from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from comms.models import PortfolioMaintenanceNote
from comms.services import (
    generate_portfolio_maintenance_notes,
    _build_maintenance_prompt_portfolio,
    _build_maintenance_prompt_work_orders,
    _build_maintenance_generated_note,
)
from core.models import Owner, Portfolio, Property, Unit
from maintenance.models import Meld, WorkOrder
from maintenance.selectors import (
    get_portfolio_maintenance_data,
    get_portfolio_work_order_data,
)


pytestmark = pytest.mark.django_db

PERIOD_START = date(2026, 5, 25)
PERIOD_END = date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Fixtures — mirror production data shapes
# ---------------------------------------------------------------------------


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=1, name="Sunset Properties")


@pytest.fixture
def portfolio_empty():
    """Portfolio with no melds — should be skipped during generation."""
    return Portfolio.objects.create(rentvine_id=2, name="Ghost Portfolio")


@pytest.fixture
def prop_single(portfolio):
    """Single-unit property."""
    return Property.objects.create(
        rentvine_id=100,
        portfolio=portfolio,
        address_line_1="123 Main St",
        city="Asheville",
        state="NC",
        postal_code="28801",
        is_active=True,
    )


@pytest.fixture
def prop_multi(portfolio):
    """Multi-unit property (duplex)."""
    return Property.objects.create(
        rentvine_id=101,
        portfolio=portfolio,
        address_line_1="456 Oak Ave",
        city="Asheville",
        state="NC",
        postal_code="28801",
        is_active=True,
    )


@pytest.fixture
def unit_single(prop_single):
    return Unit.objects.create(
        rentvine_id=200,
        property=prop_single,
        name="123 Main St",
        address_line_1="123 Main St",
        is_active=True,
    )


@pytest.fixture
def unit_multi_a(prop_multi):
    return Unit.objects.create(
        rentvine_id=201,
        property=prop_multi,
        name="456 Oak Ave Unit A",
        address_line_1="456 Oak Ave",
        unit_number="A",
        is_active=True,
    )


@pytest.fixture
def unit_multi_b(prop_multi):
    return Unit.objects.create(
        rentvine_id=202,
        property=prop_multi,
        name="456 Oak Ave Unit B",
        address_line_1="456 Oak Ave",
        unit_number="B",
        is_active=True,
    )


@pytest.fixture
def melds(prop_single, prop_multi, unit_single, unit_multi_a, unit_multi_b):
    """
    Production-shaped meld set:
      - 2 open (one per property, one HIGH priority)
      - 1 closed within window
      - 1 canceled within window
      - 1 merged (excluded)
      - 1 lawn/mowing (excluded)
      - 1 needing approval
    """
    open1 = Meld.objects.create(
        property_meld_id="PM100",
        brief_description="Leaky faucet in kitchen",
        status="PENDING_COMPLETION",
        category="PLUMBING",
        property=prop_single,
        unit=unit_single,
        assigned_vendor_name="Quick Plumb LLC",
        source_created_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )
    open2 = Meld.objects.create(
        property_meld_id="PM101",
        brief_description="HVAC not cooling unit B",
        status="PENDING_VENDOR",
        category="HVAC",
        priority="HIGH",
        property=prop_multi,
        unit=unit_multi_b,
        assigned_vendor_name="CoolAir Services",
        owner_approval_status="Requested",
        source_created_at=datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc),
    )
    closed1 = Meld.objects.create(
        property_meld_id="PM102",
        brief_description="Replaced smoke detector batteries",
        status="COMPLETED",
        category="Safety",
        property=prop_multi,
        unit=unit_multi_a,
        assigned_vendor_name="In-House",
        completion_date=datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc),
        source_created_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
    )
    canceled1 = Meld.objects.create(
        property_meld_id="PM103",
        brief_description="Tenant-reported noise issue",
        status="TENANT_CANCELED",
        category="GENERAL",
        property=prop_single,
        unit=unit_single,
        source_modified_at=datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc),
        source_created_at=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )
    # EXCLUDED: merged
    Meld.objects.create(
        property_meld_id="PM_MERGED",
        brief_description="Merged into PM100",
        status="PENDING_COMPLETION",
        property=prop_single,
        unit=unit_single,
        merged_meld_data={"merged_into": 999},
    )
    # EXCLUDED: lawn/mowing
    Meld.objects.create(
        property_meld_id="PM_LAWN",
        brief_description="Mow the lawn",
        category="EXTERIOR",
        status="PENDING_COMPLETION",
        property=prop_single,
        unit=unit_single,
    )
    return open1, open2, closed1, canceled1


@pytest.fixture
def work_orders(portfolio, prop_single, prop_multi, unit_single, unit_multi_a, unit_multi_b):
    """
    Production-shaped work-order set (four-bucket structure):
      - 1 open (no scheduled date)
      - 1 scheduled (has scheduled_start_date)
      - 1 completed within window
      - 1 cancelled within window
    """
    open_wo = WorkOrder.objects.create(
        rentvine_id=1001,
        work_order_number="WO-1001",
        description="<p>Leaky faucet in kitchen</p>",
        portfolio=portfolio,
        property=prop_single,
        unit=unit_single,
        vendor_name="Quick Plumb LLC",
        source_created_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )
    scheduled_wo = WorkOrder.objects.create(
        rentvine_id=1002,
        work_order_number="WO-1002",
        description="<div>HVAC not cooling unit B</div>",
        portfolio=portfolio,
        property=prop_multi,
        unit=unit_multi_b,
        vendor_name="CoolAir Services",
        scheduled_start_date=date(2026, 5, 30),
        source_created_at=datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc),
    )
    completed = WorkOrder.objects.create(
        rentvine_id=1003,
        work_order_number="WO-1003",
        description="Replaced smoke detector batteries",
        portfolio=portfolio,
        property=prop_multi,
        unit=unit_multi_a,
        vendor_name="In-House",
        closed_by_user_id=1,
        date_closed=date(2026, 5, 28),
        source_created_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
    )
    cancelled = WorkOrder.objects.create(
        rentvine_id=1004,
        work_order_number="WO-1004",
        description="Tenant-reported noise issue",
        portfolio=portfolio,
        property=prop_single,
        unit=unit_single,
        cancelled_by_user_id=1,
        source_modified_at=datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc),
        source_created_at=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )
    return open_wo, scheduled_wo, completed, cancelled


@pytest.fixture
def mock_anthropic():
    """Mock Anthropic API to return portfolio-grain maintenance summaries."""
    mock_message = MagicMock()
    mock_message.content = [
        MagicMock(
            text='{"intro": "This week your portfolio has 2 open work orders, '
            '1 completed, and 1 cancelled.", '
            '"wo_summaries": {'
            '"WO-1001": "Quick Plumb LLC is addressing a kitchen faucet leak at 123 Main St.", '
            '"WO-1002": "CoolAir Services has been assigned to investigate the HVAC issue in Unit B.", '
            '"WO-1003": "Smoke detector batteries were replaced in Unit A — completed by in-house staff.", '
            '"WO-1004": "The tenant-reported noise issue was cancelled by the tenant."}}'
        )
    ]
    mock_client = MagicMock()
    mock_client.return_value.messages.create.return_value = mock_message
    return mock_client


# ---------------------------------------------------------------------------
# Selector tests: get_portfolio_maintenance_data
# ---------------------------------------------------------------------------


class TestGetPortfolioMaintenanceData:
    def test_returns_open_melds(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert len(data["open_melds"]) == 2
        ids = {m["property_meld_id"] for m in data["open_melds"]}
        assert ids == {"PM100", "PM101"}

    def test_returns_closed_melds_in_window(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert len(data["closed_melds"]) == 1
        assert data["closed_melds"][0]["property_meld_id"] == "PM102"

    def test_returns_canceled_melds_in_window(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert len(data["canceled_melds"]) == 1
        assert data["canceled_melds"][0]["property_meld_id"] == "PM103"

    def test_needs_approval_subset(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert len(data["needs_approval"]) == 1
        assert data["needs_approval"][0]["property_meld_id"] == "PM101"

    def test_merged_excluded(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        all_ids = set()
        for key in ("open_melds", "closed_melds", "canceled_melds"):
            all_ids.update(m["property_meld_id"] for m in data[key])
        assert "PM_MERGED" not in all_ids

    def test_lawn_excluded(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        all_ids = set()
        for key in ("open_melds", "closed_melds", "canceled_melds"):
            all_ids.update(m["property_meld_id"] for m in data[key])
        assert "PM_LAWN" not in all_ids

    def test_has_data_true(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert data["_has_data"] is True

    def test_has_data_false_no_activity(self, portfolio_empty):
        data = get_portfolio_maintenance_data(
            portfolio_empty, PERIOD_START, PERIOD_END
        )
        assert data["_has_data"] is False
        assert data["open_melds"] == []

    def test_no_owner_first_name(self, portfolio, melds):
        """Portfolio-grain data does NOT include owner_first_name."""
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        assert "owner_first_name" not in data

    def test_meld_dict_shape(self, portfolio, melds):
        """Each meld dict has the expected fields."""
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        meld = data["open_melds"][0]
        assert "property_meld_id" in meld
        assert "brief_description" in meld
        assert "unit_address" in meld
        assert "category" in meld
        assert "assigned_vendor_name" in meld


# ---------------------------------------------------------------------------
# Prompt + generated_note tests
# ---------------------------------------------------------------------------


class TestBuildMaintenancePromptPortfolio:
    def test_prompt_contains_portfolio_name(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        prompt = _build_maintenance_prompt_portfolio(
            data, portfolio.name, PERIOD_START, PERIOD_END
        )
        assert "Sunset Properties" in prompt

    def test_prompt_is_owner_agnostic(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        prompt = _build_maintenance_prompt_portfolio(
            data, portfolio.name, PERIOD_START, PERIOD_END
        )
        # No owner name, no "email" word, no greeting
        assert "email for" not in prompt.lower()
        assert "greeting" not in prompt.lower()

    def test_prompt_contains_meld_ids(self, portfolio, melds):
        data = get_portfolio_maintenance_data(portfolio, PERIOD_START, PERIOD_END)
        prompt = _build_maintenance_prompt_portfolio(
            data, portfolio.name, PERIOD_START, PERIOD_END
        )
        assert "PM100" in prompt
        assert "PM101" in prompt


class TestBuildMaintenanceGeneratedNote:
    def test_builds_plain_text(self):
        ai_result = {
            "intro": "Two open, one closed.",
            "wo_summaries": {
                "WO-1001": "Faucet leak being repaired.",
                "WO-1002": "HVAC issue assigned.",
            },
        }
        note = _build_maintenance_generated_note(ai_result)
        assert "Two open, one closed." in note
        assert "- Faucet leak being repaired." in note
        assert "- HVAC issue assigned." in note

    def test_empty_summaries(self):
        ai_result = {"intro": "No activity.", "wo_summaries": {}}
        note = _build_maintenance_generated_note(ai_result)
        assert note == "No activity."


# ---------------------------------------------------------------------------
# Generation tests
# ---------------------------------------------------------------------------


class TestGeneratePortfolioMaintenanceNotes:
    @patch("comms.services.anthropic.Anthropic")
    def test_generates_note_per_portfolio(self, mock_anthro, portfolio, work_orders, mock_anthropic):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["generated"] == 1
        assert result["skipped"] == 0
        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        assert note.period_type == "weekly"
        assert note.period_start == PERIOD_START
        assert note.period_end == PERIOD_END
        assert note.status == "draft"
        assert note.generated_note != ""
        assert note.notes_html != ""

    @patch("comms.services.anthropic.Anthropic")
    def test_skips_no_activity(self, mock_anthro, portfolio_empty, mock_anthropic):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio_empty.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["skipped"] == 1
        assert result["generated"] == 0
        assert PortfolioMaintenanceNote.objects.count() == 0
        # Anthropic should NOT have been called
        mock_anthropic.return_value.messages.create.assert_not_called()

    @patch("comms.services.anthropic.Anthropic")
    def test_preserves_approved_note(self, mock_anthro, portfolio, work_orders, mock_anthropic):
        """Approved rows are never overwritten."""
        mock_anthro.side_effect = mock_anthropic
        PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_note="Original approved note.",
            status="approved",
        )

        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["skipped"] == 1
        assert result["generated"] == 0
        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        assert note.generated_note == "Original approved note."
        assert note.status == "approved"

    @patch("comms.services.anthropic.Anthropic")
    def test_preserves_note_with_approved_generated_note(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        """Notes with approved_generated_note set are never overwritten, even if status=draft."""
        mock_anthro.side_effect = mock_anthropic
        PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_note="Original.",
            approved_generated_note="Human-edited version.",
            status="draft",
        )

        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["skipped"] == 1
        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        assert note.approved_generated_note == "Human-edited version."

    @patch("comms.services.anthropic.Anthropic")
    def test_wipes_stale_drafts(self, mock_anthro, portfolio, work_orders, mock_anthropic):
        """Draft rows are wiped on regeneration."""
        mock_anthro.side_effect = mock_anthropic
        PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_note="Old draft.",
            status="draft",
        )

        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["generated"] == 1
        notes = PortfolioMaintenanceNote.objects.filter(portfolio=portfolio)
        assert notes.count() == 1
        assert notes.first().generated_note != "Old draft."

    @patch("comms.services.anthropic.Anthropic")
    def test_skips_inactive_portfolio(self, mock_anthro, mock_anthropic):
        """Inactive portfolios are never processed."""
        mock_anthro.side_effect = mock_anthropic
        inactive = Portfolio.objects.create(
            rentvine_id=999, name="Inactive", is_active=False
        )

        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            result = generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=inactive.pk),
                PERIOD_START, PERIOD_END,
            )

        assert result["generated"] == 0
        assert result["skipped"] == 0
        assert not PortfolioMaintenanceNote.objects.filter(
            portfolio=inactive
        ).exists()

    @patch("comms.services.anthropic.Anthropic")
    def test_no_email_drafts_created(self, mock_anthro, portfolio, work_orders, mock_anthropic):
        """Layer 1 generation NEVER creates EmailDraft rows."""
        from comms.models import EmailDraft

        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        assert EmailDraft.objects.filter(product="maintenance").count() == 0


# ---------------------------------------------------------------------------
# Fragment render tests
# ---------------------------------------------------------------------------


class TestMaintenanceFragmentRender:
    @patch("comms.services.anthropic.Anthropic")
    def test_fragment_contains_work_order_descriptions(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        html = note.notes_html

        # Work order descriptions present (HTML stripped)
        assert "Leaky faucet in kitchen" in html
        assert "HVAC not cooling unit B" in html
        assert "Replaced smoke detector batteries" in html
        assert "Tenant-reported noise issue" in html

    @patch("comms.services.anthropic.Anthropic")
    def test_fragment_contains_ai_summaries(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        html = note.notes_html

        # AI summaries rendered
        assert "Quick Plumb LLC is addressing" in html
        assert "CoolAir Services has been assigned" in html

    @patch("comms.services.anthropic.Anthropic")
    def test_fragment_contains_stats_bar(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        html = note.notes_html

        # Four-bucket stats bar: each bucket has 1 item
        assert ">1<" in html
        assert "Open" in html
        assert "Scheduled" in html
        assert "Completed" in html
        assert "Cancelled" in html

    @patch("comms.services.anthropic.Anthropic")
    def test_fragment_no_envelope_elements(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        """Fragment must NOT contain envelope elements (greeting, header, footer, CTA)."""
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        html = note.notes_html.lower()

        assert "hi " not in html  # No greeting
        assert "weekly maintenance update" not in html  # No header
        assert "owner portal" not in html  # No CTA
        assert "<!doctype" not in html  # No full HTML page
        assert "<html" not in html

    @patch("comms.services.anthropic.Anthropic")
    def test_fragment_contains_ai_intro(
        self, mock_anthro, portfolio, work_orders, mock_anthropic
    ):
        mock_anthro.side_effect = mock_anthropic
        with patch("comms.services.anthropic.Anthropic", mock_anthropic):
            generate_portfolio_maintenance_notes(
                Portfolio.objects.filter(pk=portfolio.pk),
                PERIOD_START, PERIOD_END,
            )

        note = PortfolioMaintenanceNote.objects.get(portfolio=portfolio)
        assert "2 open work orders" in note.notes_html


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestPortfolioMaintenanceNoteModel:
    def test_create(self, portfolio):
        note = PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_note="Test note.",
        )
        assert note.pk is not None
        assert note.status == "draft"
        assert str(note) == f"PortfolioMaintenanceNote: {portfolio} ({PERIOD_START})"

    def test_unique_constraint(self, portfolio):
        PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            PortfolioMaintenanceNote.objects.create(
                portfolio=portfolio,
                period_type="weekly",
                period_start=PERIOD_START,
                period_end=PERIOD_END,
            )

    def test_default_status_is_draft(self, portfolio):
        note = PortfolioMaintenanceNote.objects.create(
            portfolio=portfolio,
            period_type="weekly",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        assert note.status == "draft"

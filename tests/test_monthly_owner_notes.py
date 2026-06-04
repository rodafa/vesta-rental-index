"""
Tests for the monthly_owner_notes comms product end-to-end.
"""

from datetime import date
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from comms.models import EmailDraft, VoiceGuide
from comms.registry import PRODUCTS
from comms.services import generate_drafts
from core.models import Owner, Portfolio


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=1, name="Test Portfolio")


@pytest.fixture
def owner(portfolio):
    o = Owner.objects.create(
        rentvine_contact_id=1,
        name="Jane Doe",
        first_name="Jane",
        email="jane@example.com",
        is_active=True,
    )
    o.portfolios.add(portfolio)
    return o


def _mock_selector_data():
    """Return realistic monthly owner notes selector data."""
    return {
        "owner_first_name": "Jane",
        "processes_by_category": {
            "renewal": [
                {
                    "process_id": 101,
                    "name": "Lease Renewal - 456 Oak Ave",
                    "category": "renewal",
                    "stage_name": "Negotiation",
                    "stage_status": "active",
                    "created_at": "2026-04-15T10:00:00Z",
                    "closed_at": None,
                    "address": "456 Oak Ave",
                    "unit_number": "",
                },
            ],
            "move_out": [
                {
                    "process_id": 202,
                    "name": "Move Out - 789 Pine St Unit 3",
                    "category": "move_out",
                    "stage_name": "Scheduled",
                    "stage_status": "active",
                    "created_at": "2026-05-01T10:00:00Z",
                    "closed_at": None,
                    "address": "789 Pine St",
                    "unit_number": "3",
                },
            ],
        },
        "total_count": 2,
        "_has_data": True,
        "_degraded": False,
    }


@pytest.fixture
def mock_anthropic_response():
    """Mock Anthropic API returning monthly owner notes JSON."""
    mock_message = MagicMock()
    mock_message.content = [
        MagicMock(
            text='{"intro": "This month we processed 1 lease renewal and '
            'coordinated 1 move-out for your portfolio.", '
            '"process_summaries": {'
            '"101": "We are currently negotiating the lease renewal at 456 Oak Ave.", '
            '"202": "A move-out has been scheduled at 789 Pine St Unit 3."}}'
        )
    ]

    mock_client = MagicMock()
    mock_client.return_value.messages.create.return_value = mock_message
    return mock_client


@pytest.mark.django_db
class TestMonthlyOwnerNotesRegistry:
    def test_product_in_registry(self):
        assert "monthly_owner_notes" in PRODUCTS

    def test_registry_keys_present(self):
        config = PRODUCTS["monthly_owner_notes"]
        assert "selector" in config
        assert "prompt_builder" in config
        assert "context_builder" in config
        assert "voice_guide_product" in config
        assert "template" in config
        assert "subject_template" in config


@pytest.mark.django_db
class TestMonthlyOwnerNotesDraftGeneration:
    def test_creates_monthly_draft(self, owner, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            # Return real prompt/context builders, but mock the selector
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: _mock_selector_data()
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
        assert result["skipped"] == 0
        assert result["errors"] == 0

        draft = EmailDraft.objects.get(owner=owner)
        assert draft.product == "monthly_owner_notes"
        assert draft.period_type == "monthly"
        assert draft.period_start == date(2026, 5, 1)
        assert draft.period_end == date(2026, 5, 31)
        assert draft.status == "draft"
        assert "May 2026" in draft.subject
        assert "Owner Update" in draft.subject
        # Template rendered with AI content
        assert "Jane" in draft.body_html
        assert "lease renewal" in draft.body_html.lower()
        assert "move-out" in draft.body_html.lower() or "move out" in draft.body_html.lower()

    def test_ai_summaries_in_rendered_html(self, owner, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: _mock_selector_data()
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
            )

        draft = EmailDraft.objects.get(owner=owner)
        assert "negotiating the lease renewal at 456 Oak Ave" in draft.body_html
        assert "move-out has been scheduled" in draft.body_html

    def test_voice_guide_created(self, owner, mock_anthropic_response):
        assert VoiceGuide.objects.filter(product="monthly_owner_notes").count() == 0

        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: _mock_selector_data()
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
            )

        guide = VoiceGuide.objects.get(product="monthly_owner_notes")
        assert "monthly owner update" in guide.instructions.lower()
        assert "first person plural" in guide.instructions.lower()

    def test_degraded_skips(self, owner, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: {"_degraded": True, "_has_data": False}
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

        assert result["degraded"] == 1
        assert result["generated"] == 0
        assert EmailDraft.objects.count() == 0
        mock_anthropic_response.return_value.messages.create.assert_not_called()

    def test_no_data_skips(self, owner, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: {
                        "owner_first_name": "Jane",
                        "processes_by_category": {},
                        "total_count": 0,
                        "_has_data": False,
                        "_degraded": False,
                    }
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

        assert result["skipped"] == 1
        assert result["generated"] == 0
        assert EmailDraft.objects.count() == 0


@pytest.mark.django_db
class TestMonthlyDraftsManagementCommand:
    def test_command_with_limit(self, owner, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services.build_portfolio_section") as mock_section:
            # Mock portfolio section to return realistic data
            mock_section.return_value = {
                "portfolio_name": "Test Portfolio",
                "financials": {},
                "maintenance": {"_has_data": False, "open_count": 0, "closed_count": 0, "canceled_count": 0},
                "pipeline": {
                    "processes_by_category": _mock_selector_data()["processes_by_category"],
                    "total_count": 2,
                    "_has_data": True,
                    "_degraded": False,
                },
            }

            out = StringIO()
            call_command(
                "generate_monthly_drafts",
                period_start="2026-05-01",
                period_end="2026-05-31",
                limit=1,
                stdout=out,
            )

        output = out.getvalue()
        assert "generated=1" in output
        assert EmailDraft.objects.count() == 1
        draft = EmailDraft.objects.first()
        assert draft.period_type == "monthly"


@pytest.mark.django_db
class TestMonthlyPeriodLabel:
    def test_period_label_format(self, owner, mock_anthropic_response):
        """Monthly period label should be 'May 2026' not 'May 01 – May 31, 2026'."""
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_load:
            from comms.services import (
                _build_monthly_context,
                _build_monthly_prompt,
            )
            def fake_load(path):
                if "selector" in path:
                    return lambda *a: _mock_selector_data()
                if "prompt" in path:
                    return _build_monthly_prompt
                if "context" in path:
                    return _build_monthly_context
                raise ValueError(f"Unexpected path: {path}")
            mock_load.side_effect = fake_load

            generate_drafts(
                "monthly_owner_notes",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 1),
                date(2026, 5, 31),
                period_type="monthly",
            )

        draft = EmailDraft.objects.get(owner=owner)
        assert "May 2026" in draft.subject
        assert "Your May 2026 Owner Update" in draft.subject

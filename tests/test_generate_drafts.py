"""
Tests for the comms engine draft generation (Anthropic call mocked).
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from comms.models import EmailDraft, VoiceGuide
from comms.services import generate_drafts
from core.models import Owner, Portfolio, Property, Unit
from maintenance.models import Meld


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


@pytest.fixture
def prop(portfolio):
    return Property.objects.create(
        rentvine_id=100,
        portfolio=portfolio,
        address_line_1="456 Oak Ave",
        city="Asheville",
        state="NC",
        postal_code="28801",
        is_active=True,
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        rentvine_id=200,
        property=prop,
        name="456 Oak Ave",
        address_line_1="456 Oak Ave",
        is_active=True,
    )


@pytest.fixture
def open_meld(prop, unit):
    return Meld.objects.create(
        property_meld_id="PM100",
        brief_description="Broken window",
        status="PENDING_COMPLETION",
        category="Windows",
        property=prop,
        unit=unit,
        assigned_vendor_name="Glass Co",
        source_created_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def mock_anthropic_response():
    """Mock Anthropic API to return a valid JSON response."""
    mock_message = MagicMock()
    mock_message.content = [
        MagicMock(
            text='{"intro": "This week there is 1 open work order for your property.", '
            '"meld_summaries": {"PM100": "We have assigned Glass Co to repair a broken window at 456 Oak Ave."}}'
        )
    ]

    mock_client = MagicMock()
    mock_client.return_value.messages.create.return_value = mock_message
    return mock_client


@pytest.mark.django_db
class TestGenerateDrafts:
    def test_creates_email_draft(self, owner, open_meld, mock_anthropic_response):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["generated"] == 1
        assert result["skipped"] == 0
        assert result["degraded"] == 0
        assert result["errors"] == 0

        draft = EmailDraft.objects.get(owner=owner)
        assert draft.product == "maintenance"
        assert draft.status == "draft"
        assert draft.sent_at is None
        # Period fields populated
        assert draft.period_type == "weekly"
        assert draft.period_start == date(2026, 5, 25)
        assert draft.period_end == date(2026, 5, 31)
        assert "Weekly Maintenance Update" in draft.subject
        assert "May 25" in draft.subject
        assert "Jane" in draft.body_html
        assert "Broken window" in draft.body_html
        assert "Glass Co" in draft.body_html

    def test_skips_owner_with_no_melds(self, owner, mock_anthropic_response):
        """Owner has no melds — should be skipped, no API call."""
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["generated"] == 0
        assert result["skipped"] == 1
        assert EmailDraft.objects.count() == 0
        # Anthropic should not have been called
        mock_anthropic_response.return_value.messages.create.assert_not_called()

    def test_voice_guide_created_on_first_run(
        self, owner, open_meld, mock_anthropic_response
    ):
        assert VoiceGuide.objects.count() == 0
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )
        guide = VoiceGuide.objects.get(product="maintenance")
        assert "first person" in guide.instructions.lower()

    def test_ai_summary_in_rendered_html(
        self, owner, open_meld, mock_anthropic_response
    ):
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        draft = EmailDraft.objects.get(owner=owner)
        assert "Glass Co to repair a broken window" in draft.body_html
        assert "1 open work order" in draft.body_html

    def test_rerun_updates_existing_draft(
        self, owner, open_meld, mock_anthropic_response
    ):
        """Re-running generate for the same period updates, not duplicates."""
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )
        assert EmailDraft.objects.count() == 1
        first_id = EmailDraft.objects.first().pk

        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["generated"] == 1
        assert EmailDraft.objects.count() == 1
        assert EmailDraft.objects.first().pk == first_id

    def test_rerun_skips_sent_draft(
        self, owner, open_meld, mock_anthropic_response
    ):
        """Re-running generate skips an already-sent draft."""
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )
        draft = EmailDraft.objects.first()
        draft.status = "sent"
        draft.save(update_fields=["status"])

        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response):
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["generated"] == 0
        assert result["skipped"] == 1
        draft.refresh_from_db()
        assert draft.status == "sent"

    def test_unknown_product_raises(self):
        with pytest.raises(ValueError, match="Unknown product"):
            generate_drafts(
                "nonexistent",
                Owner.objects.none(),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

    def test_degraded_skips_owner(self, owner, mock_anthropic_response):
        """Selector returning _degraded=True skips the owner without calling AI."""
        degraded_data = {
            "owner_first_name": "Jane",
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "_has_data": True,
            "_degraded": True,
        }
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_sel:
            mock_sel.return_value = lambda *a: degraded_data
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["degraded"] == 1
        assert result["generated"] == 0
        assert result["skipped"] == 0
        assert EmailDraft.objects.count() == 0
        mock_anthropic_response.return_value.messages.create.assert_not_called()

    def test_has_data_false_skips_owner(self, owner, mock_anthropic_response):
        """Selector returning _has_data=False skips the owner without calling AI."""
        no_data = {
            "owner_first_name": "Jane",
            "open_melds": [],
            "closed_melds": [],
            "canceled_melds": [],
            "_has_data": False,
        }
        with patch("comms.services.anthropic.Anthropic", mock_anthropic_response), \
             patch("comms.services._load_selector") as mock_sel:
            mock_sel.return_value = lambda *a: no_data
            result = generate_drafts(
                "maintenance",
                Owner.objects.filter(pk=owner.pk),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

        assert result["skipped"] == 1
        assert result["degraded"] == 0
        assert result["generated"] == 0
        assert EmailDraft.objects.count() == 0
        mock_anthropic_response.return_value.messages.create.assert_not_called()

"""
Tests for the comms engine draft generation (Anthropic call mocked).

Note: maintenance generation has moved to portfolio-grain
(generate_portfolio_maintenance_notes) — see test_portfolio_maintenance_notes.py.
These tests validate generic generate_drafts behavior only.
"""

import pytest

from comms.services import generate_drafts


@pytest.mark.django_db
class TestGenerateDrafts:
    def test_unknown_product_raises(self):
        from datetime import date
        from core.models import Owner

        with pytest.raises(ValueError, match="Unknown product"):
            generate_drafts(
                "nonexistent",
                Owner.objects.none(),
                date(2026, 5, 25),
                date(2026, 5, 31),
            )

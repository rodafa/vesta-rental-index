"""
Tests for the maintenance email data selector.
"""

from datetime import date, datetime, timezone

import pytest

from core.models import Owner, Portfolio, Property, Unit
from maintenance.models import Meld
from maintenance.selectors import get_owner_maintenance_data


@pytest.fixture
def portfolio():
    return Portfolio.objects.create(rentvine_id=1, name="Test Portfolio")


@pytest.fixture
def owner(portfolio):
    o = Owner.objects.create(
        rentvine_contact_id=1, name="John Smith", first_name="John", is_active=True
    )
    o.portfolios.add(portfolio)
    return o


@pytest.fixture
def prop(portfolio):
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
def unit(prop):
    return Unit.objects.create(
        rentvine_id=200,
        property=prop,
        name="123 Main St",
        address_line_1="123 Main St",
        is_active=True,
    )


@pytest.mark.django_db
class TestGetOwnerMaintenanceData:
    def test_open_melds_returned(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="OPEN1",
            brief_description="Leaky faucet",
            status="PENDING_COMPLETION",
            property=prop,
            unit=unit,
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["open_melds"]) == 1
        assert data["open_melds"][0]["brief_description"] == "Leaky faucet"

    def test_closed_melds_within_window(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="CLOSED1",
            brief_description="Fixed AC",
            status="COMPLETED",
            property=prop,
            unit=unit,
            completion_date=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        )
        # Outside window
        Meld.objects.create(
            property_meld_id="CLOSED_OLD",
            brief_description="Old fix",
            status="COMPLETED",
            property=prop,
            unit=unit,
            completion_date=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["closed_melds"]) == 1
        assert data["closed_melds"][0]["property_meld_id"] == "CLOSED1"

    def test_canceled_melds_within_window(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="CANCEL1",
            brief_description="Canceled job",
            status="MANAGER_CANCELED",
            property=prop,
            unit=unit,
            source_modified_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["canceled_melds"]) == 1

    def test_needs_approval_subset(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="APPROVE1",
            brief_description="Needs approval",
            status="PENDING_VENDOR",
            owner_approval_status="Requested",
            property=prop,
            unit=unit,
        )
        Meld.objects.create(
            property_meld_id="NOAPPROVE1",
            brief_description="No approval needed",
            status="PENDING_VENDOR",
            owner_approval_status="Not Requested",
            property=prop,
            unit=unit,
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["needs_approval"]) == 1
        assert data["needs_approval"][0]["property_meld_id"] == "APPROVE1"

    def test_merged_melds_excluded(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="MERGED1",
            brief_description="Merged into another",
            status="PENDING_COMPLETION",
            property=prop,
            unit=unit,
            merged_meld_data={"merged_into": 999},
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["open_melds"]) == 0

    def test_lawn_melds_excluded(self, owner, prop, unit):
        Meld.objects.create(
            property_meld_id="LAWN1",
            brief_description="Mow the lawn",
            category="EXTERIOR",
            status="PENDING_COMPLETION",
            property=prop,
            unit=unit,
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["open_melds"]) == 0

    def test_property_only_meld_included(self, owner, prop):
        """Melds linked to property but not unit are included."""
        Meld.objects.create(
            property_meld_id="PROPONLY1",
            brief_description="Multi-unit issue",
            status="PENDING_ASSIGNMENT",
            property=prop,
            unit=None,
        )
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert len(data["open_melds"]) == 1

    def test_empty_when_no_portfolios(self):
        owner_no_port = Owner.objects.create(
            rentvine_contact_id=99, name="No Portfolios", is_active=True
        )
        data = get_owner_maintenance_data(
            owner_no_port, date(2026, 5, 25), date(2026, 5, 31)
        )
        assert data["open_melds"] == []
        assert data["closed_melds"] == []

    def test_owner_first_name(self, owner, prop, unit):
        data = get_owner_maintenance_data(owner, date(2026, 5, 25), date(2026, 5, 31))
        assert data["owner_first_name"] == "John"

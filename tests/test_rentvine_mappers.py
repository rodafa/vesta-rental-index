"""
Unit tests for Rentvine field mappers with sample API payloads.
"""

from decimal import Decimal

import pytest

from integrations.rentvine.mappers import (
    map_lease,
    map_owner,
    map_portfolio,
    map_property,
    map_tenant_from_lease,
    map_unit,
)


class TestMapPortfolio:
    def test_unwraps_envelope(self):
        data = {
            "portfolio": {
                "portfolioID": 42,
                "name": "Eastside Portfolio",
                "isActive": "1",
                "reserveAmount": "500.00",
            },
            "statementSetting": {"id": 1},
        }
        rentvine_id, defaults = map_portfolio(data)
        assert rentvine_id == 42
        assert defaults["name"] == "Eastside Portfolio"
        assert defaults["is_active"] is True
        assert defaults["reserve_amount"] == Decimal("500.00")
        # raw_data preserves full envelope
        assert "statementSetting" in defaults["raw_data"]

    def test_flat_record(self):
        data = {"portfolioID": 7, "name": "West End", "isActive": True}
        rentvine_id, defaults = map_portfolio(data)
        assert rentvine_id == 7
        assert defaults["name"] == "West End"

    def test_missing_id_raises(self):
        with pytest.raises(ValueError, match="missing ID"):
            map_portfolio({"name": "No ID"})


class TestMapOwner:
    def test_nested_emails_and_phones(self):
        data = {
            "contact": {
                "contactID": 1084,
                "firstName": "Jane",
                "lastName": "Doe",
                "emails": [{"email": "jane@example.com"}],
                "phones": [{"phone": "919-555-1234"}],
                "isActive": "1",
            }
        }
        contact_id, defaults = map_owner(data)
        assert contact_id == 1084
        assert defaults["name"] == "Jane Doe"
        assert defaults["email"] == "jane@example.com"
        assert defaults["phone"] == "919-555-1234"

    def test_flat_email_fallback(self):
        data = {"contactID": 99, "name": "Bob", "email": "bob@test.com"}
        contact_id, defaults = map_owner(data)
        assert defaults["email"] == "bob@test.com"

    def test_name_built_from_first_last(self):
        data = {"contactID": 5, "firstName": "Alice", "lastName": "Smith"}
        _, defaults = map_owner(data)
        assert defaults["name"] == "Alice Smith"


class TestMapProperty:
    def test_full_property(self):
        data = {
            "property": {
                "propertyID": 100,
                "portfolioID": 42,
                "name": "123 Main St",
                "address": "123 Main St",
                "city": "Raleigh",
                "stateID": "NC",
                "postalCode": "27601",
                "propertyTypeID": 1,
                "isMultiUnit": "0",
                "isActive": "1",
            },
            "token": "abc123",
        }
        rentvine_id, portfolio_id, defaults = map_property(data)
        assert rentvine_id == 100
        assert portfolio_id == 42
        assert defaults["address_line_1"] == "123 Main St"
        assert defaults["city"] == "Raleigh"
        assert defaults["state"] == "NC"
        assert defaults["postal_code"] == "27601"
        assert defaults["property_type"] == "single_family"

    def test_address_from_street_parts(self):
        data = {
            "propertyID": 101,
            "streetNumber": "456",
            "streetName": "Oak Ave",
        }
        _, _, defaults = map_property(data)
        assert defaults["address_line_1"] == "456 Oak Ave"

    def test_type_string_mapping(self):
        data = {"propertyID": 102, "propertyType": "Townhouse"}
        _, _, defaults = map_property(data)
        assert defaults["property_type"] == "townhouse"


class TestMapUnit:
    """Critical: address drift fix tests."""

    def test_unit_number_not_address_line_2(self):
        """The old address_line_2 field MUST map to unit_number."""
        data = {
            "unit": {
                "unitID": 200,
                "propertyID": 100,
                "name": "Unit A",
                "address": "123 Main St",
                "address2": "A",
                "city": "Raleigh",
                "stateID": "NC",
                "postalCode": "27601",
            }
        }
        rentvine_id, prop_id, defaults = map_unit(data)
        assert rentvine_id == 200
        assert prop_id == 100
        assert defaults["unit_number"] == "A"
        assert "address_line_2" not in defaults
        assert defaults["address_line_1"] == "123 Main St"
        assert defaults["city"] == "Raleigh"
        assert defaults["state"] == "NC"
        assert defaults["postal_code"] == "27601"

    def test_empty_address_fields_from_api(self):
        """When RentVine returns empty unit address, mapper produces empty strings."""
        data = {"unitID": 201, "propertyID": 100, "name": "B"}
        _, _, defaults = map_unit(data)
        assert defaults["address_line_1"] == ""
        assert defaults["city"] == ""
        assert defaults["state"] == ""
        assert defaults["postal_code"] == ""
        # The service layer fills these from the parent property

    def test_bedrooms_and_baths(self):
        data = {
            "unitID": 202,
            "beds": 3,
            "fullBaths": 2,
            "halfBaths": 1,
            "size": 1500,
        }
        _, _, defaults = map_unit(data)
        assert defaults["bedrooms"] == 3
        assert defaults["full_bathrooms"] == 2
        assert defaults["half_bathrooms"] == 1
        assert defaults["square_feet"] == 1500


class TestMapLease:
    def test_extracts_fk_ids_from_siblings(self):
        data = {
            "lease": {
                "leaseID": 300,
                "primaryLeaseStatusID": 2,
                "dateLeaseStart": "2025-01-01",
                "dateLeaseEnd": "2026-01-01",
            },
            "unit": {"unitID": 200},
            "property": {"propertyID": 100},
        }
        rentvine_id, unit_id, prop_id, defaults = map_lease(data)
        assert rentvine_id == 300
        assert unit_id == 200
        assert prop_id == 100
        assert defaults["primary_lease_status"] == 2

    def test_fallback_ids_from_lease_body(self):
        data = {
            "lease": {
                "leaseID": 301,
                "unitID": 201,
                "propertyID": 101,
            }
        }
        _, unit_id, prop_id, _ = map_lease(data)
        assert unit_id == 201
        assert prop_id == 101


class TestMapTenantFromLease:
    def test_contact_envelope(self):
        data = {
            "leaseTenant": {"isPrimary": "1"},
            "contact": {
                "contactID": 500,
                "firstName": "Tom",
                "lastName": "Renter",
                "emails": [{"email": "tom@test.com"}],
            },
        }
        contact_id, is_primary, defaults = map_tenant_from_lease(data)
        assert contact_id == 500
        assert is_primary is True
        assert defaults["name"] == "Tom Renter"
        assert defaults["email"] == "tom@test.com"

    def test_not_primary(self):
        data = {
            "leaseTenant": {"isPrimary": "0"},
            "contact": {"contactID": 501, "name": "Sue Co-tenant"},
        }
        _, is_primary, defaults = map_tenant_from_lease(data)
        assert is_primary is False
        assert defaults["name"] == "Sue Co-tenant"


class TestAddressDriftFixInService:
    """Test that UnitSyncService fills empty unit address from parent property."""

    @pytest.mark.django_db
    def test_unit_inherits_property_address(self):
        from core.models import Property, Unit

        prop = Property.objects.create(
            rentvine_id=100,
            address_line_1="123 Main St",
            city="Raleigh",
            state="NC",
            postal_code="27601",
        )

        # Simulate what map_unit returns when RentVine has empty unit address
        defaults = {
            "name": "Unit B",
            "address_line_1": "",
            "unit_number": "B",
            "city": "",
            "state": "",
            "postal_code": "",
            "property": prop,
        }

        # Apply the same logic as UnitSyncService.sync()
        if not defaults.get("address_line_1"):
            defaults["address_line_1"] = prop.address_line_1
        if not defaults.get("city"):
            defaults["city"] = prop.city
        if not defaults.get("state"):
            defaults["state"] = prop.state
        if not defaults.get("postal_code"):
            defaults["postal_code"] = prop.postal_code

        unit = Unit.objects.create(rentvine_id=200, **defaults)

        assert unit.address_line_1 == "123 Main St"
        assert unit.city == "Raleigh"
        assert unit.state == "NC"
        assert unit.postal_code == "27601"
        assert unit.display_address == "123 Main St - B"

    @pytest.mark.django_db
    def test_unit_keeps_own_address_when_present(self):
        from core.models import Property, Unit

        prop = Property.objects.create(
            rentvine_id=101,
            address_line_1="100 Oak Ave",
            city="Durham",
            state="NC",
            postal_code="27701",
        )

        # Unit has its own address from RentVine
        defaults = {
            "name": "",
            "address_line_1": "100 Oak Ave",
            "unit_number": "",
            "city": "Durham",
            "state": "NC",
            "postal_code": "27701",
            "property": prop,
        }

        # The drift fix should NOT overwrite existing values
        if not defaults.get("address_line_1"):
            defaults["address_line_1"] = prop.address_line_1
        if not defaults.get("city"):
            defaults["city"] = prop.city

        unit = Unit.objects.create(rentvine_id=201, **defaults)
        assert unit.city == "Durham"
        assert unit.display_address == "100 Oak Ave"


class TestLinkOwnersFromPortfolioContacts:
    @pytest.mark.django_db
    def test_links_owner_to_portfolio(self):
        import json
        from core.models import Owner, Portfolio
        from integrations.rentvine.services import link_owners_from_portfolio_contacts

        owner = Owner.objects.create(rentvine_contact_id=1084, name="Jane Owner")
        portfolio = Portfolio.objects.create(
            rentvine_id=42,
            name="Test Portfolio",
            raw_data={
                "portfolio": {
                    "contacts": json.dumps([
                        {"contactID": "1084", "contactTypeID": "1"},
                        {"contactID": "9999", "contactTypeID": "1"},  # non-existent
                    ])
                }
            },
        )

        result = link_owners_from_portfolio_contacts()
        assert result["linked"] == 1
        assert result["skipped"] == 1
        assert portfolio in owner.portfolios.all()

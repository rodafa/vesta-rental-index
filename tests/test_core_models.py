import pytest
from core.models import Lease, Owner, Portfolio, Property, Tenant, Unit
from core.selectors import get_revenue_units


@pytest.fixture
def portfolio(db):
    return Portfolio.objects.create(rentvine_id=1, name="Test Portfolio")


@pytest.fixture
def owner(db, portfolio):
    o = Owner.objects.create(rentvine_contact_id=1, name="Jane Owner")
    o.portfolios.add(portfolio)
    return o


@pytest.fixture
def prop(db, portfolio):
    return Property.objects.create(
        rentvine_id=100,
        portfolio=portfolio,
        name="123 Main St",
        address_line_1="123 Main St",
        city="Raleigh",
        state="NC",
        postal_code="27601",
    )


@pytest.fixture
def unit(db, prop):
    return Unit.objects.create(
        rentvine_id=200,
        property=prop,
        address_line_1="123 Main St",
        unit_number="",
        bedrooms=3,
    )


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(rentvine_contact_id=2, name="Bob Renter")


# --- FK relationships ---


class TestRelationships:
    def test_property_belongs_to_portfolio(self, prop, portfolio):
        assert prop.portfolio == portfolio
        assert prop in portfolio.properties.all()

    def test_unit_belongs_to_property(self, unit, prop):
        assert unit.property == prop
        assert unit in prop.units.all()

    def test_owner_m2m_portfolio(self, owner, portfolio):
        assert portfolio in owner.portfolios.all()
        assert owner in portfolio.owners.all()

    def test_lease_fks(self, unit, prop, tenant):
        lease = Lease.objects.create(
            rentvine_id=300,
            unit=unit,
            property=prop,
            primary_lease_status=2,
        )
        lease.tenants.add(tenant)
        assert lease.unit == unit
        assert lease.property == prop
        assert tenant in lease.tenants.all()
        assert lease in unit.leases.all()
        assert lease in tenant.leases.all()


# --- display_address ---


class TestDisplayAddress:
    def test_single_family_no_unit_number(self, unit):
        """Single-family: just the base address, no suffix."""
        assert unit.display_address == "123 Main St"

    def test_multi_unit_with_unit_number(self, prop):
        u = Unit.objects.create(
            rentvine_id=201,
            property=prop,
            address_line_1="123 Main St",
            unit_number="A",
        )
        assert u.display_address == "123 Main St - A"

    def test_name_used_as_suffix_when_no_unit_number(self, prop):
        u = Unit.objects.create(
            rentvine_id=202,
            property=prop,
            address_line_1="123 Main St",
            name="B",
        )
        assert u.display_address == "123 Main St - B"

    def test_name_suppressed_when_duplicate_of_address(self, prop):
        """Name that duplicates address_line_1 should not produce a suffix."""
        u = Unit.objects.create(
            rentvine_id=203,
            property=prop,
            address_line_1="123 Main St",
            name="123 Main St",
        )
        assert u.display_address == "123 Main St"

    def test_name_suppressed_when_word_subset_of_address(self, prop):
        """Name whose words are a subset of address words should be suppressed."""
        u = Unit.objects.create(
            rentvine_id=204,
            property=prop,
            address_line_1="555 Baldwin Ave",
            name="Baldwin 555",
        )
        assert u.display_address == "555 Baldwin Ave"

    def test_falls_back_to_property_address(self, prop):
        """Unit with no address_line_1 falls back to property's address."""
        u = Unit.objects.create(
            rentvine_id=205,
            property=prop,
            address_line_1="",
            unit_number="C",
        )
        assert u.display_address == "123 Main St - C"

    def test_falls_back_to_property_str(self, db):
        """Unit with no address at all falls back to property __str__."""
        bare_prop = Property.objects.create(rentvine_id=999, name="Bare Property")
        u = Unit.objects.create(rentvine_id=206, property=bare_prop)
        assert u.display_address == "Bare Property"


# --- __str__ ---


class TestStr:
    def test_portfolio_str(self, portfolio):
        assert str(portfolio) == "Test Portfolio"

    def test_owner_str(self, owner):
        assert str(owner) == "Jane Owner"

    def test_property_str(self, prop):
        assert str(prop) == "123 Main St"

    def test_unit_str_delegates_to_display_address(self, unit):
        assert str(unit) == unit.display_address

    def test_tenant_str(self, tenant):
        assert str(tenant) == "Bob Renter"

    def test_lease_str(self, unit, prop):
        lease = Lease.objects.create(rentvine_id=301, unit=unit, property=prop)
        assert str(lease) == f"Lease #301 - {unit}"


# --- Portfolio slug auto-generation ---


class TestPortfolioSlug:
    def test_auto_slug(self, db):
        p = Portfolio.objects.create(rentvine_id=10, name="My Cool Portfolio")
        assert p.slug == "my-cool-portfolio"

    def test_slug_uniqueness(self, db):
        Portfolio.objects.create(rentvine_id=11, name="Dupe")
        p2 = Portfolio.objects.create(rentvine_id=12, name="Dupe")
        assert p2.slug == "dupe-1"


# --- Selectors ---


class TestSelectors:
    def test_get_revenue_units_excludes_non_revenue(self, prop):
        rev = Unit.objects.create(rentvine_id=210, property=prop, is_active=True)
        non_rev = Unit.objects.create(
            rentvine_id=211,
            property=prop,
            is_active=True,
            raw_data={"unit": {"isNonRevenue": "1"}},
        )
        inactive = Unit.objects.create(
            rentvine_id=212, property=prop, is_active=False
        )
        qs = get_revenue_units()
        assert rev in qs
        assert non_rev not in qs
        assert inactive not in qs

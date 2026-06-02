"""
Unit tests for PM<->local address matching and meld-to-unit resolution.
"""

import pytest

from core.models import Portfolio, Property, Unit
from integrations.property_meld.services import (
    normalize_address,
    build_property_key,
    resolve_meld_unit,
    resolve_all_melds,
)
from maintenance.models import Meld


# ---------------------------------------------------------------------------
# Pure function tests (no DB)
# ---------------------------------------------------------------------------


class TestNormalizeAddress:
    def test_basic(self):
        assert normalize_address("123 Main Street") == "123 main st"

    def test_directionals(self):
        assert normalize_address("456 North Oak Avenue") == "456 n oak ave"

    def test_punctuation(self):
        assert normalize_address("789 Elm Blvd., #2") == "789 elm blvd 2"

    def test_whitespace(self):
        assert normalize_address("  100   Park   Drive  ") == "100 park dr"

    def test_empty(self):
        assert normalize_address("") == ""
        assert normalize_address(None) == ""


class TestBuildPropertyKey:
    def test_full_key(self):
        key = build_property_key("123 Main St", "Denver", "CO", "80202")
        assert key == "123 main st|denver|co|80202"

    def test_normalises_components(self):
        key = build_property_key("123 Main Street", "Denver", "CO", "80202-1234")
        assert key == "123 main st|denver|co|80202"

    def test_empty_parts_omitted(self):
        key = build_property_key("123 Main St", "", "CO", "")
        assert key == "123 main st|co"


# ---------------------------------------------------------------------------
# DB tests — resolve_meld_unit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveMeldUnit:
    @pytest.fixture
    def portfolio(self):
        return Portfolio.objects.create(rentvine_id=1, name="Test Portfolio")

    @pytest.fixture
    def single_unit_property(self, portfolio):
        prop = Property.objects.create(
            rentvine_id=100,
            property_meld_id=500,
            portfolio=portfolio,
            address_line_1="123 Main St",
            city="Denver",
            state="CO",
            postal_code="80202",
            is_active=True,
        )
        unit = Unit.objects.create(
            rentvine_id=200,
            property_meld_id=700,
            property=prop,
            name="123 Main St",
            address_line_1="123 Main St",
            is_active=True,
        )
        return prop, unit

    @pytest.fixture
    def multi_unit_property(self, portfolio):
        prop = Property.objects.create(
            rentvine_id=101,
            property_meld_id=501,
            portfolio=portfolio,
            address_line_1="456 Oak Ave",
            city="Denver",
            state="CO",
            postal_code="80203",
            is_multi_unit=True,
            is_active=True,
        )
        unit_a = Unit.objects.create(
            rentvine_id=201,
            property_meld_id=701,
            property=prop,
            name="A",
            address_line_1="456 Oak Ave",
            unit_number="A",
            is_active=True,
        )
        unit_b = Unit.objects.create(
            rentvine_id=202,
            property_meld_id=702,
            property=prop,
            name="B",
            address_line_1="456 Oak Ave",
            unit_number="B",
            is_active=True,
        )
        return prop, unit_a, unit_b

    def test_resolve_by_unit_ref(self, single_unit_property):
        _, unit = single_unit_property
        meld = Meld.objects.create(
            property_meld_id="M1",
            unit_ref="700",
            property_meld_property_id="500",
        )
        result = resolve_meld_unit(meld)
        assert result == unit

    def test_resolve_by_property_single_unit(self, single_unit_property):
        prop, unit = single_unit_property
        meld = Meld.objects.create(
            property_meld_id="M2",
            unit_ref="",
            property_meld_property_id="500",
        )
        result = resolve_meld_unit(meld)
        assert result == unit

    def test_no_resolve_multi_unit_without_unit_ref(self, multi_unit_property):
        prop, unit_a, unit_b = multi_unit_property
        meld = Meld.objects.create(
            property_meld_id="M3",
            unit_ref="",
            property_meld_property_id="501",
        )
        result = resolve_meld_unit(meld)
        assert result is None

    def test_resolve_multi_unit_with_unit_ref(self, multi_unit_property):
        prop, unit_a, unit_b = multi_unit_property
        meld = Meld.objects.create(
            property_meld_id="M4",
            unit_ref="702",
            property_meld_property_id="501",
        )
        result = resolve_meld_unit(meld)
        assert result == unit_b

    def test_no_match(self):
        meld = Meld.objects.create(
            property_meld_id="M5",
            unit_ref="99999",
            property_meld_property_id="99999",
        )
        result = resolve_meld_unit(meld)
        assert result is None

    def test_invalid_unit_ref(self):
        meld = Meld.objects.create(
            property_meld_id="M6",
            unit_ref="not-a-number",
            property_meld_property_id="",
        )
        result = resolve_meld_unit(meld)
        assert result is None

    def test_resolve_all_sets_property_on_unit_resolved(self, single_unit_property):
        prop, unit = single_unit_property
        meld = Meld.objects.create(
            property_meld_id="M7",
            unit_ref="700",
            property_meld_property_id="500",
        )
        resolve_all_melds()
        meld.refresh_from_db()
        assert meld.unit == unit
        assert meld.property == prop

    def test_resolve_all_sets_property_without_unit_on_multi(self, multi_unit_property):
        """Multi-unit, no unit_ref: unit stays None but property is set."""
        prop, unit_a, unit_b = multi_unit_property
        meld = Meld.objects.create(
            property_meld_id="M8",
            unit_ref="",
            property_meld_property_id="501",
        )
        res = resolve_all_melds()
        meld.refresh_from_db()
        assert meld.unit is None
        assert meld.property == prop
        assert res["prop_resolved"] >= 1
        assert res["unit_unresolved"] >= 1

    def test_resolve_all_backfills_property_from_existing_unit(self, single_unit_property):
        """Meld already has unit set — property gets backfilled."""
        prop, unit = single_unit_property
        meld = Meld.objects.create(
            property_meld_id="M9",
            unit_ref="700",
            property_meld_property_id="500",
            unit=unit,
        )
        assert meld.property is None
        resolve_all_melds()
        meld.refresh_from_db()
        assert meld.property == prop

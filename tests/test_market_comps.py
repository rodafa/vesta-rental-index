"""
Tests for leasing.market_comps — the pure reducer function.

No network, no DB.  Every test uses the 78 Moody Ave fixture.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from leasing.market_comps import _normalize_address, _str_money, reduce_comps

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_comps_78_moody.json"


@pytest.fixture
def raw_comps():
    return json.loads(FIXTURE_PATH.read_text())


# Fixture has 11 comps.  Confirm it before tests depend on it.
def test_fixture_has_expected_count(raw_comps):
    assert len(raw_comps) == 11


# --- Standard reduction with our_sqft known ---
#
# Fixture walkthrough (11 comps, our_sqft=906, sqft range 453–1359):
#
#   78 Moody Ave      906 sqft  available  Single Family  apt_complex=null  → self (excluded step 3)
#   32 Brice St       null sqft rented     Single Family  apt_complex=null  → survives (null sqft kept)
#   15 Locust Hill    1100 sqft available  Single Family  apt_complex=null  → survives
#   120 Main St 4A    950 sqft  available  Apartment      apt_complex=true  → apartment (excluded step 1)
#   120 Main St 4B    1100 sqft available  Apartment      apt_complex=true  → apartment (excluded step 1)
#   55 Ridge Ave      950 sqft  rented     Single Family  apt_complex=null  → survives
#   400 Warburton     2500 sqft available  Single Family  apt_complex=null  → sqft_outlier (excluded step 4)
#   10 Oak St         880 sqft  available  Single Family  apt_complex=null  → rent_null_or_zero (excluded step 5)
#   22 Elm Pl         920 sqft  available  Single Family  apt_complex=true  → in_apartment_complex (excluded step 2)
#   88 Park Hill      1000 sqft available  Single Family  apt_complex=null  → survives
#   5 Maple St        870 sqft  rented     Single Family  apt_complex=null  → survives
#
# Surviving set (5): 32 Brice, 15 Locust Hill, 55 Ridge, 88 Park Hill, 5 Maple
#   available (status=="available"): 15 Locust Hill (2200), 88 Park Hill (2150)
#   off_market (status=="rented"): 32 Brice (1850), 55 Ridge (1800), 5 Maple (1750)
#
# Address normalization: "78 Moody Avenue" (our) and "78 Moody Avenue, Candler, NC"
# (comp) both normalize to "78 moody ave" — suffix mapping avenue→ave.

PERIOD_START = date(2026, 11, 17)
PERIOD_END = date(2026, 11, 23)
TODAY = date(2026, 11, 23)

OUR_ADDRESS = "78 Moody Avenue"
OUR_RENT = 2100
OUR_SQFT = 906
BEDS = 3
RADIUS = 2


@pytest.fixture
def result(raw_comps):
    return reduce_comps(
        raw_comps=raw_comps,
        our_address=OUR_ADDRESS,
        our_rent=OUR_RENT,
        our_sqft=OUR_SQFT,
        beds=BEDS,
        radius_miles=RADIUS,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        today=TODAY,
    )


class TestExclusions:
    """Verify each exclusion step removes the right comps."""

    def test_apartment_exclusion(self, result):
        # 120 Main St 4A and 4B are Apartment type
        assert result["excluded_counts"]["apartment"] == 2

    def test_in_apartment_complex_exclusion(self, result):
        # 22 Elm Pl is in_apartment_complex (not Apartment type)
        assert result["excluded_counts"]["in_apartment_complex"] == 1

    def test_self_exclusion(self, result):
        # 78 Moody Avenue matches our address
        assert result["excluded_counts"]["self"] == 1

    def test_sqft_outlier_exclusion(self, result):
        # 400 Warburton has 2500 sqft vs our 906 (>50% diff)
        assert result["excluded_counts"]["sqft_outlier"] == 1

    def test_rent_null_or_zero_exclusion(self, result):
        # 10 Oak St has rent=0
        assert result["excluded_counts"]["rent_null_or_zero"] == 1

    def test_total_exclusions_and_set_size(self, result):
        total_excluded = sum(result["excluded_counts"].values())
        assert total_excluded == 6
        assert result["comps_in_set"] == 5
        assert result["comps_returned"] == 11


class TestTopLevelMetadata:
    """Verify beds, radius, our_rent, our_sqft echo through."""

    def test_beds(self, result):
        assert result["beds"] == 3

    def test_radius_miles(self, result):
        assert result["radius_miles"] == 2

    def test_our_rent(self, result):
        assert result["our_rent"] == "2100"

    def test_our_sqft(self, result):
        assert result["our_sqft"] == 906


class TestAvailableAggregates:
    """Verify available-comp aggregates."""

    def test_available_count(self, result):
        # 15 Locust Hill (2200) and 88 Park Hill (2150)
        assert result["available_count"] == 2

    def test_available_median_rent(self, result):
        # median of [2200, 2150] = 2175
        assert result["available_median_rent"] == "2175"

    def test_available_median_dom(self, result):
        # DOM: 15 Locust Hill=7, 88 Park Hill=3 → median=5
        assert result["available_median_dom"] == 5


class TestOffMarketAggregates:
    """Verify off-market comp aggregates."""

    def test_off_market_count(self, result):
        # 32 Brice (1850), 55 Ridge (1800), 5 Maple (1750)
        assert result["off_market_count"] == 3

    def test_off_market_median_rent(self, result):
        # median of [1850, 1800, 1750] = 1800
        assert result["off_market_median_rent"] == "1800"

    def test_off_market_median_dom(self, result):
        # DOM: 32 Brice=45, 55 Ridge=20, 5 Maple=25 → median=25
        assert result["off_market_median_dom"] == 25

    def test_off_market_window(self, result):
        # date_rented: 32 Brice=2026-11-19, 55 Ridge=2026-11-18, 5 Maple=2026-11-22
        assert result["off_market_window_start"] == "2026-11-18"
        assert result["off_market_window_end"] == "2026-11-22"


class TestCameOffMarketInPeriod:
    """Verify came_off_market_in_period detection."""

    def test_count(self, result):
        # All 3 off-market comps have date_rented in [11/17, 11/23]
        assert result["came_off_market_in_period"]["count"] == 3

    def test_examples_populated(self, result):
        examples = result["came_off_market_in_period"]["examples"]
        assert len(examples) == 3
        addresses = {e["address"] for e in examples}
        assert "32 Brice St, Candler, NC" in addresses
        assert "55 Ridge Ave, Candler, NC" in addresses
        assert "5 Maple St, Candler, NC" in addresses


class TestNewListingsInPeriod:
    """Verify new_listings_in_period (available comps with DOM <= period days)."""

    def test_count(self, result):
        # Period is 6 days (11/17 to 11/23). Available comps:
        # 15 Locust Hill DOM=7 (>6, excluded), 88 Park Hill DOM=3 (<=6, included)
        assert result["new_listings_in_period"]["count"] == 1

    def test_example(self, result):
        examples = result["new_listings_in_period"]["examples"]
        assert len(examples) == 1
        assert "Park Hill" in examples[0]["address"]


class TestPriceDropInPeriodDetected:
    """Verify price drops within the period are detected."""

    def test_count(self, result):
        # 15 Locust Hill: drop on 2026-11-20 (in period)
        # 5 Maple St: drop on 2026-11-18 (in period)
        assert result["price_drops_in_period"]["count"] == 2

    def test_examples(self, result):
        examples = result["price_drops_in_period"]["examples"]
        assert len(examples) == 2
        addresses = {e["address"] for e in examples}
        assert "15 Locust Hill Ave, Candler, NC" in addresses
        assert "5 Maple St, Candler, NC" in addresses

    def test_drop_values(self, result):
        examples = result["price_drops_in_period"]["examples"]
        assert len(examples) == 2
        locust = next(e for e in examples if "Locust Hill" in e["address"])
        assert locust["from"] == "2400"
        assert locust["to"] == "2200"
        assert locust["date"] == "2026-11-20"


class TestPriceDropOutsidePeriodIgnored:
    """A price drop outside the period window must not appear."""

    def test_outside_period_ignored(self, raw_comps):
        # Use a period that excludes all price drops in the fixture
        result = reduce_comps(
            raw_comps=raw_comps,
            our_address=OUR_ADDRESS,
            our_rent=OUR_RENT,
            our_sqft=OUR_SQFT,
            beds=BEDS,
            radius_miles=RADIUS,
            period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 7),
            today=TODAY,
        )
        assert result["price_drops_in_period"]["count"] == 0
        assert result["price_drops_in_period"]["examples"] == []


class TestClosestRankedBySqftDiff:
    """Verify closest_available is ranked by sqft difference from our unit."""

    def test_closest_order(self, result):
        items = result["closest_available"]
        # Only 2 available comps: 15 Locust Hill (1100 sqft), 88 Park Hill (1000 sqft)
        # Our sqft is 906.
        # 88 Park Hill: |1000 - 906| = 94
        # 15 Locust Hill: |1100 - 906| = 194
        assert len(items) == 2
        assert "Park Hill" in items[0]["address"]
        assert "Locust Hill" in items[1]["address"]


class TestApartmentContext:
    """Verify apartment_context from excluded apartment comps."""

    def test_apartment_context_exists(self, result):
        assert result["apartment_context"] is not None

    def test_available_count(self, result):
        # Both apartment comps (120 Main St 4A and 4B) are "available"
        assert result["apartment_context"]["available_count"] == 2

    def test_median_rent(self, result):
        # median of [2200, 1950] = 2075
        assert result["apartment_context"]["median_rent"] == "2075"


class TestNullSqftCompSurvives:
    """
    32 Brice St has sqft=null and must NOT be excluded by sqft_outlier.

    Proof: off_market_count is 3 (not 2). Without Brice St it would be 2.
    And off_market_median_rent is 1800 (median of [1850, 1800, 1750]).
    """

    def test_off_market_includes_null_sqft(self, result):
        assert result["off_market_count"] == 3
        assert result["off_market_median_rent"] == "1800"


class TestNoRentedInOutput:
    """Verify no key or string value in the output dict contains 'rented'."""

    def _walk_values(self, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from self._walk_values(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from self._walk_values(item)
        elif isinstance(obj, str):
            yield obj

    def test_no_rented_in_output(self, result):
        all_strings = list(self._walk_values(result))
        assert len(all_strings) > 0, "result should contain strings"
        for s in all_strings:
            assert "rented" not in s.lower(), (
                f"Found 'rented' in result: {s!r}"
            )


# --- _str_money unit tests ---

class TestStrMoney:

    def test_int(self):
        assert _str_money(2100) == "2100"

    def test_float(self):
        assert _str_money(2100.0) == "2100"

    def test_float_half_up(self):
        assert _str_money(2100.5) == "2101"

    def test_decimal(self):
        assert _str_money(Decimal("2100.00")) == "2100"

    def test_decimal_our_rent(self):
        # Our rent from DB is Decimal — must not produce "2750.00"
        assert _str_money(Decimal("2750.00")) == "2750"
        assert "." not in _str_money(Decimal("2750.00"))

    def test_string(self):
        assert _str_money("2100.49") == "2100"

    def test_string_round_up(self):
        assert _str_money("2100.50") == "2101"

    def test_none(self):
        assert _str_money(None) is None

    def test_unparseable_comma_string(self):
        # "2,100" has a comma — Decimal("2,100") raises InvalidOperation.
        # Must return None, not the raw string.
        result = _str_money("2,100")
        assert result is None

    def test_unparseable_garbage(self):
        assert _str_money("N/A") is None

    def test_zero(self):
        assert _str_money(0) == "0"

    def test_negative(self):
        assert _str_money(-500) == "-500"


# --- Edge cases ---

class TestNoComps:
    """reduce_comps with an empty list."""

    def test_empty_comps(self):
        result = reduce_comps(
            raw_comps=[],
            our_address="78 Moody Avenue",
            our_rent=2100,
            our_sqft=906,
            beds=3,
            radius_miles=2,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            today=TODAY,
        )
        assert result["comps_returned"] == 0
        assert result["comps_in_set"] == 0
        assert result["available_count"] == 0
        assert result["off_market_count"] == 0
        assert result["available_median_rent"] is None
        assert result["available_median_dom"] is None
        assert result["off_market_window_start"] is None
        assert result["off_market_window_end"] is None
        assert result["apartment_context"] is None


class TestNoSqft:
    """When our_sqft is None, sqft_outlier step excludes nobody."""

    def test_no_sqft_skips_outlier_filter(self, raw_comps):
        result = reduce_comps(
            raw_comps=raw_comps,
            our_address=OUR_ADDRESS,
            our_rent=OUR_RENT,
            our_sqft=None,
            beds=BEDS,
            radius_miles=RADIUS,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            today=TODAY,
        )
        assert result["excluded_counts"]["sqft_outlier"] == 0
        # 400 Warburton (2500 sqft) survives — total remaining is 6 not 5
        assert result["comps_in_set"] == 6


class TestAddressNormalization:
    """Verify _normalize_address handles suffix abbreviations and unit designators."""

    def test_avenue_vs_ave_with_different_city(self):
        # Rentvine says "78 Moody Avenue, Asheville, NC 28806"
        # Comp data says "78 Moody Ave, Candler, NC"
        assert _normalize_address("78 Moody Avenue, Asheville, NC 28806") == \
            _normalize_address("78 Moody Ave, Candler, NC")

    def test_road_vs_rd_with_period(self):
        assert _normalize_address("12 Baker Road") == \
            _normalize_address("12 Baker Rd.")

    def test_unit_designator_stripped(self):
        assert _normalize_address("445 Sand Hill Rd APT 3") == \
            _normalize_address("445 Sand Hill Road")

    def test_different_addresses_do_not_match(self):
        assert _normalize_address("78 Moody Avenue, Asheville, NC") != \
            _normalize_address("79 Moody Avenue, Asheville, NC")


class TestOffMarketLookbackWindow:
    """Off-market aggregates only include comps with date_rented within 90 days."""

    def test_old_comp_excluded_recent_included(self):
        today = date(2026, 11, 23)
        comps = [
            {
                "address": "1 Recent St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 1800,
                "status": "rented", "days_on_market": 10,
                "date_rented": "2026-09-25",  # 59 days before today — included
                "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
            {
                "address": "2 Old St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 3000,
                "status": "rented", "days_on_market": 50,
                "date_rented": "2026-08-15",  # 100 days before today — excluded
                "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
            {
                "address": "3 Null St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 5000,
                "status": "rented", "days_on_market": 5,
                "date_rented": None,  # no date — excluded from aggregates
                "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
        ]
        result = reduce_comps(
            raw_comps=comps,
            our_address="99 Nowhere",
            our_rent=2000,
            our_sqft=900,
            beds=3,
            radius_miles=2,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            today=today,
        )
        # All 3 survive the exclusion pipeline → comps_in_set counts them all
        assert result["comps_in_set"] == 3
        # Only the recent comp (59 days) is in aggregates
        assert result["off_market_count"] == 1
        assert result["off_market_median_rent"] == "1800"
        assert result["off_market_median_dom"] == 10
        # Window reflects only the recent comp
        assert result["off_market_window_start"] == "2026-09-25"
        assert result["off_market_window_end"] == "2026-09-25"


class TestUnknownStatusIgnored:
    """Comps with status other than 'available'/'rented' land in neither bucket."""

    def test_pending_and_null_status_excluded_from_buckets(self):
        comps = [
            {
                "address": "1 Good St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 2000,
                "status": "available", "days_on_market": 5,
                "date_rented": None, "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
            {
                "address": "2 Good St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 1800,
                "status": "rented", "days_on_market": 10,
                "date_rented": "2026-11-20", "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
            {
                "address": "3 Weird St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 9999,
                "status": "pending", "days_on_market": 1,
                "date_rented": None, "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
            {
                "address": "4 Weird St, Candler, NC",
                "beds": 3, "sqft": 900, "rent": 9999,
                "status": None, "days_on_market": 1,
                "date_rented": None, "property_type": "Single Family",
                "in_apartment_complex": None, "price_history": [],
            },
        ]
        result = reduce_comps(
            raw_comps=comps,
            our_address="99 Nowhere",
            our_rent=2000,
            our_sqft=900,
            beds=3,
            radius_miles=2,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            today=TODAY,
        )
        # All 4 survive exclusion pipeline (no apartments, no self, etc.)
        assert result["comps_in_set"] == 4
        # Only the explicit matches land in each bucket
        assert result["available_count"] == 1
        assert result["off_market_count"] == 1
        # Medians reflect only the explicit buckets, not the unknowns
        assert result["available_median_rent"] == "2000"
        assert result["off_market_median_rent"] == "1800"


class TestOutputKeyContract:
    """Verify the output dict has exactly the approved key set, flat."""

    REQUIRED_KEYS = {
        "beds", "radius_miles", "our_rent", "our_sqft",
        "comps_returned", "comps_in_set", "excluded_counts",
        "available_count", "available_median_rent", "available_median_dom",
        "off_market_count", "off_market_median_rent", "off_market_median_dom",
        "off_market_window_start", "off_market_window_end",
        "came_off_market_in_period", "new_listings_in_period",
        "price_drops_in_period", "closest_available", "apartment_context",
    }

    def test_exact_keys(self, result):
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_excluded_counts_keys(self, result):
        assert set(result["excluded_counts"].keys()) == {
            "apartment", "in_apartment_complex", "self",
            "sqft_outlier", "rent_null_or_zero",
        }

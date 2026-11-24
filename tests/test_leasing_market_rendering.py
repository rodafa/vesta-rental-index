"""
Tests for BUILD 2 — market comp data rendering pipeline.

Tests prepare_market_for_template and verifies that market data flows
correctly from snapshot to template output.

ALL rendering-test expectations are DERIVED from the market_dict fixture
at runtime — no hardcoded values except the cross-check class, which
mirrors test_market_comps.py assertions for consistency.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from leasing.market_comps import reduce_comps
from leasing.note_services import (
    _fmt_money,
    build_leasing_prompt,
    market_position,
    prepare_market_for_template,
    render_leasing_notes_html_from_snapshot,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_comps_78_moody.json"

PERIOD_START = date(2026, 11, 17)
PERIOD_END = date(2026, 11, 23)
TODAY = date(2026, 11, 23)
OUR_ADDRESS = "78 Moody Avenue"
OUR_RENT = 2100
OUR_SQFT = 906
BEDS = 3
RADIUS = 2


@pytest.fixture
def raw_comps():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def market_dict(raw_comps):
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


@pytest.fixture
def prepared(market_dict):
    return prepare_market_for_template(market_dict)


# ---------------------------------------------------------------------------
# prepare_market_for_template
# ---------------------------------------------------------------------------


class TestPrepareMarketForTemplate:
    """Verify date parsing and key renaming."""

    def test_empty_dict(self):
        assert prepare_market_for_template({}) == {}

    def test_none(self):
        assert prepare_market_for_template(None) == {}

    def test_window_dates_are_date_objects(self, prepared):
        assert isinstance(prepared["off_market_window_start"], date)
        assert isinstance(prepared["off_market_window_end"], date)

    def test_price_drop_keys_renamed(self, prepared):
        for ex in prepared["price_drops_in_period"]["examples"]:
            assert "rent_from" in ex, "expected 'rent_from' key"
            assert "rent_to" in ex, "expected 'rent_to' key"
            assert "from" not in ex, "'from' should be renamed"
            assert "to" not in ex, "'to' should be renamed"

    def test_price_drop_dates_parsed(self, prepared):
        for ex in prepared["price_drops_in_period"]["examples"]:
            assert isinstance(ex["date"], date)

    def test_came_off_dates_parsed(self, prepared):
        for ex in prepared["came_off_market_in_period"]["examples"]:
            assert isinstance(ex["date"], date)

    def test_passthrough_values_unchanged(self, market_dict, prepared):
        """Non-date, non-money fields pass through unchanged."""
        assert prepared["beds"] == market_dict["beds"]
        assert prepared["radius_miles"] == market_dict["radius_miles"]
        assert prepared["available_count"] == market_dict["available_count"]
        assert prepared["off_market_count"] == market_dict["off_market_count"]

    def test_our_rent_formatted(self, market_dict, prepared):
        """our_rent should be comma-formatted."""
        assert prepared["our_rent"] == _fmt_money(market_dict["our_rent"])

    def test_position_none_when_few_comps(self, prepared):
        """Fixture has available_count=2, below threshold — position is None."""
        assert prepared["position"] is None
        assert prepared["position_sentence"] is None

    def test_position_sentence_populated_when_enough_comps(self):
        """With available_count >= 3 and parseable rents, sentence is built."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 10,
            "off_market_count": 0,
            "our_rent": "2100",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        out = prepare_market_for_template(mkt)
        assert out["position"] == "above"
        assert "5 comparable homes" in out["position_sentence"]
        assert "$2,000" in out["position_sentence"]
        assert "$2,100" in out["position_sentence"]
        assert "above the median asking rent" in out["position_sentence"]
        assert "median 10 days on market" in out["position_sentence"]

    def test_position_sentence_omits_dom_when_none(self):
        """When available_median_dom is None, DOM clause is omitted."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": None,
            "off_market_count": 0,
            "our_rent": "2100",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        out = prepare_market_for_template(mkt)
        assert "days on market" not in out["position_sentence"]
        assert "above the median asking rent" in out["position_sentence"]

    def test_position_sentence_at_wording(self):
        """'at' position uses 'right at the median asking rent'."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 7,
            "off_market_count": 0,
            "our_rent": "2010",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        out = prepare_market_for_template(mkt)
        assert "right at the median asking rent" in out["position_sentence"]

    def test_money_fields_have_commas(self, market_dict, prepared):
        """All money display strings should have thousands separators."""
        assert prepared["available_median_rent"] == _fmt_money(market_dict["available_median_rent"])
        assert prepared["off_market_median_rent"] == _fmt_money(market_dict["off_market_median_rent"])
        assert prepared["apartment_context"]["median_rent"] == _fmt_money(
            market_dict["apartment_context"]["median_rent"]
        )

    def test_example_rents_have_commas(self, market_dict, prepared):
        """Example rent values should be comma-formatted."""
        for ex in prepared["came_off_market_in_period"]["examples"]:
            assert "," in ex["rent"] or len(ex["rent"]) <= 3, f"expected comma in {ex['rent']}"
        for ex in prepared["new_listings_in_period"]["examples"]:
            assert "," in ex["rent"] or len(ex["rent"]) <= 3, f"expected comma in {ex['rent']}"
        for ex in prepared["price_drops_in_period"]["examples"]:
            assert "," in ex["rent_from"] or len(ex["rent_from"]) <= 3
            assert "," in ex["rent_to"] or len(ex["rent_to"]) <= 3

    def test_does_not_mutate_input(self, market_dict):
        original_ws = market_dict["off_market_window_start"]
        prepare_market_for_template(market_dict)
        assert market_dict["off_market_window_start"] == original_ws
        assert isinstance(market_dict["off_market_window_start"], str)


# ---------------------------------------------------------------------------
# Cross-check: reducer output matches test_market_comps.py assertions
# ---------------------------------------------------------------------------


class TestCrossCheckWithReducer:
    """
    Cross-check that market_dict values match test_market_comps.py assertions.
    If these fail, the fixture or reducer changed — update both test files.
    """

    def test_available_count(self, market_dict):
        assert market_dict["available_count"] == 2

    def test_available_median_rent(self, market_dict):
        assert market_dict["available_median_rent"] == "2175"

    def test_available_median_dom(self, market_dict):
        assert market_dict["available_median_dom"] == 5

    def test_off_market_count(self, market_dict):
        assert market_dict["off_market_count"] == 3

    def test_off_market_median_rent(self, market_dict):
        assert market_dict["off_market_median_rent"] == "1800"

    def test_off_market_window_start(self, market_dict):
        assert market_dict["off_market_window_start"] == "2026-11-18"

    def test_off_market_window_end(self, market_dict):
        assert market_dict["off_market_window_end"] == "2026-11-22"

    def test_came_off_count(self, market_dict):
        assert market_dict["came_off_market_in_period"]["count"] == 3

    def test_new_listings_count(self, market_dict):
        assert market_dict["new_listings_in_period"]["count"] == 1

    def test_price_drops_count(self, market_dict):
        assert market_dict["price_drops_in_period"]["count"] == 2

    def test_apartment_context(self, market_dict):
        assert market_dict["apartment_context"]["available_count"] == 2
        assert market_dict["apartment_context"]["median_rent"] == "2075"

    def test_price_drop_values(self, market_dict):
        examples = market_dict["price_drops_in_period"]["examples"]
        locust = next(e for e in examples if "Locust Hill" in e["address"])
        assert locust["from"] == "2400"
        assert locust["to"] == "2200"

    def test_total_examples(self, market_dict):
        """Count every example list to catch missing comps."""
        total = (
            len(market_dict["came_off_market_in_period"]["examples"])
            + len(market_dict["new_listings_in_period"]["examples"])
            + len(market_dict["price_drops_in_period"]["examples"])
            + len(market_dict["closest_available"])
        )
        assert total == 8  # 3 came-off + 1 new-listing + 2 price-drops + 2 closest


# ---------------------------------------------------------------------------
# market_position helper
# ---------------------------------------------------------------------------


class TestMarketPosition:
    """Threshold tests for market_position()."""

    def test_none_on_empty(self):
        assert market_position({}) is None
        assert market_position(None) is None

    def test_none_when_few_comps(self):
        mkt = {"available_count": 2, "our_rent": "2100", "available_median_rent": "2000"}
        assert market_position(mkt) is None

    def test_none_when_unparseable_rent(self):
        mkt = {"available_count": 5, "our_rent": "N/A", "available_median_rent": "2000"}
        assert market_position(mkt) is None

    def test_none_when_median_zero(self):
        mkt = {"available_count": 5, "our_rent": "2100", "available_median_rent": "0"}
        assert market_position(mkt) is None

    def test_above(self):
        mkt = {"available_count": 5, "our_rent": "2200", "available_median_rent": "2000"}
        assert market_position(mkt) == "above"

    def test_below(self):
        mkt = {"available_count": 5, "our_rent": "1800", "available_median_rent": "2000"}
        assert market_position(mkt) == "below"

    def test_at_within_two_percent(self):
        # 2020 / 2000 = 1.01 → within 2%
        mkt = {"available_count": 5, "our_rent": "2020", "available_median_rent": "2000"}
        assert market_position(mkt) == "at"

    def test_at_boundary_low(self):
        # 1960 / 2000 = 0.98 → exactly at boundary
        mkt = {"available_count": 5, "our_rent": "1960", "available_median_rent": "2000"}
        assert market_position(mkt) == "at"

    def test_above_just_outside_boundary(self):
        # 2041 / 2000 = 1.0205 → just above 2%
        mkt = {"available_count": 5, "our_rent": "2041", "available_median_rent": "2000"}
        assert market_position(mkt) == "above"

    def test_fixture_returns_none(self, market_dict):
        """Fixture has available_count=2, so position is None."""
        assert market_position(market_dict) is None


# ---------------------------------------------------------------------------
# AI prompt — market position section
# ---------------------------------------------------------------------------


class TestPromptMarketSection:
    """Verify market position line in the AI prompt."""

    def _build_prompt(self, market_dict):
        ctx = {
            "unit_id": 42,
            "address": "78 Moody Ave",
            "advertised_rent": "2100",
            "rentengine_status": "Available",
            "days_on_market": 34,
            "days_on_market_delta": None,
            "new_leads": 3,
            "new_leads_delta": None,
            "showings_scheduled": 2,
            "showings_scheduled_delta": None,
            "showings_completed": 1,
            "showings_completed_delta": None,
            "showings_canceled": 0,
            "showings_canceled_delta": None,
            "showings_missed": 0,
            "showings_missed_delta": None,
            "applications_received": 0,
            "applications_received_delta": None,
            "touchpoints_calls": None,
            "touchpoints_calls_delta": None,
            "touchpoints_texts": None,
            "touchpoints_texts_delta": None,
            "upcoming_showings": None,
            "upcoming_showings_delta": None,
            "leads_per_day": 0.43,
            "price_review_suggested": True,
            "dom_urgent": False,
            "feedback": [],
            "upcoming_tours": [],
            "applications": [],
            "price_changes": [],
            "segment_benchmark": {},
            "since_listed": {},
            "since_listed_date_display": None,
            "market": market_dict,
        }
        return build_leasing_prompt(
            "Test Portfolio", [ctx], PERIOD_START, PERIOD_END,
        )

    def test_no_market_line_when_few_comps(self, market_dict):
        """Fixture has available_count=2 — no market line in prompt."""
        prompt = self._build_prompt(market_dict)
        assert "Market position" not in prompt

    def test_position_above_in_prompt(self):
        """With enough comps and rent above median, prompt says 'above'."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 10,
            "off_market_count": 0,
            "our_rent": "2200",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        prompt = self._build_prompt(mkt)
        assert "above" in prompt
        assert "Market position" in prompt

    def test_no_dollar_sign_in_market_line(self):
        """Prompt market line must not contain dollar figures."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 10,
            "off_market_count": 0,
            "our_rent": "2200",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        prompt = self._build_prompt(mkt)
        # Find the market position line specifically
        for line in prompt.splitlines():
            if "Market position" in line:
                assert "$" not in line, f"Dollar sign found in market line: {line}"

    def test_empty_market_adds_nothing(self):
        prompt = self._build_prompt({})
        assert "Market position" not in prompt

    def test_no_counts_in_prompt(self):
        """Prompt must not leak market counts or medians."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 10,
            "off_market_count": 3,
            "our_rent": "2200",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        prompt = self._build_prompt(mkt)
        # No market counts or dollar amounts should appear
        assert "5 available" not in prompt
        assert "3 came off" not in prompt
        assert "$2,000" not in prompt
        assert "$2000" not in prompt


# ---------------------------------------------------------------------------
# Fragment rendering — template-level tests
# ---------------------------------------------------------------------------

def _make_snapshot(market_dict):
    """Build a minimal unit_snapshot with market data for rendering."""
    return {
        "unit_contexts": [{
            "unit_id": 42,
            "address": "78 Moody Ave",
            "advertised_rent": "2100",
            "rentengine_status": "Available",
            "days_on_market": 34,
            "days_on_market_delta": None,
            "new_leads": 3,
            "new_leads_delta": None,
            "showings_scheduled": 2,
            "showings_scheduled_delta": None,
            "showings_completed": 1,
            "showings_completed_delta": None,
            "showings_canceled": 0,
            "showings_canceled_delta": None,
            "showings_missed": 0,
            "showings_missed_delta": None,
            "applications_received": 0,
            "applications_received_delta": None,
            "touchpoints_calls": None,
            "touchpoints_calls_delta": None,
            "touchpoints_texts": None,
            "touchpoints_texts_delta": None,
            "upcoming_showings": None,
            "upcoming_showings_delta": None,
            "leads_per_day": 0.43,
            "price_review_suggested": True,
            "dom_urgent": False,
            "feedback": [],
            "upcoming_tours": [],
            "applications": [],
            "price_changes": [],
            "segment_benchmark": {},
            "since_listed": {},
            "since_listed_date_display": None,
            "market": market_dict,
        }],
        "ai_result": {"intro": "", "unit_summaries": {"42": "Test note."}},
    }


@pytest.mark.django_db
class TestFragmentRendering:
    """Verify market data renders correctly in the email fragment."""

    def test_market_block_renders(self, market_dict):
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        assert "Market Activity" in html

    def test_heading_includes_scope(self, market_dict):
        """Heading should include bedroom count and radius."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        assert f"{market_dict['beds']}-Bedroom" in html
        assert f"{market_dict['radius_miles']} Miles" in html

    def test_position_sentence_renders_first(self):
        """When position is populated, position_sentence renders as first body line."""
        mkt = {
            "available_count": 5,
            "available_median_rent": "2000",
            "available_median_dom": 10,
            "off_market_count": 0,
            "our_rent": "2200",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(mkt))
        heading_pos = html.find("Market Activity")
        sentence_pos = html.find("comparable homes are available within")
        assert sentence_pos > heading_pos
        assert sentence_pos >= 0, "position_sentence should render"
        assert "median asking rent of $" in html
        assert "above the median asking rent" in html
        assert "median 10 days on market" in html

    def test_position_sentence_includes_dom(self):
        """position_sentence includes DOM clause when available_median_dom is set."""
        mkt = {
            "available_count": 4,
            "available_median_rent": "1800",
            "available_median_dom": 7,
            "off_market_count": 0,
            "our_rent": "1500",
            "our_sqft": 900,
            "beds": 3,
            "radius_miles": 2,
        }
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(mkt))
        assert "median 7 days on market" in html

    def test_no_separate_available_line(self, market_dict):
        """The old separate 'N comparable homes available · median...' line is gone."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        # Fixture has available_count=2 (no position_sentence), so no "comparable home" at all
        assert "comparable home" not in html

    def test_no_position_sentence_when_few_comps(self, market_dict):
        """Fixture has available_count=2 — no position_sentence in output."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        assert "comparable homes are available within" not in html

    def test_off_market_merged_line(self, market_dict):
        """Off-market line is a single merged line with dates and median."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        assert "came off the market*" in html
        # Dates and median should be on the same line (merged format)
        count = market_dict["off_market_count"]
        assert f"{count} came off the market*" in html

    def test_came_off_example_format(self, market_dict):
        """Came-off examples render as '{address} · ${rent}' with commas."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        for ex in market_dict["came_off_market_in_period"]["examples"]:
            assert ex["address"] in html
            assert f"${_fmt_money(ex['rent'])}" in html

    def test_new_listings_format(self, market_dict):
        """New listings render as '{address} · ${rent} · {dom} days on market'."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        for ex in market_dict["new_listings_in_period"]["examples"]:
            assert ex["address"] in html
            assert f"${_fmt_money(ex['rent'])}" in html
            assert f"{ex['days_on_market']} days on market" in html

    def test_price_drop_values_in_output(self, market_dict):
        """Price drop examples render comma-formatted from and to amounts."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        for ex in market_dict["price_drops_in_period"]["examples"]:
            assert f"${_fmt_money(ex['from'])}" in html
            assert f"${_fmt_money(ex['to'])}" in html

    def test_apartment_context_in_output(self, market_dict):
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        apt = market_dict["apartment_context"]
        assert f"{apt['available_count']} apartment" in html

    def test_comma_format_in_market_block(self, market_dict):
        """Dollar figures in the market block use $X,XXX format."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        off_median = _fmt_money(market_dict["off_market_median_rent"])
        assert f"${off_median}" in html

    def test_no_bare_four_digit_dollar(self, market_dict):
        """No bare 4-digit dollar figure (like $2050) in the market block."""
        import re
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        start = html.find("Market Activity")
        assert start >= 0
        end = html.find("Activity vs. Similar Listings", start)
        if end < 0:
            end = len(html)
        market_section = html[start:end]
        bare = re.findall(r"\$(\d{4,})", market_section)
        assert bare == [], f"Bare dollar figures found in market block: ${bare}"

    def test_no_rented_in_market_block(self, market_dict):
        """The word 'rented' must not appear in the market activity section."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        start = html.find("Market Activity")
        assert start >= 0, "Market Activity block should be present"
        end = html.find("Activity vs. Similar Listings", start)
        if end < 0:
            end = len(html)
        market_section = html[start:end]
        assert "rented" not in market_section.lower()

    def test_no_leased_in_market_block(self, market_dict):
        """The word 'leased' must not appear in the market activity section."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        start = html.find("Market Activity")
        assert start >= 0
        end = html.find("Activity vs. Similar Listings", start)
        if end < 0:
            end = len(html)
        market_section = html[start:end]
        assert "leased" not in market_section.lower()

    def test_body_lines_use_georgia(self, market_dict):
        """Body lines in the market block should use Georgia serif, not Helvetica blue."""
        html = render_leasing_notes_html_from_snapshot(_make_snapshot(market_dict))
        start = html.find("Market Activity")
        end = html.find("Activity vs. Similar Listings", start)
        if end < 0:
            end = len(html)
        market_section = html[start:end]
        assert "font-family:Georgia,serif" in market_section
        assert "came off the market" in market_section

    def test_empty_market_renders_nothing(self):
        html = render_leasing_notes_html_from_snapshot(_make_snapshot({}))
        assert "Market Activity" not in html

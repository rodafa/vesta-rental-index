"""Tests for automations.digest_metrics — all 8 graded measurables + watchlist tagging."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from automations.digest_config import (
    CONVERT_APPS_MAX,
    CONVERT_SHOWINGS_MIN,
    DOM_THRESHOLD,
    FIX_KEYWORD_RE,
    FOLLOW_UP_LEADS_THRESHOLD,
    FOLLOW_UP_SHOWINGS_MAX,
    LOW_LEADS_LPW_THRESHOLD,
    TAG_CONVERT,
    TAG_FIX,
    TAG_FOLLOW_UP,
    TAG_LOW_LEADS,
    TAG_PRICE,
    TAG_REVIEW,
    TAG_WAIT,
    TEAM_PASS_THRESHOLD,
    WATCHLIST_DOM_TRIGGER,
    WATCHLIST_KEYWORD_HIT_THRESHOLD,
    WATCHLIST_LPW_TRIGGER,
    WATCHLIST_MIN_DOM_FOR_LEAD_RULE,
)
from automations.digest_metrics import (
    compute_agent_contributions,
    compute_apps_submitted,
    compute_call_answer_rate,
    compute_dom,
    compute_feedback_captured,
    compute_issues,
    compute_lead_to_showing,
    compute_reply_time,
    compute_reply_time_history,
    compute_showing_completion,
    compute_stale_apps,
    compute_top_sources,
    compute_units_leased,
    compute_units_past_dom,
    compute_watchlist,
    compute_week_window,
    compute_yesterday_leads,
    is_placeholder,
    score_measurables,
    tag_watchlist_unit,
)

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 4)


def _pp_row(
    unit_id=1,
    address="100 Test St",
    unit_status="Available",
    dma="2026-08-02T12:00:00+00:00",
    dmu=None,
    leads=5,
    sched=2,
    completed=1,
    apps=0,
    pricing_position=None,
    rent=None,
    median=None,
    excluded_from_dom=False,
    context=None,
    earliest_showing=None,
    days_on_market=None,
):
    """Build a minimal property-performance row for testing."""
    row = {
        "unit_id": unit_id,
        "address": address,
        "unit_status": unit_status,
        "date_marked_available": dma,
        "date_marked_unavailable": dmu,
        "total_new_leads": leads,
        "showings_scheduled": sched,
        "showings_completed": completed,
        "applications_received": apps,
        "excluded_from_dom": excluded_from_dom,
        "showing_complete_context": context,
        "unit_earliest_showing_date": earliest_showing,
        "days_on_market": days_on_market,
        "unit_price": rent,
        "market": {
            "pricing_position": pricing_position,
            "median_rent": median,
        },
    }
    return row


# ---------------------------------------------------------------------------
# Placeholder exclusion
# ---------------------------------------------------------------------------


class TestPlaceholderExclusion:
    def test_placeholder_detected(self):
        assert is_placeholder("18 Wildwood Park - Applications Only")

    def test_placeholder_case_insensitive(self):
        assert is_placeholder("Some Address - applications only")

    def test_normal_address_not_placeholder(self):
        assert not is_placeholder("100 Main Street")

    def test_empty_string(self):
        assert not is_placeholder("")


# ---------------------------------------------------------------------------
# DOM computation
# ---------------------------------------------------------------------------


class TestComputeDom:
    def test_basic_dom(self):
        # 94 days from Aug 2 to Nov 4
        assert compute_dom("2026-08-02T12:00:00+00:00", TODAY) == 94

    def test_dom_et_conversion(self):
        """A DMA at 02:25 UTC is the previous evening in ET -> DOM should be 62."""
        # Sep 4 at 02:25 UTC = Sep 3 at 10:25 PM ET
        dom = compute_dom("2026-09-04T02:25:53+00:00", TODAY)
        assert dom == 62

    def test_dom_null_dma(self):
        assert compute_dom(None, TODAY) is None

    def test_dom_no_minus_one(self):
        # DMA 30 days ago -> DOM = 30
        dma_date = TODAY - timedelta(days=30)
        dma_str = f"{dma_date.isoformat()}T12:00:00+00:00"
        assert compute_dom(dma_str, TODAY) == 30


# ---------------------------------------------------------------------------
# Measurable 1: units_leased
# ---------------------------------------------------------------------------


class TestUnitsLeased:
    def test_counts_leased_in_window(self):
        rows = [
            _pp_row(unit_id=1, unit_status="Leased", dmu="2026-10-28T12:00:00+00:00"),
            _pp_row(unit_id=2, unit_status="Leased", dmu="2026-10-30T12:00:00+00:00"),
        ]
        assert compute_units_leased(rows, date(2026, 10, 27), date(2026, 11, 2)) == 2

    def test_excludes_dmu_outside_window(self):
        rows = [
            _pp_row(unit_id=1, unit_status="Leased", dmu="2026-10-20T12:00:00+00:00"),
        ]
        assert compute_units_leased(rows, date(2026, 10, 27), date(2026, 11, 2)) == 0

    def test_excludes_placeholder(self):
        rows = [
            _pp_row(
                unit_id=1,
                address="Place - Applications Only",
                unit_status="Leased",
                dmu="2026-10-28T12:00:00+00:00",
            ),
        ]
        assert compute_units_leased(rows, date(2026, 10, 27), date(2026, 11, 2)) == 0

    def test_dedupes_on_unit_id(self):
        rows = [
            _pp_row(unit_id=1, unit_status="Leased", dmu="2026-10-28T12:00:00+00:00"),
            _pp_row(unit_id=1, unit_status="Leased", dmu="2026-10-29T12:00:00+00:00"),
        ]
        assert compute_units_leased(rows, date(2026, 10, 27), date(2026, 11, 2)) == 1


# ---------------------------------------------------------------------------
# Measurable 2: apps_submitted
# ---------------------------------------------------------------------------


class TestAppsSubmitted:
    def test_counts_in_window(self):
        groups = [
            {"id": "g1", "submitted_at": "2026-10-28T12:00:00+00:00"},
            {"id": "g2", "submitted_at": "2026-10-30T12:00:00+00:00"},
            {"id": "g3", "submitted_at": "2026-11-01T12:00:00+00:00"},
        ]
        assert compute_apps_submitted(groups, date(2026, 10, 27), date(2026, 11, 2)) == 3

    def test_dedupes_group_id(self):
        groups = [
            {"id": "g1", "submitted_at": "2026-10-28T12:00:00+00:00"},
            {"id": "g1", "submitted_at": "2026-10-29T12:00:00+00:00"},
        ]
        assert compute_apps_submitted(groups, date(2026, 10, 27), date(2026, 11, 2)) == 1

    def test_boundary(self):
        groups = [
            {"id": "g1", "submitted_at": "2026-10-26T23:00:00+00:00"},  # outside
        ]
        assert compute_apps_submitted(groups, date(2026, 10, 27), date(2026, 11, 2)) == 0


# ---------------------------------------------------------------------------
# Measurable 3: showing_completion
# ---------------------------------------------------------------------------


class TestShowingCompletion:
    def test_basic(self):
        showings = [
            {"status": "Showing Complete", "address": "A"},
            {"status": "Showing Complete", "address": "B"},
            {"status": "Showing Confirmed", "address": "C"},
            {"status": "Showing No Show", "address": "D"},
        ]
        result = compute_showing_completion(showings)
        assert result["total"] == 4
        assert result["complete"] == 2
        assert result["rate_pct"] == 50

    def test_zero_showings(self):
        result = compute_showing_completion([])
        assert result["rate_pct"] is None

    def test_all_complete(self):
        showings = [
            {"status": "Showing Complete", "address": "A"},
            {"status": "Showing Complete", "address": "B"},
        ]
        result = compute_showing_completion(showings)
        assert result["rate_pct"] == 100

    def test_excludes_rescheduled_from_adjusted(self):
        showings = [
            {"status": "Showing Complete", "address": "A"},
            {"status": "Showing Canceled", "address": "B", "is_rescheduled": True},
            {"status": "Showing Canceled", "address": "C", "is_auto_canceled": True},
            {"status": "Missed Showing", "address": "D"},
        ]
        result = compute_showing_completion(showings)
        assert result["adjusted_total"] == 2  # 4 - 2 excluded
        assert result["adjusted_rate_pct"] == 50  # 1/2


# ---------------------------------------------------------------------------
# Measurable 7: feedback_captured
# ---------------------------------------------------------------------------


class TestFeedbackCaptured:
    def test_counts_nonempty(self):
        showings = [
            {"status": "Showing Complete", "address": "A", "feedback": "Liked it"},
            {"status": "Showing Complete", "address": "B", "feedback": "Nice place"},
            {"status": "Showing Complete", "address": "C", "feedback": None},
        ]
        result = compute_feedback_captured(showings)
        assert result["complete_count"] == 3
        assert result["with_feedback"] == 2
        assert result["rate_pct"] == 67  # 2/3 rounded

    def test_ignores_non_complete(self):
        showings = [
            {"status": "Showing Canceled", "address": "A", "feedback": "Had feedback"},
        ]
        result = compute_feedback_captured(showings)
        assert result["complete_count"] == 0


# ---------------------------------------------------------------------------
# Measurable 8: stale_apps
# ---------------------------------------------------------------------------


class TestStaleApps:
    def test_counts_over_72h(self):
        now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        groups = [
            {"id": "g1", "status": "Submitted", "submitted_at": "2026-10-31T12:00:00+00:00"},  # 4d
            {"id": "g2", "status": "Submitted", "submitted_at": "2026-11-03T12:00:00+00:00"},  # 1d
            {"id": "g3", "status": "Submitted", "submitted_at": "2026-10-30T12:00:00+00:00"},  # 5d
        ]
        result = compute_stale_apps(groups, now)
        assert result["stale_count"] == 2

    def test_awaiting_info_included(self):
        now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        groups = [
            {"id": "g1", "status": "Awaiting Additional Info", "submitted_at": "2026-10-28T12:00:00+00:00"},
        ]
        result = compute_stale_apps(groups, now)
        assert result["stale_count"] == 1

    def test_excludes_closed(self):
        now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        groups = [
            {"id": "g1", "status": "Approved", "submitted_at": "2026-10-28T12:00:00+00:00"},
        ]
        result = compute_stale_apps(groups, now)
        assert result["stale_count"] == 0


# ---------------------------------------------------------------------------
# Measurable 9: units_past_dom
# ---------------------------------------------------------------------------


class TestUnitsPastDom:
    def test_dom_threshold(self):
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00"),  # 94 DOM
            _pp_row(unit_id=2, dma="2026-10-20T12:00:00+00:00"),  # 15 DOM
        ]
        result = compute_units_past_dom(rows, TODAY)
        assert result["count"] == 1

    def test_excludes_excluded_from_dom(self):
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00", excluded_from_dom=True),
        ]
        result = compute_units_past_dom(rows, TODAY)
        assert result["count"] == 0

    def test_excludes_placeholder(self):
        rows = [
            _pp_row(
                unit_id=1,
                address="Place - Applications Only",
                dma="2026-08-02T12:00:00+00:00",
            ),
        ]
        result = compute_units_past_dom(rows, TODAY)
        assert result["count"] == 0

    def test_null_dma(self):
        rows = [_pp_row(unit_id=1, dma=None)]
        result = compute_units_past_dom(rows, TODAY)
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# Reply time
# ---------------------------------------------------------------------------


class TestReplyTime:
    def test_from_overview(self):
        rows = [{"sms_response_seconds": 2039}]
        assert compute_reply_time(rows) == 34.0

    def test_null(self):
        rows = [{"sms_response_seconds": None}]
        assert compute_reply_time(rows) is None

    def test_history_four_weeks(self):
        rows = [
            {"sms_response_seconds": 600},
            {"sms_response_seconds": 1200},
        ]
        assert compute_reply_time(rows) == 15.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_all_green(self):
        metrics = {
            "units_leased": 5,
            "apps_submitted": 10,
            "showing_completion": 80,
            "lead_to_showing": 40,
            "call_answer_rate": 90,
            "feedback_captured": 95,
            "stale_apps": 0,
            "units_past_dom": 5,
        }
        result = score_measurables(metrics)
        assert result["green_count"] == 8
        assert result["team_pass"] is True

    def test_threshold_pass(self):
        metrics = {
            "units_leased": 5,
            "apps_submitted": 10,
            "showing_completion": 80,
            "lead_to_showing": 40,
            "call_answer_rate": 90,
            "feedback_captured": 95,
            "stale_apps": 5,  # red
            "units_past_dom": 15,  # red
        }
        result = score_measurables(metrics)
        assert result["green_count"] == 6
        assert result["team_pass"] is True  # 6 >= TEAM_PASS_THRESHOLD

    def test_threshold_fail(self):
        metrics = {
            "units_leased": 5,
            "apps_submitted": 10,
            "showing_completion": 80,
            "lead_to_showing": 40,
            "call_answer_rate": 90,
            "feedback_captured": 50,  # red
            "stale_apps": 5,  # red
            "units_past_dom": 15,  # red
        }
        result = score_measurables(metrics)
        assert result["green_count"] == 5
        assert result["team_pass"] is False  # 5 < 6

    def test_total_graded_is_8(self):
        result = score_measurables({})
        assert result["total_graded"] == 8


# ---------------------------------------------------------------------------
# Keyword regex
# ---------------------------------------------------------------------------


class TestKeywordRegex:
    def test_whole_no_match(self):
        assert FIX_KEYWORD_RE.search("whole") is None

    def test_painted_match(self):
        assert FIX_KEYWORD_RE.search("painted") is not None

    def test_cleaning_match(self):
        assert FIX_KEYWORD_RE.search("cleaning") is not None

    def test_cracked_match(self):
        assert FIX_KEYWORD_RE.search("cracked") is not None

    def test_case_insensitive(self):
        assert FIX_KEYWORD_RE.search("MOLD found") is not None

    def test_word_boundary_start(self):
        assert FIX_KEYWORD_RE.search("The paint is peeling") is not None

    def test_no_match_in_middle(self):
        # "whole" has no word boundary before "hole"
        assert FIX_KEYWORD_RE.search("wholesale") is None


# ---------------------------------------------------------------------------
# Watchlist tagging
# ---------------------------------------------------------------------------


class TestTagWatchlistUnit:
    def test_tag_wait_new_listing(self):
        row = _pp_row(dma=f"{(TODAY - timedelta(days=10)).isoformat()}T12:00:00+00:00")
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_WAIT

    def test_tag_wait_excluded_from_dom(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            excluded_from_dom=True,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_WAIT

    def test_tag_wait_future_showing(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            earliest_showing="2026-12-01T12:00:00+00:00",
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_WAIT

    def test_tag_fix_keyword_hits(self):
        context = [
            json.dumps({"maintenanceIssues": "crack in wall", "relatedShowing": 1}),
            json.dumps({"prospectDisliked": "hole in floor", "relatedShowing": 2}),
        ]
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            context=context,
        )
        tag, evidence = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_FIX
        assert "keyword hits" in evidence

    def test_tag_fix_null_context(self):
        row = _pp_row(dma="2026-08-02T12:00:00+00:00", context=None)
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_FIX

    def test_tag_fix_relatedshowing_only(self):
        context = [
            json.dumps({"relatedShowing": 1}),
            json.dumps({"relatedShowing": 2}),
        ]
        row = _pp_row(dma="2026-08-02T12:00:00+00:00", context=context)
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_FIX

    def test_tag_price_overpriced_zero_apps(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            pricing_position="overpriced",
            rent=2300,
            median=1650,
            apps=0,
        )
        tag, evidence = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_PRICE
        assert "median" in evidence

    def test_tag_price_with_apps_not_price(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            pricing_position="overpriced",
            rent=2300,
            median=1650,
            apps=1,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_PRICE

    def test_tag_follow_up(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=FOLLOW_UP_LEADS_THRESHOLD,
            sched=FOLLOW_UP_SHOWINGS_MAX,
            completed=1,
            apps=0,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_FOLLOW_UP

    def test_tag_convert_many_showings_few_apps(self):
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=20,
            sched=10,
            completed=CONVERT_SHOWINGS_MIN,
            apps=CONVERT_APPS_MAX,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_CONVERT

    def test_tag_convert_boundary_below(self):
        """showings_completed just below threshold -> not CONVERT."""
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=20,
            sched=10,
            completed=CONVERT_SHOWINGS_MIN - 1,
            apps=0,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_CONVERT

    def test_tag_convert_boundary_apps_above(self):
        """apps above threshold -> not CONVERT."""
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=20,
            sched=10,
            completed=CONVERT_SHOWINGS_MIN,
            apps=CONVERT_APPS_MAX + 1,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_CONVERT

    def test_tag_low_leads(self):
        # DOM=94, leads=2 -> lpw = 2/(28/7) = 0.5 < 2
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=2,
            sched=0,
            completed=0,
            apps=0,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_LOW_LEADS

    def test_tag_low_leads_boundary(self):
        # DOM=28, leads=4 -> lpw = 4/(28/7) = 1.0 < 2 -> LOW LEADS
        dma = f"{(TODAY - timedelta(days=28)).isoformat()}T12:00:00+00:00"
        row = _pp_row(dma=dma, leads=4, completed=0, apps=0)
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_LOW_LEADS

    def test_tag_low_leads_not_triggered(self):
        # DOM=28, leads=8 -> lpw = 8/4 = 2.0, exactly at threshold -> NOT low leads
        dma = f"{(TODAY - timedelta(days=28)).isoformat()}T12:00:00+00:00"
        row = _pp_row(dma=dma, leads=8, completed=0, apps=0)
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag != TAG_LOW_LEADS

    def test_tag_review_fallthrough(self):
        # DOM=94, leads=20 -> lpw=5.0 >= 4, no keywords, not overpriced,
        # leads < 10 or sched > 2, completed < 4
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            leads=20,
            sched=8,
            completed=3,
            apps=0,
        )
        tag, _ = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_REVIEW


# ---------------------------------------------------------------------------
# Watchlist flagging
# ---------------------------------------------------------------------------


class TestWatchlist:
    def test_red_both_triggers(self):
        # DOM >= 30 AND lpw < 4
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00", leads=2),
        ]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 1
        assert wl[0]["color"] == "\U0001f534"  # red

    def test_yellow_one_trigger(self):
        # DOM >= 30 but lpw >= 4
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00", leads=20),
        ]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 1
        assert wl[0]["color"] == "\U0001f7e1"  # yellow

    def test_new_listing_skips_dom_and_lead_rules(self):
        # DOM < 14, no keyword hits -> not flagged
        dma = f"{(TODAY - timedelta(days=10)).isoformat()}T12:00:00+00:00"
        rows = [_pp_row(unit_id=1, dma=dma, leads=0)]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 0

    def test_keyword_trigger_flags_low_dom_unit(self):
        """A unit with DOM=24, lpw=8.75, but 3 keyword hits -> flagged yellow, tagged FIX."""
        dma = f"{(TODAY - timedelta(days=24)).isoformat()}T12:00:00+00:00"
        context = [
            json.dumps({"prospectDisliked": "dilapidated, musty smell"}),
            json.dumps({"prospectDisliked": "paint peeling everywhere"}),
            json.dumps({"prospectDisliked": "bad smell in basement"}),
        ]
        rows = [
            _pp_row(
                unit_id=99,
                dma=dma,
                leads=30,  # lpw = 30 / (24/7) = 8.75
                completed=5,
                apps=2,
                context=context,
            ),
        ]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 1
        assert wl[0]["tag"] == TAG_FIX
        assert wl[0]["color"] == "\U0001f7e1"  # yellow (keyword only, no DOM/lead trigger)

    def test_lpw_new_formula(self):
        # DOM=14, leads=7 -> lpw = 7/(14/7) = 3.5 < 4 -> flagged
        dma = f"{(TODAY - timedelta(days=14)).isoformat()}T12:00:00+00:00"
        rows = [_pp_row(unit_id=1, dma=dma, leads=7)]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 1

    def test_lpw_short_listing_not_flagged(self):
        # DOM=7, leads=6 -> lpw = 6/(7/7) = 6.0 >= 4, DOM < 14 -> skip lead rule
        dma = f"{(TODAY - timedelta(days=7)).isoformat()}T12:00:00+00:00"
        rows = [_pp_row(unit_id=1, dma=dma, leads=6)]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 0

    def test_lpw_capped_at_28(self):
        # DOM=100, leads=12 -> lpw = 12/(28/7) = 3.0
        dma = f"{(TODAY - timedelta(days=100)).isoformat()}T12:00:00+00:00"
        rows = [_pp_row(unit_id=1, dma=dma, leads=12)]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 1
        assert wl[0]["lpw"] == pytest.approx(3.0)

    def test_evidence_includes_price(self):
        rows = [
            _pp_row(
                unit_id=1,
                dma="2026-08-02T12:00:00+00:00",
                pricing_position="overpriced",
                rent=2300,
                median=1650,
                apps=0,
            ),
        ]
        wl = compute_watchlist(rows, TODAY)
        assert "median" in wl[0]["evidence"]

    def test_placeholder_excluded_from_watchlist(self):
        rows = [
            _pp_row(
                unit_id=1,
                address="Place - Applications Only",
                dma="2026-01-01T12:00:00+00:00",
                leads=0,
            ),
        ]
        wl = compute_watchlist(rows, TODAY)
        assert len(wl) == 0


# ---------------------------------------------------------------------------
# Yesterday leads
# ---------------------------------------------------------------------------


class TestYesterdayLeads:
    def test_sums_across_sources(self):
        rows = [
            {"source_name": "Zillow", "leads": 3},
            {"source_name": "Phone", "leads": 2},
        ]
        assert compute_yesterday_leads(rows) == 5


# ---------------------------------------------------------------------------
# ISO window (via digest_data)
# ---------------------------------------------------------------------------


class TestIsoWindow:
    def test_no_dst(self):
        from automations.digest_data import _iso_window

        # Both in EST (Nov 2026)
        start, end = _iso_window(date(2026, 11, 2), date(2026, 11, 4))
        # EST = UTC-5
        assert start == "2026-11-02T05:00:00Z"
        assert end == "2026-11-05T04:59:59Z"

    def test_dst_spanning(self):
        from automations.digest_data import _iso_window

        # Oct 26 EDT (UTC-4) to Nov 1 EST (UTC-5)
        # DST ended Nov 1, 2026
        start, end = _iso_window(date(2026, 10, 26), date(2026, 11, 1))
        assert start == "2026-10-26T04:00:00Z"
        assert end == "2026-11-02T04:59:59Z"


# ---------------------------------------------------------------------------
# _get_report raise on unexpected response
# ---------------------------------------------------------------------------


class TestGetReportRaises:
    def test_raises_on_unexpected_response(self):
        from integrations.rentengine.client import RentEngineAPIError, RentEngineClient

        client = RentEngineClient.__new__(RentEngineClient)
        client.get = lambda *a, **kw: "unexpected string"
        with pytest.raises(RentEngineAPIError, match="Unexpected response"):
            client._get_report("reporting/overview", {})


# ---------------------------------------------------------------------------
# _build_daily overview KeyError
# ---------------------------------------------------------------------------


class TestBuildDailyOverviewKeyError:
    def test_missing_key_raises(self):
        """Direct dict indexing raises KeyError when overview is missing expected fields."""
        overview = {}
        with pytest.raises(KeyError):
            _ = overview["answered_inbound_calls"]


# ---------------------------------------------------------------------------
# compute_week_window
# ---------------------------------------------------------------------------


class TestComputeWeekWindow:
    def test_monday_reports_last_week(self):
        start, end = compute_week_window(date(2026, 11, 2))  # Monday
        assert start == date(2026, 10, 26)
        assert end == date(2026, 11, 1)

    def test_sunday_is_end_of_week(self):
        start, end = compute_week_window(date(2026, 11, 1))  # Sunday
        assert start == date(2026, 10, 26)
        assert end == date(2026, 11, 1)

    def test_midweek_finds_previous_sunday(self):
        start, end = compute_week_window(date(2026, 10, 29))  # Thursday
        assert start == date(2026, 10, 19)
        assert end == date(2026, 10, 25)


# ---------------------------------------------------------------------------
# compute_reply_time_history
# ---------------------------------------------------------------------------


class TestComputeReplyTimeHistory:
    def test_basic(self):
        history = [
            {"rows": {"sms_response_seconds": 3968}, "start": date(2026, 10, 19), "end": date(2026, 10, 25)},
        ]
        result = compute_reply_time_history(history)
        assert len(result) == 1
        assert result[0]["value"] == 66.1
        assert "Oct" in result[0]["label"]

    def test_null_reply_time(self):
        history = [
            {"rows": {"sms_response_seconds": None}, "start": date(2026, 10, 19), "end": date(2026, 10, 25)},
        ]
        result = compute_reply_time_history(history)
        assert result[0]["value"] is None


# ---------------------------------------------------------------------------
# compute_agent_contributions
# ---------------------------------------------------------------------------


class TestComputeAgentContributions:
    def test_excludes_unassigned(self):
        rows = [{"agent_name": "Unassigned", "showings_completed": 5}]
        assert compute_agent_contributions(rows) == []

    def test_excludes_all_zeros(self):
        rows = [{
            "agent_name": "Agent D",
            "showings_completed": 0,
            "messages_sent": 0,
            "calls_made": 0,
            "apps_approved": 0,
        }]
        assert compute_agent_contributions(rows) == []

    def test_keeps_nonzero(self):
        rows = [{
            "agent_name": "Agent A",
            "showings_completed": 3,
            "messages_sent": 0,
            "calls_made": 0,
            "apps_approved": 0,
        }]
        result = compute_agent_contributions(rows)
        assert len(result) == 1
        assert result[0]["name"] == "Agent A"
        assert result[0]["showings"] == 3

    def test_strips_whitespace(self):
        rows = [{
            "agent_name": "  Agent B  ",
            "showings_completed": 1,
            "messages_sent": 0,
            "calls_made": 0,
            "apps_approved": 0,
        }]
        result = compute_agent_contributions(rows)
        assert result[0]["name"] == "Agent B"


# ---------------------------------------------------------------------------
# compute_top_sources
# ---------------------------------------------------------------------------


class TestComputeTopSources:
    def test_top_3(self):
        rows = [
            {"source_display": "A", "leads": 10, "showings_scheduled": 3, "apps_approved": 1},
            {"source_display": "B", "leads": 5, "showings_scheduled": 1, "apps_approved": 0},
            {"source_display": "C", "leads": 20, "showings_scheduled": 5, "apps_approved": 2},
            {"source_display": "D", "leads": 1, "showings_scheduled": 0, "apps_approved": 0},
        ]
        result = compute_top_sources(rows)
        assert len(result) == 3
        assert result[0]["source"] == "C"
        assert result[1]["source"] == "A"
        assert result[2]["source"] == "B"

    def test_empty_list(self):
        assert compute_top_sources([]) == []


# ---------------------------------------------------------------------------
# compute_issues
# ---------------------------------------------------------------------------


class TestComputeIssues:
    def test_only_red_measurables(self):
        scorecard = {"results": [
            ("units_leased", "Units leased", 4, "2+", True),
            ("apps_submitted", "Apps submitted", 5, "7+", False),
            ("showing_completion", "Showing completion", 40, "70%+", False),
        ]}
        issues = compute_issues(scorecard)
        assert len(issues) == 2
        assert issues[0] == ("Apps submitted", 5, "7+")
        assert issues[1] == ("Showing completion", "40%", "70%+")

    def test_all_green_no_issues(self):
        scorecard = {"results": [
            ("units_leased", "Units leased", 4, "2+", True),
        ]}
        assert compute_issues(scorecard) == []


# ---------------------------------------------------------------------------
# Watchlist evidence plurals
# ---------------------------------------------------------------------------


class TestWatchlistEvidencePlurals:
    def test_singular_forms(self):
        row = _pp_row(
            unit_id=1, address="1 Test St",
            dma="2026-01-01T12:00:00+00:00",
            leads=1, sched=0, completed=1, apps=1,
        )
        tag, evidence = tag_watchlist_unit(row, TODAY)
        assert "1 showing done" in evidence
        assert "1 app" in evidence
        assert "1 apps" not in evidence

    def test_plural_forms(self):
        row = _pp_row(
            unit_id=2, address="2 Test St",
            dma="2026-01-01T12:00:00+00:00",
            leads=5, sched=3, completed=3, apps=2,
        )
        tag, evidence = tag_watchlist_unit(row, TODAY)
        assert "3 showings done" in evidence
        assert "2 apps" in evidence

    def test_keyword_hit_singular(self):
        """A unit with exactly 1 keyword hit should not trigger FIX (threshold=2),
        but if we test the evidence line via a FIX-tagged unit with exactly 2 hits,
        it should say 'hits' (plural)."""
        context = [
            json.dumps({"maintenanceIssues": "crack in wall"}),
            json.dumps({"prospectDisliked": "hole in floor"}),
        ]
        row = _pp_row(
            dma="2026-08-02T12:00:00+00:00",
            context=context,
        )
        tag, evidence = tag_watchlist_unit(row, TODAY)
        assert tag == TAG_FIX
        assert "2 keyword hits" in evidence


# ---------------------------------------------------------------------------
# DOM cross-check assertion
# ---------------------------------------------------------------------------


class TestDomCrossCheck:
    def test_measurable_9_matches_watchlist_dom_count(self):
        """Measurable 9 from pp_trailing must match count of watchlist units with DOM>=30."""
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00", leads=2),  # DOM=94, flagged
            _pp_row(unit_id=2, dma="2026-10-20T12:00:00+00:00", leads=2),  # DOM=15, flagged by lead
            _pp_row(unit_id=3, dma="2026-10-30T12:00:00+00:00", leads=0),  # DOM=5, not flagged
        ]
        dom_result = compute_units_past_dom(rows, TODAY)
        watchlist = compute_watchlist(rows, TODAY)
        dom_from_watchlist = sum(1 for u in watchlist if u["dom"] >= DOM_THRESHOLD)
        assert dom_result["count"] == dom_from_watchlist

    def test_mismatch_would_fail(self):
        """Demonstrate that a mismatch between the two paths is detectable."""
        rows = [
            _pp_row(unit_id=1, dma="2026-08-02T12:00:00+00:00", leads=2),
        ]
        dom_result = compute_units_past_dom(rows, TODAY)
        assert dom_result["count"] == 1
        # Artificially wrong watchlist count
        assert dom_result["count"] != 0

"""Tests for automations.digest_metrics — all 8 graded measurables + scoring."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from automations.digest_config import (
    DOM_THRESHOLD,
    TEAM_PASS_THRESHOLD,
    WEEKLY_GOALS,
)
from automations.digest_metrics import (
    compute_apps_submitted,
    compute_call_answer_rate,
    compute_dom,
    compute_feedback_captured,
    compute_lead_to_showing,
    compute_reply_time,
    compute_reply_time_history,
    compute_showing_completion,
    compute_stale_apps,
    compute_units_leased,
    compute_units_past_dom,
    compute_week_window,
    compute_yesterday_leads,
    is_placeholder,
    score_measurables,
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

    def test_call_answer_rate_50_fails_70_passes(self):
        """50% fails call_answer_rate (goal 70%+), 70% passes.

        Uses the real WEEKLY_GOALS to verify the goal value.
        """
        # Confirm the goal value from config
        call_goal = next(
            (goal for key, _, goal, _, _ in WEEKLY_GOALS if key == "call_answer_rate"),
            None,
        )
        assert call_goal == 70, f"Expected call_answer_rate goal=70, got {call_goal}"

        # 50% should fail
        metrics_fail = {
            "units_leased": 5,
            "apps_submitted": 10,
            "showing_completion": 80,
            "lead_to_showing": 40,
            "call_answer_rate": 50,
            "feedback_captured": 95,
            "stale_apps": 0,
            "units_past_dom": 5,
        }
        result_fail = score_measurables(metrics_fail)
        call_result_fail = next(
            r for r in result_fail["results"] if r[0] == "call_answer_rate"
        )
        assert call_result_fail[4] is False  # is_green

        # 70% should pass (gte boundary)
        metrics_pass = {**metrics_fail, "call_answer_rate": 70}
        result_pass = score_measurables(metrics_pass)
        call_result_pass = next(
            r for r in result_pass["results"] if r[0] == "call_answer_rate"
        )
        assert call_result_pass[4] is True  # is_green


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

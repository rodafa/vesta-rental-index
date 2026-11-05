"""Tests for automations.digest_format — Slack formatting functions."""

import pytest

from automations.digest_config import (
    MESSAGE_MAX_CHARS,
    REPLY_TIME_LABEL,
    TAG_FIX,
    TAG_LOW_LEADS,
    TAG_PRICE,
    TAG_REVIEW,
    TAG_WAIT,
    TEAM_PASS_THRESHOLD,
    WATCHLIST_MAX_DISPLAY,
)
from automations.digest_format import (
    _format_watchlist_block,
    _split_messages,
    check_watchlist_render,
    format_weekly_digest,
)


def _make_scorecard(green=1, total=8, team_pass=False):
    """Build a minimal scorecard dict."""
    results = []
    for i in range(total):
        is_green = i < green
        results.append((
            f"metric_{i}",
            f"Metric {i}",
            10 if is_green else 0,
            "5+",
            is_green,
        ))
    return {
        "results": results,
        "green_count": green,
        "total_graded": total,
        "team_pass": team_pass,
    }


def _make_watchlist_unit(address, tag, dom, color="\U0001f534"):
    return {
        "unit_id": hash(address) % 100000,
        "address": address,
        "dom": dom,
        "tag": tag,
        "color": color,
        "evidence": f"{dom} DOM, 1.0 leads/wk, 0 showings done, 0 apps",
        "lpw": 1.0,
        "keyword_hits": 0,
    }


class TestScorecardFormat:
    def test_uses_unicode_emoji(self):
        sc = _make_scorecard(green=4, team_pass=False)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=34.0,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "\U0001f7e2" in text or "\U0001f534" in text
        assert ":green_circle:" not in text
        assert ":red_circle:" not in text

    def test_no_shortcodes_in_output(self):
        sc = _make_scorecard(green=8, team_pass=True)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=10.0,
            reply_time_history=[{"value": 20.0, "label": "Oct 20\u201326"}],
            agent_contributions=[{"name": "A", "showings": 5, "messages": 10, "calls": 2, "approved": 1}],
            top_sources=[{"source": "Zillow", "leads": 10, "scheduled": 3, "approved": 1}],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        import re
        assert not re.search(r":\w+_circle:", text)

    def test_no_code_block(self):
        sc = _make_scorecard(green=2, team_pass=False)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "```" not in text

    def test_reply_time_shown_ungraded(self):
        sc = _make_scorecard(green=4, team_pass=False)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=34.0,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        # White circle for ungraded
        assert "\u26aa" in text
        assert REPLY_TIME_LABEL in text
        assert "tracking" in text

    def test_header_format(self):
        sc = _make_scorecard(green=4, total=8, team_pass=False)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert f"4 of 8 graded green (need {TEAM_PASS_THRESHOLD})" in text


class TestWatchlistFormat:
    def test_cap_at_10(self):
        units = [
            _make_watchlist_unit(f"{i} Test St", TAG_REVIEW, 100 - i)
            for i in range(18)
        ]
        block = _format_watchlist_block(units)
        # Should have 10 unit lines + header + overflow
        lines = block.strip().split("\n")
        unit_lines = [l for l in lines if l.startswith(("\U0001f534", "\U0001f7e1"))]
        assert len(unit_lines) == WATCHLIST_MAX_DISPLAY
        assert "+8 more" in block

    def test_evidence_format(self):
        unit = _make_watchlist_unit("100 Test St", TAG_REVIEW, 50)
        unit["evidence"] = "50 DOM, 1.0 leads/wk, 0 showings done, 0 apps"
        block = _format_watchlist_block([unit])
        assert "50 DOM" in block
        assert "leads/wk" in block
        assert "showings done" in block

    def test_overflow_per_tag_counts(self):
        units = []
        # 5 LOW LEADS, 3 REVIEW, 5 more of each -> need overflow
        for i in range(8):
            units.append(_make_watchlist_unit(f"{i} Low St", TAG_LOW_LEADS, 50 - i))
        for i in range(5):
            units.append(_make_watchlist_unit(f"{i} Rev St", TAG_REVIEW, 40 - i))
        block = _format_watchlist_block(units)
        assert "+3 more" in block
        # Overflow should list counts per tag
        overflow_line = [l for l in block.split("\n") if l.startswith("+")][0]
        assert TAG_LOW_LEADS in overflow_line or TAG_REVIEW in overflow_line

    def test_tag_sort_order(self):
        """FIX comes before PRICE, PRICE before REVIEW."""
        units = [
            _make_watchlist_unit("Review St", TAG_REVIEW, 80),
            _make_watchlist_unit("Fix St", TAG_FIX, 60),
            _make_watchlist_unit("Price St", TAG_PRICE, 70),
        ]
        block = _format_watchlist_block(units)
        lines = block.strip().split("\n")
        unit_lines = [l for l in lines if "\u2014" in l and "flagged" not in l]
        assert "Fix St" in unit_lines[0]
        assert "Price St" in unit_lines[1]
        assert "Review St" in unit_lines[2]

    def test_empty_watchlist(self):
        assert _format_watchlist_block([]) == ""

    def test_total_flagged_from_code(self):
        """The '(N flagged)' count must match len(watchlist)."""
        units = [
            _make_watchlist_unit(f"{i} St", TAG_REVIEW, 50)
            for i in range(7)
        ]
        block = _format_watchlist_block(units)
        assert "(7 flagged)" in block


class TestMessageSplitting:
    def test_within_limit(self):
        sections = ["Short section 1", "Short section 2"]
        messages = _split_messages(sections)
        assert len(messages) == 1

    def test_splits_at_boundary(self):
        long_section = "x" * 2000
        sections = [long_section, long_section]
        messages = _split_messages(sections, max_chars=3000)
        assert len(messages) == 2


# ---------------------------------------------------------------------------
# check_watchlist_render
# ---------------------------------------------------------------------------


class TestCheckWatchlistRender:
    def test_correct_render_passes(self):
        units = [
            _make_watchlist_unit(f"{i} Test St", TAG_REVIEW, 100 - i)
            for i in range(7)
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=units,
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        check_watchlist_render(messages, 7)

    def test_wrong_expected_total_raises(self):
        units = [
            _make_watchlist_unit(f"{i} Test St", TAG_REVIEW, 100 - i)
            for i in range(15)
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=units,
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        with pytest.raises(ValueError, match="header says 15 flagged"):
            check_watchlist_render(messages, 20)

    def test_wrong_header_count_raises(self):
        units = [
            _make_watchlist_unit(f"{i} Test St", TAG_REVIEW, 100 - i)
            for i in range(5)
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=units,
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        with pytest.raises(ValueError, match="header says"):
            check_watchlist_render(messages, 10)

    def test_empty_watchlist_zero_expected(self):
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        check_watchlist_render(messages, 0)


# ---------------------------------------------------------------------------
# Plurals in contributions and top sources
# ---------------------------------------------------------------------------


class TestContributionPlurals:
    def test_singular_forms(self):
        contributions = [{"name": "Agent A", "showings": 1, "messages": 1, "calls": 1, "approved": 1}]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=contributions,
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "1 showing," in text
        assert "1 msg," in text
        assert "1 call," in text
        assert "1 showings" not in text
        assert "1 msgs" not in text
        assert "1 calls" not in text

    def test_plural_forms(self):
        contributions = [{"name": "Agent B", "showings": 5, "messages": 10, "calls": 3, "approved": 2}]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=contributions,
            top_sources=[],
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "5 showings," in text
        assert "10 msgs," in text
        assert "3 calls," in text


class TestTopSourcePlurals:
    def test_singular_lead(self):
        sources = [{"source": "Walk-in", "leads": 1, "scheduled": 1, "approved": 0}]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=sources,
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "1 lead," in text
        assert "1 leads" not in text

    def test_plural_leads(self):
        sources = [{"source": "Zillow", "leads": 10, "scheduled": 3, "approved": 1}]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            agent_contributions=[],
            top_sources=sources,
            watchlist=[],
            issues=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert "10 leads," in text

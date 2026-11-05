"""Tests for automations.digest_format — Slack formatting functions."""

import pytest

from automations.digest_config import (
    DOM_LIST_MAX_DISPLAY,
    MESSAGE_MAX_CHARS,
    REPLY_TIME_LABEL,
    TEAM_PASS_THRESHOLD,
)
from automations.digest_format import (
    _split_messages,
    check_dom_list_render,
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


class TestScorecardFormat:
    def test_uses_unicode_emoji(self):
        sc = _make_scorecard(green=4, team_pass=False)
        messages = format_weekly_digest(
            scorecard=sc,
            reply_time=34.0,
            reply_time_history=[],
            dom_units=[],
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
            dom_units=[],
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
            dom_units=[],
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
            dom_units=[],
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
            dom_units=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        text = "\n".join(messages)
        assert f"4 of 8 graded green (need {TEAM_PASS_THRESHOLD})" in text


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
# check_dom_list_render
# ---------------------------------------------------------------------------


class TestCheckDomListRender:
    def test_correct_render_passes(self):
        """DOM list with 3 units renders correctly and check passes."""
        dom_units = [
            {"address": "100 Test St", "dom": 94},
            {"address": "200 Test St", "dom": 75},
            {"address": "300 Test St", "dom": 50},
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            dom_units=dom_units,
            week_label="Oct 27 \u2013 Nov 2",
        )
        check_dom_list_render(messages, 3)

    def test_wrong_overflow_raises(self):
        """Overflow mismatch raises ValueError."""
        # Create DOM_LIST_MAX_DISPLAY + 5 units
        dom_units = [
            {"address": f"{i} Test St", "dom": 100 - i}
            for i in range(DOM_LIST_MAX_DISPLAY + 5)
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            dom_units=dom_units,
            week_label="Oct 27 \u2013 Nov 2",
        )
        # Correct total should be DOM_LIST_MAX_DISPLAY + 5
        # Pass wrong total
        with pytest.raises(ValueError, match="!="):
            check_dom_list_render(messages, DOM_LIST_MAX_DISPLAY + 10)

    def test_wrong_total_raises(self):
        """Wrong expected total raises ValueError."""
        dom_units = [
            {"address": "100 Test St", "dom": 94},
            {"address": "200 Test St", "dom": 75},
        ]
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            dom_units=dom_units,
            week_label="Oct 27 \u2013 Nov 2",
        )
        with pytest.raises(ValueError, match="!="):
            check_dom_list_render(messages, 5)

    def test_empty_list_zero_expected(self):
        """Empty DOM list with expected_total=0 passes."""
        messages = format_weekly_digest(
            scorecard=_make_scorecard(),
            reply_time=None,
            reply_time_history=[],
            dom_units=[],
            week_label="Oct 27 \u2013 Nov 2",
        )
        check_dom_list_render(messages, 0)

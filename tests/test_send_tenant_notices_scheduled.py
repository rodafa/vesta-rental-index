"""
Tests for the --scheduled gate in send_tenant_notices.

These tests verify dispatch control only — the selector, balance logic,
templates, and send path are fully mocked.  The gate must:
  - Allow 'reminder' on day 5 in --scheduled mode.
  - Withhold 'pay_or_quit' (day 6) and 'final' (day 11) in --scheduled mode.
  - Send nothing on non-cadence days in --scheduled mode.
  - Leave human (non-scheduled) runs unchanged on every cadence day.
"""

from io import StringIO
from unittest.mock import patch, MagicMock

import pytest
from django.core.management import call_command


# All tests mock the selector so no DB or API calls happen.
SELECTOR_PATH = (
    "automations.management.commands.send_tenant_notices"
    ".get_delinquent_leases"
)


def _call(as_of, *, scheduled=False):
    """Run the command with --dry-run and capture stdout."""
    out = StringIO()
    args = ["send_tenant_notices", "--dry-run", "--as-of", as_of]
    if scheduled:
        args.append("--scheduled")
    call_command(*args, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# --scheduled mode
# ---------------------------------------------------------------------------


@patch(SELECTOR_PATH)
def test_scheduled_day5_reaches_selector(mock_selector):
    """--scheduled on day 5: the gate allows 'reminder' through."""
    mock_selector.return_value = {
        "delinquent": [],
        "skipped_no_lease_id": 0,
        "skipped_voided": 0,
        "skipped_below_minimum": 0,
        "skipped_no_address": 0,
        "skipped_status_excluded": 0,
        "skipped_status_unknown": 0,
        "total_charges_scanned": 0,
        "display_only_charges_scanned": 0,
        "display_only_skipped_voided": 0,
        "display_only_skipped_no_lease_id": 0,
    }
    output = _call("2026-10-05", scheduled=True)
    mock_selector.assert_called_once()
    assert "kind=reminder" in output
    assert "scheduled=True" in output


def test_scheduled_day6_withholds_pay_or_quit():
    """--scheduled on day 6: gate withholds pay_or_quit, selector never called."""
    output = _call("2026-10-06", scheduled=True)
    assert "withheld 'pay_or_quit'" in output
    assert "Only 'reminder' is permitted unattended" in output


def test_scheduled_day11_withholds_final():
    """--scheduled on day 11: gate withholds final, selector never called."""
    output = _call("2026-10-11", scheduled=True)
    assert "withheld 'final'" in output
    assert "Only 'reminder' is permitted unattended" in output


def test_scheduled_off_day_sends_nothing():
    """--scheduled on a non-cadence day: exits at the existing 'not a cadence day' check."""
    output = _call("2026-10-03", scheduled=True)
    assert "not a cadence day" in output


# ---------------------------------------------------------------------------
# Human (non-scheduled) mode — unchanged behavior
# ---------------------------------------------------------------------------


@patch(SELECTOR_PATH)
def test_human_day6_selects_pay_or_quit(mock_selector):
    """Without --scheduled, day 6 selects pay_or_quit normally."""
    mock_selector.return_value = {
        "delinquent": [],
        "skipped_no_lease_id": 0,
        "skipped_voided": 0,
        "skipped_below_minimum": 0,
        "skipped_no_address": 0,
        "skipped_status_excluded": 0,
        "skipped_status_unknown": 0,
        "total_charges_scanned": 0,
        "display_only_charges_scanned": 0,
        "display_only_skipped_voided": 0,
        "display_only_skipped_no_lease_id": 0,
    }
    output = _call("2026-10-06", scheduled=False)
    mock_selector.assert_called_once()
    assert "kind=pay_or_quit" in output
    assert "scheduled=False" in output


@patch(SELECTOR_PATH)
def test_human_day11_selects_final(mock_selector):
    """Without --scheduled, day 11 selects final normally."""
    mock_selector.return_value = {
        "delinquent": [],
        "skipped_no_lease_id": 0,
        "skipped_voided": 0,
        "skipped_below_minimum": 0,
        "skipped_no_address": 0,
        "skipped_status_excluded": 0,
        "skipped_status_unknown": 0,
        "total_charges_scanned": 0,
        "display_only_charges_scanned": 0,
        "display_only_skipped_voided": 0,
        "display_only_skipped_no_lease_id": 0,
    }
    output = _call("2026-10-11", scheduled=False)
    mock_selector.assert_called_once()
    assert "kind=final" in output
    assert "scheduled=False" in output

"""
Tests for the --lease-id filter in send_tenant_notices.

These tests verify single-lease targeting only — the selector, balance
logic, templates, and send path are fully mocked.  The filter must:
  - Process only the named lease when it is in the delinquent set.
  - Send nothing when the named lease is not in the delinquent set.
  - Reject --lease-id combined with --scheduled.
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


SELECTOR_PATH = (
    "automations.management.commands.send_tenant_notices"
    ".get_delinquent_leases"
)

RESOLVE_EMAILS_PATH = (
    "automations.management.commands.send_tenant_notices"
    ".resolve_lease_tenant_emails"
)

RENDER_PATH = (
    "automations.management.commands.send_tenant_notices"
    ".render_to_string"
)


def _selector_result(delinquent=None):
    """Build a minimal selector return value."""
    return {
        "delinquent": delinquent or [],
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


def _delinquent_entry(rv_lease_id, address="123 Main St"):
    """Build a minimal delinquent-lease dict."""
    return {
        "rentvine_lease_id": rv_lease_id,
        "balance": 500.00,
        "display_balance": 500.00,
        "property_address": address,
        "lease_status_id": 1,
    }


# ---------------------------------------------------------------------------
# --lease-id targets exactly one lease
# ---------------------------------------------------------------------------


@patch(RENDER_PATH, return_value="<html>preview</html>")
@patch(RESOLVE_EMAILS_PATH, return_value={"emails": ["t@example.com"], "missing_recipients": []})
@patch(SELECTOR_PATH)
def test_lease_id_in_set_only_processes_target(mock_selector, _mock_emails, _mock_render):
    """When --lease-id names a lease in the set, only that lease is processed."""
    mock_selector.return_value = _selector_result(
        delinquent=[
            _delinquent_entry(161),
            _delinquent_entry(205),
            _delinquent_entry(310),
        ]
    )
    out = StringIO()
    call_command(
        "send_tenant_notices", "--dry-run",
        "--as-of", "2026-10-05",
        "--lease-id", "161",
        stdout=out,
    )
    output = out.getvalue()
    assert "Single-lease mode: targeting Rentvine lease 161" in output
    # Only lease 161 should appear in preview lines
    assert "Lease 161:" in output
    assert "Lease 205:" not in output
    assert "Lease 310:" not in output


# ---------------------------------------------------------------------------
# --lease-id with absent lease sends nothing
# ---------------------------------------------------------------------------


@patch(SELECTOR_PATH)
def test_lease_id_not_in_set_sends_nothing(mock_selector):
    """When --lease-id names a lease NOT in the set, exit 0 with explanation."""
    mock_selector.return_value = _selector_result(
        delinquent=[
            _delinquent_entry(205),
            _delinquent_entry(310),
        ]
    )
    out = StringIO()
    call_command(
        "send_tenant_notices", "--dry-run",
        "--as-of", "2026-10-05",
        "--lease-id", "161",
        stdout=out,
    )
    output = out.getvalue()
    assert "Lease 161 not in delinquent set" in output
    assert "2 leases qualified" in output


# ---------------------------------------------------------------------------
# --lease-id + --scheduled is rejected
# ---------------------------------------------------------------------------


def test_lease_id_with_scheduled_rejected():
    """--lease-id and --scheduled together raise CommandError."""
    out = StringIO()
    with pytest.raises(CommandError, match="mutually exclusive"):
        call_command(
            "send_tenant_notices", "--dry-run",
            "--as-of", "2026-10-05",
            "--lease-id", "161",
            "--scheduled",
            stdout=out,
        )

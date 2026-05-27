"""
Tests for group_properties_by_owner() and build_send_preview().

Covers: person vs entity filtering, no-email exclusion, co-ownership,
entity-only portfolios, preview shape, and send idempotency.
"""

import datetime
from unittest.mock import patch

import pytest

from dashboard.models import PropertyWeeklyNote
from properties.models import Owner, Portfolio, Property, Unit
from weekly_reports.models import OwnerEmailSend
from weekly_reports.services.send_owner_emails import (
    build_send_preview,
    group_properties_by_owner,
    send_approved_emails,
)

# -- Dates ------------------------------------------------------------------
# Tuesday–Monday reporting window; Monday anchor for week_date.
WEEK_START = datetime.date(2026, 5, 19)  # Tuesday
WEEK_END = datetime.date(2026, 5, 25)    # Monday
MONDAY = datetime.date(2026, 5, 18)      # Monday anchor


def _make_portfolio(suffix, rentvine_id):
    return Portfolio.objects.create(
        rentvine_id=rentvine_id,
        name=f"Portfolio {suffix}",
    )


def _make_property(portfolio, address):
    return Property.objects.create(
        portfolio=portfolio,
        address_line_1=address,
    )


def _make_unit(prop, address=None):
    return Unit.objects.create(
        property=prop,
        address_line_1=address or prop.address_line_1,
        is_active=True,
    )


def _make_owner(name, email, rentvine_id, portfolios):
    owner = Owner.objects.create(
        rentvine_contact_id=rentvine_id,
        name=name,
        email=email,
        is_active=True,
    )
    owner.portfolios.set(portfolios)
    return owner


def _make_approved_note(unit):
    return PropertyWeeklyNote.objects.create(
        unit=unit,
        week_date=MONDAY,
        author="test",
        note_text="Good activity this week.",
        approved=True,
    )


def _mock_report_for_units(units):
    """Return a marketing-report dict whose rows match the given units."""
    rows = [
        {
            "unit_id": u.id,
            "address": u.display_address,
            "note_approved": True,
            "leads": 3,
            "showings": 2,
            "apps": 1,
        }
        for u in units
    ]
    return {"rows": rows, "benchmarks": {"avg_dom": 14}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def person_setup(db):
    """Single person-owner with one portfolio / property / unit / approved note."""
    pf = _make_portfolio("A", rentvine_id=9001)
    prop = _make_property(pf, "100 Elm St")
    unit = _make_unit(prop)
    owner = _make_owner("Jane Smith", "jane@example.com", 1001, [pf])
    _make_approved_note(unit)
    return {"portfolio": pf, "unit": unit, "owner": owner}


@pytest.fixture()
def entity_setup(db):
    """Single LLC-owner with one portfolio / property / unit / approved note."""
    pf = _make_portfolio("B", rentvine_id=9002)
    prop = _make_property(pf, "200 Oak Ave")
    unit = _make_unit(prop)
    owner = _make_owner("Acme Holdings LLC", "acme@example.com", 1002, [pf])
    _make_approved_note(unit)
    return {"portfolio": pf, "unit": unit, "owner": owner}


@pytest.fixture()
def no_email_setup(db):
    """Person-owner with no email address."""
    pf = _make_portfolio("C", rentvine_id=9003)
    prop = _make_property(pf, "300 Pine Rd")
    unit = _make_unit(prop)
    owner = _make_owner("Bob Jones", "", 1003, [pf])
    _make_approved_note(unit)
    return {"portfolio": pf, "unit": unit, "owner": owner}


@pytest.fixture()
def coowned_setup(db):
    """Portfolio co-owned by a person and an LLC."""
    pf = _make_portfolio("D", rentvine_id=9004)
    prop = _make_property(pf, "400 Maple Dr")
    unit = _make_unit(prop)
    person = _make_owner("Crom Carey", "crom@example.com", 1004, [pf])
    entity = _make_owner("Carey Properties LLC", "carey@llc.com", 1005, [pf])
    _make_approved_note(unit)
    return {"portfolio": pf, "unit": unit, "person": person, "entity": entity}


@pytest.fixture()
def entity_only_setup(db):
    """Portfolio whose only owners are entities."""
    pf = _make_portfolio("E", rentvine_id=9005)
    prop = _make_property(pf, "500 Birch Ln")
    unit = _make_unit(prop)
    _make_owner("Alpha Investments LLC", "alpha@inv.com", 1006, [pf])
    _make_owner("Beta Ventures Corp", "beta@vent.com", 1007, [pf])
    _make_approved_note(unit)
    return {"portfolio": pf, "unit": unit}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_units_from(*fixtures):
    """Collect all units from fixture dicts."""
    return [f["unit"] for f in fixtures]


def _patch_report(units):
    """Return a patch context manager that mocks build_marketing_report."""
    report = _mock_report_for_units(units)
    return patch(
        "weekly_reports.services.send_owner_emails.build_marketing_report",
        return_value=report,
    )


# ---------------------------------------------------------------------------
# Tests — group_properties_by_owner
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGroupPropertiesByOwner:

    def test_person_owner_in_recipients(self, person_setup):
        units = [person_setup["unit"]]
        with _patch_report(units):
            result = group_properties_by_owner(WEEK_START, WEEK_END)

        names = [r["owner"].name for r in result["recipients"]]
        assert "Jane Smith" in names
        assert len(result["skipped"]) == 0

    def test_entity_owner_in_skipped_with_reason(self, entity_setup):
        units = [entity_setup["unit"]]
        with _patch_report(units):
            result = group_properties_by_owner(WEEK_START, WEEK_END)

        assert len(result["recipients"]) == 0
        assert len(result["skipped"]) == 1
        skipped = result["skipped"][0]
        assert skipped["name"] == "Acme Holdings LLC"
        assert "entity" in skipped["reason"].lower()

    def test_no_email_owner_in_skipped_with_reason(self, no_email_setup):
        units = [no_email_setup["unit"]]
        with _patch_report(units):
            result = group_properties_by_owner(WEEK_START, WEEK_END)

        assert len(result["recipients"]) == 0
        assert len(result["skipped"]) == 1
        skipped = result["skipped"][0]
        assert skipped["name"] == "Bob Jones"
        assert "no email" in skipped["reason"].lower()

    def test_coowned_person_receives_entity_skipped(self, coowned_setup):
        units = [coowned_setup["unit"]]
        with _patch_report(units):
            result = group_properties_by_owner(WEEK_START, WEEK_END)

        recipient_names = [r["owner"].name for r in result["recipients"]]
        skipped_names = [s["name"] for s in result["skipped"]]

        # Person gets the email
        assert "Crom Carey" in recipient_names
        # LLC is skipped
        assert "Carey Properties LLC" in skipped_names

        # The property appears in the person's email
        person_entry = next(r for r in result["recipients"] if r["owner"].name == "Crom Carey")
        assert any("400 Maple Dr" in addr for addr in person_entry["properties"])

    def test_entity_only_portfolio_skipped(self, entity_only_setup):
        units = [entity_only_setup["unit"]]
        with _patch_report(units):
            result = group_properties_by_owner(WEEK_START, WEEK_END)

        assert len(result["recipients"]) == 0
        assert len(result["skipped"]) == 2
        skipped_names = {s["name"] for s in result["skipped"]}
        assert "Alpha Investments LLC" in skipped_names
        assert "Beta Ventures Corp" in skipped_names


# ---------------------------------------------------------------------------
# Tests — build_send_preview
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildSendPreview:

    def test_preview_shape(self, person_setup, entity_setup, no_email_setup):
        units = _all_units_from(person_setup, entity_setup, no_email_setup)
        with _patch_report(units):
            preview = build_send_preview(WEEK_START, WEEK_END)

        # One real recipient (Jane Smith)
        assert preview["total_recipients"] == 1
        assert preview["recipients"][0]["name"] == "Jane Smith"
        assert preview["recipients"][0]["email"] == "jane@example.com"
        assert "owner_id" in preview["recipients"][0]

        # Two skipped (LLC + no-email)
        assert len(preview["skipped"]) == 2
        skipped_names = {s["name"] for s in preview["skipped"]}
        assert "Acme Holdings LLC" in skipped_names
        assert "Bob Jones" in skipped_names


# ---------------------------------------------------------------------------
# Tests — send idempotency
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSendIdempotency:

    def test_already_sent_owner_is_skipped(self, person_setup):
        owner = person_setup["owner"]
        units = [person_setup["unit"]]

        # Pre-create a "sent" record for this owner + week
        OwnerEmailSend.objects.create(
            owner=owner,
            week_date=MONDAY,
            status="sent",
            recipient_name=owner.name,
            recipient_email=owner.email,
        )

        with _patch_report(units), \
             patch("weekly_reports.services.send_owner_emails.build_owner_email_html", return_value="<html></html>"):
            result = send_approved_emails(WEEK_START, WEEK_END, dry_run=False)

        assert result["sent"] == 0
        assert result["skipped"] == 1

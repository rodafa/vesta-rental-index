"""
Tests for webhook-driven DailyLeasingMetric increments via the
CountedLeasingEvent idempotency ledger.

Covers: mutating events, duplicate suppression, multiple countable types
on the same event, and Eastern timezone date boundaries.
"""

from datetime import date

import pytest

from integrations.models import WebhookEvent
from integrations.rentengine.processors import process_webhook_event
from leasing.models import CountedLeasingEvent, DailyLeasingMetric
from properties.models import Property, Unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prop(db):
    return Property.objects.create(
        rentvine_id=1,
        name="16 Fortunate Drive",
        address_line_1="16 Fortunate Drive",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=100,
        rentengine_id=5001,
        name="Unit B",
        address_line_1="16 Fortunate Drive",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


def _make_webhook(rentengine_id, event_type, timestamp, unit_rentengine_id):
    """Helper to create a WebhookEvent for a leasing event."""
    return WebhookEvent.objects.create(
        source="rentengine",
        event_type="INSERT",
        table_name="leasing_events",
        record={
            "id": rentengine_id,
            "event_type": event_type,
            "created_at": timestamp,
            "unit_id": unit_rentengine_id,
        },
    )


# ---------------------------------------------------------------------------
# TestMutatingLeasingEvent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMutatingLeasingEvent:
    """
    A non-countable event type first, then the same rentengine_id
    transitions to a countable type — should increment exactly once.
    """

    def test_non_countable_then_countable(self, unit):
        # First: "showing_scheduled" is not in _COUNTABLE_EVENT_TYPES
        ev1 = _make_webhook(
            rentengine_id=1001,
            event_type="Showing Scheduled",
            timestamp="2026-05-20T14:00:00Z",
            unit_rentengine_id=unit.rentengine_id,
        )
        process_webhook_event(ev1)

        assert DailyLeasingMetric.objects.count() == 0
        assert CountedLeasingEvent.objects.count() == 0

        # Same rentengine_id transitions to "Showing Complete" (countable)
        ev2 = WebhookEvent.objects.create(
            source="rentengine",
            event_type="UPDATE",
            table_name="leasing_events",
            record={
                "id": 1001,
                "event_type": "Showing Complete",
                "created_at": "2026-05-20T15:00:00Z",
                "unit_id": unit.rentengine_id,
            },
        )
        process_webhook_event(ev2)

        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 20))
        assert metric.showings_completed == 1
        assert CountedLeasingEvent.objects.count() == 1


# ---------------------------------------------------------------------------
# TestDuplicateCountableEvent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDuplicateCountableEvent:
    """
    Processing the same countable event twice should not double-count.
    """

    def test_duplicate_new_event(self, unit):
        ev1 = _make_webhook(
            rentengine_id=2001,
            event_type="New",
            timestamp="2026-05-21T10:00:00Z",
            unit_rentengine_id=unit.rentengine_id,
        )
        process_webhook_event(ev1)

        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 21))
        assert metric.new_prospects == 1

        # Same event replayed
        ev2 = WebhookEvent.objects.create(
            source="rentengine",
            event_type="UPDATE",
            table_name="leasing_events",
            record={
                "id": 2001,
                "event_type": "New",
                "created_at": "2026-05-21T10:00:00Z",
                "unit_id": unit.rentengine_id,
            },
        )
        process_webhook_event(ev2)

        metric.refresh_from_db()
        assert metric.new_prospects == 1
        assert CountedLeasingEvent.objects.count() == 1


# ---------------------------------------------------------------------------
# TestTwoCountableTypesOnSameEvent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTwoCountableTypesOnSameEvent:
    """
    Same rentengine_id transitions through two different countable types.
    Each type should increment its own counter exactly once.
    """

    def test_new_then_showing_complete(self, unit):
        # First: "New" → new_prospects = 1
        ev1 = _make_webhook(
            rentengine_id=3001,
            event_type="New",
            timestamp="2026-05-22T09:00:00Z",
            unit_rentengine_id=unit.rentengine_id,
        )
        process_webhook_event(ev1)

        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 22))
        assert metric.new_prospects == 1
        assert metric.showings_completed == 0

        # Same rentengine_id transitions to "Showing Complete"
        ev2 = WebhookEvent.objects.create(
            source="rentengine",
            event_type="UPDATE",
            table_name="leasing_events",
            record={
                "id": 3001,
                "event_type": "Showing Complete",
                "created_at": "2026-05-22T11:00:00Z",
                "unit_id": unit.rentengine_id,
            },
        )
        process_webhook_event(ev2)

        metric.refresh_from_db()
        assert metric.new_prospects == 1
        assert metric.showings_completed == 1
        assert CountedLeasingEvent.objects.count() == 2


# ---------------------------------------------------------------------------
# TestEasternDateBoundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEasternDateBoundary:
    """
    Event at 11:30 PM ET (03:30 UTC next day) should key to the ET date,
    not the UTC date.
    """

    def test_late_night_eastern_event(self, unit):
        # 2026-05-22 23:30 ET = 2026-05-23 03:30 UTC
        ev = _make_webhook(
            rentengine_id=4001,
            event_type="New",
            timestamp="2026-05-23T03:30:00Z",
            unit_rentengine_id=unit.rentengine_id,
        )
        process_webhook_event(ev)

        # Should be keyed to May 22 (Eastern), not May 23 (UTC)
        assert DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 5, 22)
        ).exists()
        assert not DailyLeasingMetric.objects.filter(
            unit=unit, date=date(2026, 5, 23)
        ).exists()

        metric = DailyLeasingMetric.objects.get(unit=unit, date=date(2026, 5, 22))
        assert metric.new_prospects == 1
        assert metric.source == "webhook_realtime"

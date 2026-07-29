from django.db import models


class Prospect(models.Model):
    """
    A leasing prospect from RentEngine.

    Linked to core.Unit via unit_of_interest (RentEngine unit ID).
    """

    rentengine_id = models.IntegerField(unique=True, db_index=True)

    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prospects",
    )

    name = models.CharField(max_length=255, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=100, blank=True, default="", db_index=True)

    # Current status from RentEngine, not history.
    # History lives in LeasingEvent.
    status = models.CharField(max_length=100, blank=True, default="", db_index=True)

    prospect_type = models.CharField(max_length=50, blank=True, default="")

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Prospect #{self.rentengine_id}"


class LeasingEvent(models.Model):
    """
    A single leasing event from RentEngine, tied to a prospect and a unit.
    """

    rentengine_id = models.IntegerField(unique=True, db_index=True)
    """RentEngine's event id."""

    prospect = models.ForeignKey(
        Prospect,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )

    # Unit is denormalized from the event's own unit_of_interest,
    # NOT read through the prospect, because a person can be a
    # prospect on multiple units.
    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leasing_events",
    )

    # Deliberately free text, NOT a choices enum, because RentEngine
    # can add event types at any time and we must never drop an event.
    event_type = models.CharField(max_length=100, db_index=True)

    event_timestamp = models.DateTimeField(db_index=True)
    event_date = models.DateField(db_index=True)
    """Date portion of event_timestamp, for period queries."""

    source = models.CharField(max_length=100, blank=True, default="")
    created_by = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    planned_date_time = models.DateTimeField(null=True, blank=True)
    next_follow_up = models.DateTimeField(null=True, blank=True)
    property_address = models.CharField(max_length=500, blank=True, default="")

    context = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_timestamp"]
        indexes = [
            models.Index(
                fields=["unit", "event_date"],
                name="ix_leasevent_unit_date",
            ),
            models.Index(
                fields=["unit", "event_type", "event_date"],
                name="ix_leasevent_unit_type_date",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} @ {self.event_timestamp}"

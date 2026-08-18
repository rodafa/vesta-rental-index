from django.core.serializers.json import DjangoJSONEncoder
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


class RentEngineWebhookDelivery(models.Model):
    """
    Append-only raw inbox. Every authenticated request is stored before any
    parsing is attempted, so an unrecognized payload shape is never lost.
    """

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    target_entity = models.CharField(
        max_length=100, blank=True, default="", db_index=True
    )
    operation = models.CharField(
        max_length=50, blank=True, default="", db_index=True
    )
    raw_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        db_index=True,
        choices=[
            ("processed", "Processed"),
            ("unparseable", "Unparseable"),
            ("ignored", "Ignored"),
        ],
    )
    error_message = models.TextField(blank=True, default="")
    resulting_event = models.ForeignKey(
        LeasingEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_deliveries",
    )
    resulting_prospect = models.ForeignKey(
        Prospect,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_deliveries",
    )

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.target_entity}/{self.operation} @ {self.received_at}"


class UnitLeasingSnapshot(models.Model):
    """
    Weekly leasing performance snapshot for a unit.

    Fields split into two groups:
    - Computed from our own LeasingEvent / Prospect rows (new_leads through
      applications_received). These are always populated and default to 0.
    - Fetched from RentEngine's reporting endpoint (touchpoints_calls through
      reporting_raw). These are nullable because a failed fetch must not
      fabricate a zero — null means unknown, zero means none.
    """

    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.CASCADE,
        related_name="leasing_snapshots",
    )
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)

    # --- Computed from our own LeasingEvent / Prospect rows ---
    new_leads = models.IntegerField(default=0)
    showings_scheduled = models.IntegerField(default=0)
    showings_completed = models.IntegerField(default=0)
    showings_canceled = models.IntegerField(default=0)
    showings_missed = models.IntegerField(default=0)
    applications_received = models.IntegerField(default=0)

    # --- Fetched from RentEngine's reporting endpoint ---
    touchpoints_calls = models.IntegerField(null=True, blank=True)
    touchpoints_texts = models.IntegerField(null=True, blank=True)
    upcoming_showings = models.IntegerField(null=True, blank=True)
    days_on_market = models.IntegerField(
        null=True, blank=True,
        help_text=(
            "Lifetime days-on-market from RentEngine. Retained in reporting_raw "
            "for comparison but no longer used in user-facing output — replaced "
            "by advertised_date from RentVine."
        ),
    )
    advertised_date = models.DateField(
        null=True, blank=True,
        help_text=(
            "Start date of the current RentVine listing (listing.advertisedDate). "
            "Null means no active listing was found. Used to compute days on market "
            "as (period_end - advertised_date).days."
        ),
    )
    property_health = models.CharField(max_length=50, blank=True, default="")
    reporting_fetch_ok = models.BooleanField(default=False)
    reporting_error = models.TextField(blank=True, default="")
    reporting_raw = models.JSONField(default=dict, blank=True)

    # --- Audit ---
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("unit", "period_start", "period_end")
        ordering = ["-period_start", "unit_id"]

    def __str__(self):
        return f"Unit {self.unit_id} | {self.period_start} \u2013 {self.period_end}"


class PortfolioLeasingNote(models.Model):
    """
    Layer 1 — portfolio-grain authored content for weekly leasing notes.

    One note per portfolio per period, authored and reviewed ONCE.
    A portfolio shared by several owners is written once here.
    Owner-grain leasing emails are ASSEMBLED from these rows at send time.
    """

    PERIOD_TYPE_CHOICES = [
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("approved", "Approved"),
    ]

    portfolio = models.ForeignKey(
        "core.Portfolio",
        on_delete=models.CASCADE,
        related_name="leasing_notes",
    )
    period_type = models.CharField(
        max_length=10,
        choices=PERIOD_TYPE_CHOICES,
        default="weekly",
        db_index=True,
    )
    period_start = models.DateField()
    period_end = models.DateField()

    notes_html = models.TextField(
        blank=True,
        default="",
        help_text="Rendered HTML fragment for the leasing section (inline-styled for SendGrid).",
    )
    generated_note = models.TextField(
        blank=True,
        default="",
        help_text="AI-generated plain-text note (the raw output, before human review).",
    )
    approved_generated_note = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Human-reviewed/edited version of generated_note. Blank = not yet reviewed. "
            "NOT used by the dashboard edit path (preserved for future use)."
        ),
    )
    unit_snapshot = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text=(
            "Frozen per-unit context including prior-week values, captured at "
            "generation. Source for rebuilding notes_html on edit."
        ),
    )
    edited_notes = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text=(
            "Per-unit edited AI note text, keyed by str(unit_id). "
            "Overrides ai_result.unit_summaries when re-rendering notes_html."
        ),
    )

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft", db_index=True
    )
    is_edited = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "True once a human has edited this note's snapshot. Protects it "
            "from being overwritten on regeneration. Set False on fresh generation."
        ),
    )

    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["portfolio", "period_type", "period_start"],
                name="unique_leasing_note_per_period",
            ),
        ]
        indexes = [
            models.Index(fields=["period_type", "period_start", "status"]),
        ]

    def __str__(self):
        return f"PortfolioLeasingNote: {self.portfolio} ({self.period_start})"

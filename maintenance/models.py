from django.db import models


class Meld(models.Model):
    """
    Maintenance meld from Property Meld. Tracks work orders from creation
    through vendor assignment, scheduling, and completion.

    Synced from the PM API; unit_fk populated by the unit resolver after
    the PM↔local cross-reference is built.
    """

    property_meld_id = models.CharField(
        max_length=255, unique=True, db_index=True
    )

    brief_description = models.TextField(blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    reference_id = models.CharField(max_length=20, blank=True, db_index=True)
    work_type = models.CharField(max_length=100, blank=True)
    work_location = models.CharField(max_length=255, blank=True)
    origin = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    priority = models.CharField(max_length=20, blank=True, db_index=True)
    status = models.CharField(max_length=100, blank=True, db_index=True)

    # Parties — stored as text since PM vendors != RentVine vendors
    assigned_vendor_name = models.CharField(max_length=255, blank=True)
    coordinator_name = models.CharField(max_length=255, blank=True)

    # Property/unit references from PM (text — no FK to avoid sync dependency)
    property_address = models.CharField(max_length=500, blank=True)
    property_meld_property_id = models.CharField(max_length=255, blank=True)
    unit_ref = models.CharField(max_length=255, blank=True)

    # Resolved FKs to local records (populated by unit resolver)
    property = models.ForeignKey(
        "core.Property",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="melds",
    )
    unit = models.ForeignKey(
        "core.Unit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="melds",
    )

    resident_presence_required = models.BooleanField(default=False)

    # Dates
    scheduled_date = models.DateField(null=True, blank=True, db_index=True)
    started = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    marked_complete = models.DateTimeField(
        null=True, blank=True, db_index=True
    )
    completion_date = models.DateTimeField(
        null=True, blank=True, db_index=True
    )

    OWNER_APPROVAL_CHOICES = [
        ("Not Requested", "Not Requested"),
        ("Requested", "Requested"),
        ("Approved", "Approved"),
        ("Not Approved", "Not Approved"),
    ]
    owner_approval_status = models.CharField(
        max_length=30,
        choices=OWNER_APPROVAL_CHOICES,
        default="Not Requested",
        db_index=True,
    )

    has_invoice = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)

    # Notes
    maintenance_notes = models.TextField(blank=True)
    completion_notes = models.TextField(blank=True)
    reason_cannot_complete = models.TextField(blank=True)
    resolution_type = models.CharField(max_length=100, blank=True)

    # Relationships
    parent_meld_id = models.CharField(max_length=50, blank=True)
    recurring_meld_id = models.CharField(max_length=50, blank=True)
    merged_meld_data = models.JSONField(default=dict, blank=True)
    tenant_rating = models.IntegerField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(
        null=True, blank=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(
                fields=["owner_approval_status", "source_modified_at"]
            ),
            models.Index(fields=["scheduled_date", "status"]),
            models.Index(fields=["marked_complete", "has_invoice"]),
        ]

    def __str__(self):
        return f"Meld {self.property_meld_id} — {self.brief_description[:60]}"

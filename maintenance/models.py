from django.db import models

# WorkOrder.property (ForeignKey) shadows the built-in; alias for decorator use.
_property = property


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


class WorkOrder(models.Model):
    """
    Maintenance work order from RentVine. Replaces PropertyMeld as the
    source for weekly maintenance emails (re-point happens in a later step).

    Synced from GET /maintenance/work-orders. FKs resolved by RentVine ID
    at sync time; left null when the referenced object hasn't been synced yet.
    """

    # ── Upsert key ──────────────────────────────────────────────────
    rentvine_id = models.IntegerField(unique=True, db_index=True)

    # ── Core ────────────────────────────────────────────────────────
    work_order_number = models.CharField(
        max_length=20, blank=True, db_index=True
    )
    description = models.TextField(blank=True)  # raw HTML; sanitise at render
    priority_id = models.IntegerField(null=True, blank=True)
    vendor_trade_id = models.IntegerField(null=True, blank=True)
    estimated_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # ── Resolved FKs (nullable for graceful degradation) ────────────
    property = models.ForeignKey(
        "core.Property",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    unit = models.ForeignKey(
        "core.Unit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    portfolio = models.ForeignKey(
        "core.Portfolio",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    lease = models.ForeignKey(
        "core.Lease",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )

    # ── Vendor (denormalised from inline contact block) ─────────────
    vendor_contact_id = models.IntegerField(null=True, blank=True)
    vendor_name = models.CharField(max_length=255, blank=True)

    # ── Flags ───────────────────────────────────────────────────────
    is_internal = models.BooleanField(default=False)
    is_vacant = models.BooleanField(default=False)
    is_owner_approved = models.BooleanField(default=False)
    is_shared_with_owner = models.BooleanField(default=False)
    is_shared_with_tenant = models.BooleanField(default=False)

    # ── Lifecycle (raw status IDs stored for traceability) ──────────
    primary_work_order_status_id = models.IntegerField(
        null=True, blank=True
    )
    work_order_status_id = models.IntegerField(null=True, blank=True)
    vendor_work_order_status_id = models.IntegerField(
        null=True, blank=True
    )
    portal_vendor_work_order_status_id = models.IntegerField(
        null=True, blank=True
    )
    closed_by_user_id = models.IntegerField(null=True, blank=True)
    cancelled_by_user_id = models.IntegerField(null=True, blank=True)

    # ── Dates (UTC datetimes) ───────────────────────────────────────
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)

    # ── Dates (date-only) ──────────────────────────────────────────
    date_closed = models.DateField(null=True, blank=True)
    date_due = models.DateField(null=True, blank=True)
    scheduled_start_date = models.DateField(null=True, blank=True)
    scheduled_end_date = models.DateField(null=True, blank=True)
    actual_start_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)

    # ── Audit / raw ────────────────────────────────────────────────
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["primary_work_order_status_id"]),
            models.Index(fields=["date_closed", "cancelled_by_user_id"]),
        ]

    def __str__(self):
        return f"WO {self.work_order_number} — {self.description[:60]}"

    @_property
    def derived_status(self):
        """
        Owner-facing status derived from lifecycle fields at read time.
        No RentVine status-ID decode (no API for labels, confirmed).
        """
        if self.cancelled_by_user_id:
            return "Cancelled"
        if self.closed_by_user_id or self.date_closed:
            return "Completed"
        return "Open"

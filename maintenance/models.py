from django.db import models


class Vendor(models.Model):
    """Vendor contact from RentVine (contactTypeID=3)."""

    rentvine_contact_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    # Insurance tracking
    liability_insurance_company = models.CharField(max_length=255, blank=True)
    liability_insurance_policy_number = models.CharField(
        max_length=100, blank=True
    )
    liability_insurance_expires = models.DateField(null=True, blank=True)
    workers_comp_insurance_company = models.CharField(
        max_length=255, blank=True
    )
    workers_comp_insurance_policy_number = models.CharField(
        max_length=100, blank=True
    )
    workers_comp_insurance_expires = models.DateField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class VendorTrade(models.Model):
    """Vendor trade/specialty categories from RentVine (e.g., Plumbing, HVAC)."""

    rentvine_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    is_visible_tenant_portal = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Inspection(models.Model):
    """Inspection from RentVine. Tied to units with lease context."""

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="inspections",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="inspections",
    )
    lease = models.ForeignKey(
        "leasing.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inspections",
    )

    INSPECTION_TYPE_CHOICES = [
        (1, "Pre-Inspection"),
        (2, "Move In"),
        (3, "Move Out"),
        (4, "Inspection"),
    ]
    inspection_type = models.IntegerField(choices=INSPECTION_TYPE_CHOICES)

    INSPECTION_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "In Progress"),
        (3, "Pending Maintenance"),
        (4, "Completed"),
    ]
    inspection_status = models.IntegerField(choices=INSPECTION_STATUS_CHOICES)

    description = models.TextField(blank=True)
    scheduled_date = models.DateField(null=True, blank=True)
    inspection_date = models.DateField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_inspection_type_display()} - {self.unit}"


class Meld(models.Model):
    """
    Maintenance meld from Property Meld. Tracks work orders from creation
    through vendor assignment, scheduling, and completion.
    """

    property_meld_id = models.CharField(max_length=255, unique=True, db_index=True)

    brief_description = models.TextField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    reference_id = models.CharField(max_length=20, blank=True, db_index=True)
    description = models.TextField(blank=True)
    work_type = models.CharField(max_length=100, blank=True)
    work_location = models.CharField(max_length=255, blank=True)
    origin = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    PRIORITY_CHOICES = [
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("EMERGENCY", "Emergency"),
    ]
    priority = models.CharField(max_length=20, blank=True, db_index=True)
    status = models.CharField(max_length=100, blank=True, db_index=True)

    # Parties — stored as text since PM vendors != RentVine vendors
    assigned_vendor_name = models.CharField(max_length=255, blank=True)
    coordinator_name = models.CharField(max_length=255, blank=True)

    # Property/unit references (text — no FK to avoid sync dependency)
    property_address = models.CharField(max_length=500, blank=True)
    property_meld_property_id = models.CharField(max_length=255, blank=True)
    unit_ref = models.CharField(max_length=255, blank=True)

    # Resolved FK to local Unit (populated by unit resolver)
    unit_fk = models.ForeignKey(
        "properties.Unit",
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
    marked_complete = models.DateTimeField(null=True, blank=True, db_index=True)
    completion_date = models.DateTimeField(null=True, blank=True, db_index=True)

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

    # Email summary fields
    ai_summary = models.TextField(blank=True)
    staff_summary = models.TextField(blank=True)
    SUMMARY_STATUS_CHOICES = [
        ("auto", "Auto"),
        ("edited", "Edited"),
        ("needs_manual", "Needs Manual"),
    ]
    summary_status = models.CharField(
        max_length=20, default="auto", choices=SUMMARY_STATUS_CHOICES
    )

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["owner_approval_status", "source_modified_at"]),
            models.Index(fields=["scheduled_date", "status"]),
            models.Index(fields=["marked_complete", "has_invoice"]),
        ]

    def __str__(self):
        return f"Meld {self.property_meld_id} — {self.brief_description[:60]}"


class Expenditure(models.Model):
    """Cost/expenditure record from Property Meld, linked to a Meld."""

    property_meld_id = models.IntegerField(unique=True, db_index=True)
    meld = models.ForeignKey(
        Meld, on_delete=models.CASCADE, related_name="expenditures"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    line_items = models.JSONField(default=list, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Expenditure #{self.property_meld_id} — ${self.amount}"


class MaintenanceEmailSend(models.Model):
    """Tracks sent maintenance summary emails per owner per week."""

    owner = models.ForeignKey(
        "properties.Owner",
        on_delete=models.CASCADE,
        related_name="maintenance_email_sends",
    )
    week_date = models.DateField()  # Monday anchor
    status = models.CharField(max_length=20)  # pending, sent, failed
    sendgrid_message_id = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    error_detail = models.TextField(blank=True)
    melds_included = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("owner", "week_date")]

    def __str__(self):
        return f"MaintenanceEmail {self.owner} — {self.week_date} ({self.status})"


class MaintenanceEmailMeld(models.Model):
    """
    Write-once snapshot of a meld as it appeared in a sent email.

    Created at send time, never updated. Survives meld deletion, portfolio
    renames, and summary re-edits. Enables the query: "every maintenance
    summary ever sent for this portfolio."
    """

    email_send = models.ForeignKey(
        MaintenanceEmailSend,
        on_delete=models.CASCADE,
        related_name="meld_snapshots",
    )
    meld = models.ForeignKey(
        Meld, null=True, blank=True, on_delete=models.SET_NULL, related_name="email_snapshots"
    )

    # Frozen copies — independent of later changes
    meld_reference_id = models.CharField(max_length=20, blank=True)
    summary_text = models.TextField(blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status_at_send = models.CharField(max_length=100, blank=True)
    section = models.CharField(max_length=10)  # open, closed, canceled

    # Portfolio linkage — frozen text survives renames/restructuring
    portfolio = models.ForeignKey(
        "properties.Portfolio",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_email_melds",
    )
    portfolio_name = models.CharField(max_length=255, blank=True)

    # Address context — self-contained record
    unit_label = models.CharField(max_length=500, blank=True)
    property_address = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["portfolio", "created_at"]),
            models.Index(fields=["email_send"]),
        ]

    def __str__(self):
        return f"Snapshot #{self.meld_reference_id} — {self.section} ({self.email_send_id})"

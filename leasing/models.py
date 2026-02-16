from django.db import models


class Tenant(models.Model):
    """Tenant contact from RentVine (contactTypeID=2)."""

    rentvine_contact_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Lease(models.Model):
    """
    Lease record from RentVine. Represents a signed lease agreement
    between Vesta and one or more tenants for a specific unit.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="leases",
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="leases",
    )
    tenants = models.ManyToManyField(Tenant, related_name="leases", blank=True)

    # Status
    PRIMARY_LEASE_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "Active"),
        (3, "Closed"),
    ]
    primary_lease_status = models.IntegerField(
        choices=PRIMARY_LEASE_STATUS_CHOICES, null=True, blank=True
    )
    lease_status_id = models.IntegerField(null=True, blank=True)

    MOVE_OUT_STATUS_CHOICES = [
        (1, "None"),
        (2, "Active"),
        (3, "Completed"),
    ]
    move_out_status = models.IntegerField(
        choices=MOVE_OUT_STATUS_CHOICES, null=True, blank=True
    )

    # Key dates
    move_in_date = models.DateField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    closed_date = models.DateField(null=True, blank=True)
    notice_date = models.DateField(null=True, blank=True)
    expected_move_out_date = models.DateField(null=True, blank=True)
    move_out_date = models.DateField(null=True, blank=True)
    deposit_refund_due_date = models.DateField(null=True, blank=True)

    # Financial
    lease_return_charge_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    # Insurance
    renters_insurance_company = models.CharField(max_length=255, blank=True)
    renters_insurance_policy_number = models.CharField(
        max_length=100, blank=True
    )
    renters_insurance_expiration_date = models.DateField(null=True, blank=True)

    # Move-out details
    move_out_reason_id = models.IntegerField(null=True, blank=True)
    move_out_tenant_remarks = models.TextField(blank=True)

    # Forwarding
    forwarding_name = models.CharField(max_length=255, blank=True)
    forwarding_address = models.CharField(max_length=500, blank=True)
    forwarding_city = models.CharField(max_length=100, blank=True)
    forwarding_state = models.CharField(max_length=2, blank=True)
    forwarding_postal_code = models.CharField(max_length=20, blank=True)
    forwarding_email = models.EmailField(blank=True)
    forwarding_phone = models.CharField(max_length=50, blank=True)

    # Renewal tracking
    is_renewal = models.BooleanField(default=False, db_index=True)
    previous_lease = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renewals",
    )

    # Application link
    rentvine_application_id = models.IntegerField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Lease #{self.rentvine_id} - {self.unit}"


class Prospect(models.Model):
    """
    Prospect from RentEngine. A lead interested in renting a unit.
    Enters the system via webhooks (INSERT on prospects table).
    """

    rentengine_id = models.IntegerField(unique=True, db_index=True)

    unit_of_interest = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prospects",
    )

    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    source = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=100, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Prospect #{self.rentengine_id}"


class LeasingEvent(models.Model):
    """
    Leasing pipeline event from RentEngine. Received via webhooks on the
    leasing_events table. Covers 40+ event types from "New" through
    "Moved In" and everything in between.
    """

    rentengine_id = models.IntegerField(null=True, blank=True, db_index=True)

    prospect = models.ForeignKey(
        Prospect,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leasing_events",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leasing_events",
    )

    EVENT_TYPE_CHOICES = [
        ("new", "New"),
        ("contacted_awaiting_information", "Contacted, Awaiting Information"),
        ("showing_desired", "Showing Desired"),
        ("showing_scheduled", "Showing Scheduled"),
        ("showing_confirmed", "Showing Confirmed"),
        ("arrived_for_showing", "Arrived for Showing"),
        ("showing_started", "Showing Started"),
        ("showing_complete", "Showing Complete"),
        ("missed_showing", "Missed Showing"),
        ("showing_failed", "Showing Failed"),
        ("showing_canceled", "Showing Canceled"),
        ("reassign_showing", "Reassign Showing"),
        ("ghosting", "Ghosting"),
        ("application_sent_to_prospect", "Application Sent to Prospect"),
        ("application_received", "Application Received"),
        ("application_pending", "Application Pending"),
        ("application_in_owner_review", "Application in Owner Review"),
        ("application_approved", "Application Approved"),
        ("application_rejected", "Application Rejected"),
        ("withdrawn", "Withdrawn"),
        ("prescreen_submitted", "Prescreen Submitted"),
        ("prescreen_rejected_credit", "Prescreen Rejected - Credit"),
        ("prescreen_rejected_income", "Prescreen Rejected - Income"),
        ("prescreen_rejected_id", "Prescreen Rejected - ID Verification"),
        ("prescreen_approved", "Prescreen Approved"),
        ("looking_too_early", "Looking too early"),
        ("lease_out_for_signing", "Lease out for signing"),
        ("lease_signed", "Lease Signed"),
        ("deposit_received", "Deposit Received"),
        ("move_in_scheduled", "Move-in Scheduled"),
        ("moved_in", "Moved In"),
        ("unit_of_interest_unavailable", "Unit of Interest Unavailable"),
        ("not_interested", "Not Interested"),
        ("duplicate_lead", "Duplicate Lead"),
        ("still_deciding", "Still Deciding"),
        ("hoa_application_sent", "HOA Application Sent To Prospect"),
        ("hoa_application_submitted", "HOA Application Submitted"),
        ("hoa_application_approved", "HOA Application Approved"),
        ("hoa_application_rejected", "HOA Application Rejected"),
        ("log_note", "Log Note"),
        ("assign_to_user", "Assign to User"),
        ("blocklist_prospect", "Blocklist Prospect"),
        ("unblock_prospect", "Unblock Prospect"),
    ]
    event_type = models.CharField(
        max_length=50, choices=EVENT_TYPE_CHOICES, db_index=True
    )

    event_timestamp = models.DateTimeField(db_index=True)
    event_date = models.DateField(db_index=True)

    context = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_timestamp"]

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.event_date}"


class Showing(models.Model):
    """
    Showing record from RentEngine. Tracks scheduled, completed, missed,
    and canceled showings for units.
    """

    rentengine_id = models.IntegerField(null=True, blank=True, db_index=True)

    prospect = models.ForeignKey(
        Prospect,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="showings",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="showings",
    )

    SHOWING_METHOD_CHOICES = [
        ("accompanied", "Accompanied"),
        ("self_guided", "Self Guided"),
        ("remote_guided", "Remote Guided"),
        ("remote_guided_gated", "Remote Guided with Gated Access"),
    ]
    showing_method = models.CharField(
        max_length=30, choices=SHOWING_METHOD_CHOICES, blank=True
    )

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("confirmed", "Confirmed"),
        ("arrived", "Arrived"),
        ("started", "Started"),
        ("completed", "Completed"),
        ("missed", "Missed"),
        ("failed", "Failed"),
        ("canceled", "Canceled"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, blank=True)

    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    feedback = models.JSONField(default=dict, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Showing ({self.status}) - {self.unit}"


class Application(models.Model):
    """
    Rental application from RentVine. Tracks applicant through the
    screening and approval process.
    """

    rentvine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
    )

    PRIMARY_APPLICATION_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "Submitted"),
        (3, "Screening"),
        (4, "Processing"),
        (5, "On Hold"),
        (6, "Approved"),
        (7, "Declined"),
        (8, "Withdrawn"),
    ]
    primary_status = models.IntegerField(
        choices=PRIMARY_APPLICATION_STATUS_CHOICES, null=True, blank=True
    )
    application_status_id = models.IntegerField(null=True, blank=True)

    number = models.CharField(max_length=50, blank=True)

    # Address at time of application
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Application #{self.number or self.pk}"


class Applicant(models.Model):
    """Individual person on a RentVine application."""

    rentvine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="applicants"
    )

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

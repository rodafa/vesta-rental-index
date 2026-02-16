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


class WorkOrderStatus(models.Model):
    """Work order status definitions from RentVine."""

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    PRIMARY_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "Open"),
        (3, "Closed"),
        (4, "On Hold"),
    ]
    primary_status = models.IntegerField(choices=PRIMARY_STATUS_CHOICES)
    name = models.CharField(max_length=255)
    is_system_status = models.BooleanField(default=False)
    order_index = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "work order statuses"

    def __str__(self):
        return self.name


class WorkOrder(models.Model):
    """
    Work order from RentVine. Tracks maintenance requests from creation
    through vendor assignment and completion.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)
    work_order_number = models.IntegerField(null=True, blank=True)

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="work_orders",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    lease = models.ForeignKey(
        "leasing.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    vendor_trade = models.ForeignKey(
        VendorTrade,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    status = models.ForeignKey(
        WorkOrderStatus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )

    PRIMARY_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "Open"),
        (3, "Closed"),
        (4, "On Hold"),
    ]
    primary_status = models.IntegerField(
        choices=PRIMARY_STATUS_CHOICES, null=True, blank=True
    )

    PRIORITY_CHOICES = [
        (1, "Low"),
        (2, "Medium"),
        (3, "High"),
    ]
    priority = models.IntegerField(
        choices=PRIORITY_CHOICES, null=True, blank=True
    )

    SOURCE_TYPE_CHOICES = [
        (1, "Portal"),
        (2, "In Person"),
        (3, "Email"),
        (4, "Text Message"),
        (5, "Phone"),
        (6, "Recurring"),
    ]
    source_type = models.IntegerField(
        choices=SOURCE_TYPE_CHOICES, null=True, blank=True
    )

    description = models.TextField(blank=True)
    vendor_instructions = models.TextField(blank=True)
    closing_description = models.TextField(blank=True)

    is_owner_approved = models.BooleanField(default=False)
    is_vacant = models.BooleanField(default=False)

    estimated_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # Dates
    scheduled_start_date = models.DateField(null=True, blank=True)
    scheduled_end_date = models.DateField(null=True, blank=True)
    actual_start_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)
    date_closed = models.DateField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"WO #{self.work_order_number or self.pk} - {self.property}"


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

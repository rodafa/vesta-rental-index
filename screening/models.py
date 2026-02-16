from django.db import models


class ScreeningApplication(models.Model):
    """
    Tenant screening application from BoomScreen. Minimal model with
    JSONField for raw data since full BoomScreen API schema is not yet
    available. Will be refined once detailed docs are provided.

    Note: The FK to leasing.Application is intentionally nullable because
    BoomScreen screenings don't always map 1:1 to RentVine applications.
    A prospect may be screened before a formal application exists, or a
    group application may generate multiple BoomScreen screenings. When
    no direct FK link exists, the linkage should be resolved through
    unit + applicant_email matching.
    """

    boompay_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )

    # Cross-references to our domain models
    application = models.ForeignKey(
        "leasing.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_applications",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_applications",
    )

    applicant_name = models.CharField(max_length=255, blank=True)
    applicant_email = models.EmailField(blank=True)

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("expired", "Expired"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Full API response stored as JSON until schema is finalized
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Screening - {self.applicant_name or self.boompay_id}"


class ScreeningReport(models.Model):
    """
    Individual screening report/check from BoomScreen. Each screening
    application can produce multiple reports (credit, criminal, etc.).
    Uses JSONField for report_data since BoomScreen schema details
    are pending.
    """

    screening_application = models.ForeignKey(
        ScreeningApplication,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    boompay_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )

    REPORT_TYPE_CHOICES = [
        ("credit", "Credit Report"),
        ("criminal", "Criminal Background"),
        ("eviction", "Eviction History"),
        ("income", "Income Verification"),
        ("landlord_ref", "Landlord Reference"),
        ("identity", "Identity Verification"),
    ]
    report_type = models.CharField(
        max_length=20, choices=REPORT_TYPE_CHOICES
    )

    DECISION_CHOICES = [
        ("pass", "Pass"),
        ("fail", "Fail"),
        ("review", "Needs Review"),
        ("pending", "Pending"),
    ]
    decision = models.CharField(
        max_length=10, choices=DECISION_CHOICES, default="pending"
    )

    completed_at = models.DateTimeField(null=True, blank=True)

    # Full report payload stored as JSON
    report_data = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_report_type_display()} - {self.screening_application}"

from django.db import models


class OwnerReportLog(models.Model):
    WORKFLOW_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    owner_id = models.CharField(max_length=100)
    owner_name = models.CharField(max_length=255)
    report_month = models.DateField()
    portfolio_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=WORKFLOW_STATUS_CHOICES, default="pending"
    )
    error_message = models.TextField(blank=True)
    report_data = models.JSONField(null=True, blank=True)
    generated_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Financial statement fields from RentVine
    statement_period = models.CharField(max_length=100, blank=True)
    beginning_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_income = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_adjustments = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ending_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_distribution = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Workflow
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sendgrid_message_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_id", "report_month"]),
        ]

    def __str__(self):
        return f"{self.owner_name} — {self.portfolio_name} ({self.report_month:%Y-%m})"

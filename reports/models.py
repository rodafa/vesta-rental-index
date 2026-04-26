from django.db import models


class OwnerReportLog(models.Model):
    owner_id = models.CharField(max_length=100)
    owner_name = models.CharField(max_length=255)
    report_month = models.DateField()
    property_address = models.CharField(max_length=255)
    status = models.CharField(max_length=20)  # 'success' | 'failed' | 'skipped'
    error_message = models.TextField(blank=True)
    report_data = models.JSONField(null=True, blank=True)
    generated_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_id", "report_month"]),
        ]

    def __str__(self):
        return f"{self.owner_name} — {self.property_address} ({self.report_month:%Y-%m})"

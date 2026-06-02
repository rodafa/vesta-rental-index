from django.db import models
from django.utils import timezone


class APISyncLog(models.Model):
    """
    Log of API sync operations. Tracks each pull from external
    APIs for auditing, debugging, and monitoring sync health.
    """

    SOURCE_CHOICES = [
        ("rentvine", "RentVine"),
        ("rentengine", "RentEngine"),
        ("boompay", "BoomPay/BoomScreen"),
        ("property_meld", "Property Meld"),
    ]
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, db_index=True
    )

    endpoint = models.CharField(max_length=255)
    sync_type = models.CharField(max_length=50)  # full, incremental, delta

    STATUS_CHOICES = [
        ("started", "Started"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("partial", "Partial"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="started"
    )

    records_fetched = models.IntegerField(default=0)
    records_created = models.IntegerField(default=0)
    records_updated = models.IntegerField(default=0)

    error_message = models.TextField(blank=True)

    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "API sync log"
        verbose_name_plural = "API sync logs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.source} {self.endpoint} ({self.status}) @ {self.started_at}"

from django.db import models


class WebhookEvent(models.Model):
    """
    Raw webhook event from any external source. Every incoming webhook
    is persisted here before processing. Provides an audit trail and
    allows replay of missed events.
    """

    SOURCE_CHOICES = [
        ("rentengine", "RentEngine"),
        ("rentvine", "RentVine"),
        ("boompay", "BoomPay/BoomScreen"),
    ]
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, db_index=True
    )

    # INSERT, UPDATE, DELETE
    event_type = models.CharField(max_length=50, db_index=True)
    # Table/resource name: prospects, leasing_events, units, etc.
    table_name = models.CharField(max_length=100, db_index=True)

    record = models.JSONField(default=dict, blank=True)
    old_record = models.JSONField(default=dict, blank=True)

    # Processing state
    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["source", "table_name", "event_type"]),
            models.Index(fields=["processed", "received_at"]),
        ]

    def __str__(self):
        return f"{self.source}:{self.table_name} {self.event_type} @ {self.received_at}"


class APISyncLog(models.Model):
    """
    Log of daily API sync operations. Tracks each pull from external
    APIs for auditing, debugging, and monitoring sync health.
    """

    SOURCE_CHOICES = [
        ("rentengine", "RentEngine"),
        ("rentvine", "RentVine"),
        ("boompay", "BoomPay/BoomScreen"),
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

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "API sync log"
        verbose_name_plural = "API sync logs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.source} {self.endpoint} ({self.status}) @ {self.started_at}"

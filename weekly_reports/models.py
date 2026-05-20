from django.db import models


class WeeklyReportRun(models.Model):
    """Tracks each weekly-update execution for auditing and idempotency."""

    STATUS_CHOICES = [
        ("running", "Running"),
        ("success", "Success"),
        ("partial", "Partial"),
        ("failed", "Failed"),
    ]

    start_date = models.DateField(help_text="Inclusive start of the reporting period")
    end_date = models.DateField(help_text="Inclusive end of the reporting period")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="running", db_index=True
    )

    units_processed = models.IntegerField(default=0)
    units_skipped = models.IntegerField(default=0)
    notes_drafted = models.IntegerField(default=0)
    units_errored = models.IntegerField(default=0)
    error_log = models.JSONField(default=list, blank=True)

    dry_run = models.BooleanField(default=False)
    triggered_by = models.CharField(max_length=100, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return (
            f"WeeklyReportRun {self.start_date}..{self.end_date} "
            f"({self.status})"
        )

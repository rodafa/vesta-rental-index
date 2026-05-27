from django.conf import settings
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


class OwnerEmailSend(models.Model):
    """Tracks owner email sends for weekly leasing reports.

    week_date = Monday of the reporting week — must match
    PropertyWeeklyNote.week_date (same Monday anchor).
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    owner = models.ForeignKey(
        "properties.Owner", on_delete=models.CASCADE, related_name="weekly_email_sends"
    )
    # Same anchor as PropertyWeeklyNote.week_date — see help_text there.
    week_date = models.DateField(
        help_text=(
            "Monday of the ISO calendar week containing the period's Tuesday start "
            "(i.e. the day before a Tue–Mon reporting window opens). "
            "Must match PropertyWeeklyNote.week_date exactly."
        ),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    sendgrid_message_id = models.CharField(max_length=200, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    error_detail = models.TextField(blank=True, default="")
    units_included = models.JSONField(default=list)

    # Denormalised snapshot of what was actually sent — survives owner edits.
    recipient_name = models.CharField(max_length=255, blank=True, default="")
    recipient_email = models.EmailField(blank=True, default="")
    email_type = models.CharField(max_length=50, blank=True, default="weekly_leasing")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("owner", "week_date")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Email to {self.owner} for week {self.week_date} ({self.status})"


class OwnerEmailBlurb(models.Model):
    """Shared team blurb for the top of weekly owner leasing emails.

    One per week. Resets each week — if no blurb is written, none appears.
    week_date = Monday anchor, matching PropertyWeeklyNote.week_date
    and OwnerEmailSend.week_date conventions.
    """

    week_date = models.DateField(
        unique=True, help_text="Monday of the week this blurb applies to"
    )
    body = models.TextField(blank=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_date"]

    def __str__(self):
        return f"Blurb for week of {self.week_date}"

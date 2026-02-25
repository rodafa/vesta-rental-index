from django.db import models


class OwnerReportNote(models.Model):
    """Draft notes for owner vacancy reports. Supports queue workflow."""

    owner = models.ForeignKey(
        "properties.Owner",
        on_delete=models.CASCADE,
        related_name="report_notes",
    )

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("reviewed", "Reviewed"),
        ("sent", "Sent"),
    ]
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft", db_index=True
    )

    notes_text = models.TextField(blank=True)
    email_body = models.TextField(blank=True)
    email_subject = models.CharField(max_length=255, blank=True)

    report_date = models.DateField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["owner", "report_date"]
        ordering = ["-report_date", "owner__name"]

    def __str__(self):
        return f"Note for {self.owner.name} - {self.report_date}"


class UnitNote(models.Model):
    """Timestamped internal staff notes on a unit."""

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="staff_notes",
    )
    author = models.CharField(max_length=100)
    note_text = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note by {self.author} on Unit #{self.unit_id}"

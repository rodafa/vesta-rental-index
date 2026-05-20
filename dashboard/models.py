from django.contrib.auth.models import User
from django.db import models


class StaffProfile(models.Model):
    """Tracks staff-specific flags like forced password change."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="staff_profile"
    )
    must_change_password = models.BooleanField(default=True)

    def __str__(self):
        return f"Profile for {self.user.username}"


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
        ("delivered", "Delivered"),
        ("bounced", "Bounced"),
    ]
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft", db_index=True
    )

    notes_text = models.TextField(blank=True)
    email_body = models.TextField(blank=True)
    email_subject = models.CharField(max_length=255, blank=True)

    report_date = models.DateField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Email tracking
    opened_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    bounce_reason = models.TextField(blank=True)
    sendgrid_message_id = models.CharField(max_length=255, blank=True)
    properties_included = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["owner", "report_date"]
        ordering = ["-report_date", "owner__name"]

    def __str__(self):
        return f"Note for {self.owner.name} - {self.report_date}"


class PropertyWeeklyNote(models.Model):
    """Per-unit weekly notes for owner reports. One note per unit per week."""

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="weekly_notes",
    )
    week_date = models.DateField(
        db_index=True,
        help_text=(
            "Monday of the ISO calendar week containing the period's Tuesday start "
            "(i.e. the day before a Tue–Mon reporting window opens). "
            "Join key only; never shown to owners."
        ),
    )
    author = models.CharField(max_length=100)
    note_text = models.TextField()

    approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["unit", "week_date"]
        ordering = ["-week_date"]

    def __str__(self):
        return f"Note for Unit #{self.unit_id} - {self.week_date}"


class MeldWeeklyDraft(models.Model):
    """One AI-drafted maintenance update email per owner per week."""

    owner = models.ForeignKey(
        "properties.Owner",
        on_delete=models.CASCADE,
        related_name="meld_drafts",
    )
    week_start = models.DateField(db_index=True)  # Most recent Tuesday

    email_subject = models.CharField(max_length=255, blank=True)
    email_body = models.TextField(blank=True)  # AI-generated, user-editable

    STATUS_CHOICES = [("draft", "Draft"), ("sent", "Sent")]
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft", db_index=True
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    sendgrid_message_id = models.CharField(max_length=255, blank=True)

    meld_count = models.IntegerField(default=0)
    properties_included = models.JSONField(default=list)  # list of address strings

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["owner", "week_start"]
        ordering = ["owner__name"]

    def __str__(self):
        return f"Meld draft for {self.owner.name} - {self.week_start}"


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

from django.db import models


class TenantNotice(models.Model):
    """
    Immutable evidence record: one row per notice sent (or attempted).

    Created BEFORE the SendGrid call with delivery_status='pending'.
    Only delivery_status, error_message, and sendgrid_message_id may be
    updated after creation - all other fields are write-once.
    """

    KIND_CHOICES = [
        ("reminder", "Friendly Reminder (Day 5)"),
        ("pay_or_quit", "Pay or Quit (Day 6)"),
        ("final", "Final Notice (Day 11)"),
    ]
    DELIVERY_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    # What was sent
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)
    period_key = models.CharField(
        max_length=7,
        db_index=True,
        help_text="'YYYY-MM' of the rent period this notice concerns.",
    )

    # Who it was sent to - FK is nullable so a record can always be written
    # even when the lease is missing from the local DB.  rentvine_lease_id
    # is always populated and carries the dedupe constraint.
    lease = models.ForeignKey(
        "core.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_notices",
    )
    rentvine_lease_id = models.IntegerField(
        db_index=True,
        help_text="RentVine lease ID - always populated, even when the FK is NULL.",
    )
    recipient_emails = models.JSONField(
        default=list,
        help_text="List of email addresses the notice was sent to.",
    )
    missing_recipients = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Tenant names on the lease that had no email address at send time. "
            "Falls back to 'Contact #<id>' when no name is available."
        ),
    )

    # Property context
    property_address = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Display address frozen at send time (from Unit.display_address).",
    )

    # Financial snapshot at send time
    balance_owed = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total outstanding balance at the time the notice was generated.",
    )

    # Delivery
    delivery_status = models.CharField(
        max_length=10,
        choices=DELIVERY_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    sendgrid_message_id = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rentvine_lease_id", "kind", "period_key"],
                name="unique_notice_per_lease_per_kind_per_period",
            ),
        ]
        indexes = [
            models.Index(fields=["period_key", "kind", "delivery_status"]),
        ]

    def save(self, *args, **kwargs):
        """
        Enforce immutability: after initial creation, only delivery_status,
        error_message, and sendgrid_message_id may be updated, and the
        caller MUST pass update_fields explicitly.
        """
        if self.pk is not None:
            allowed = {"delivery_status", "error_message", "sendgrid_message_id"}
            update_fields = kwargs.get("update_fields")
            if update_fields is None:
                raise ValueError(
                    "TenantNotice is immutable after creation. You must pass "
                    "update_fields with a subset of "
                    "{delivery_status, error_message, sendgrid_message_id}."
                )
            if not set(update_fields) <= allowed:
                raise ValueError(
                    f"TenantNotice is immutable except for {allowed}. "
                    f"Attempted: {set(update_fields) - allowed}"
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.get_kind_display()} - Lease {self.rentvine_lease_id} "
            f"({self.period_key}) [{self.delivery_status}]"
        )


class TenantNoticeHold(models.Model):
    """
    Opt-out: suppress notices for a specific lease in a specific period.

    Created manually (admin or future UI) to prevent sending when a tenant
    has a payment plan, dispute, or other reason to skip.

    Mirrors TenantNotice: rentvine_lease_id is always populated, lease FK
    is nullable so a hold can be created for a lease not yet in the local DB.
    """

    lease = models.ForeignKey(
        "core.Lease",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notice_holds",
    )
    rentvine_lease_id = models.IntegerField(
        db_index=True,
        help_text="RentVine lease ID - always populated, even when the FK is NULL.",
    )
    period_key = models.CharField(
        max_length=7,
        help_text="'YYYY-MM' period to suppress. Blank = suppress all periods.",
        blank=True,
        default="",
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rentvine_lease_id", "period_key"],
                name="unique_hold_per_lease_per_period",
            ),
        ]

    def __str__(self):
        period = self.period_key or "all periods"
        return f"Hold: Lease rv#{self.rentvine_lease_id} ({period})"

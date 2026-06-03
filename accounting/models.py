from django.db import models


class Bill(models.Model):
    """
    Bill from RentVine. Tracks payables to vendors and other contacts.

    Linked to a Meld via regex on the description field during sync —
    RentVine bill descriptions contain "Meld <ref>" when the bill is
    for a PropertyMeld work order.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    bill_date = models.DateField()
    date_due = models.DateField()
    reference = models.CharField(max_length=255, blank=True)
    payment_memo = models.TextField(blank=True)
    description = models.TextField(blank=True)

    is_voided = models.BooleanField(default=False)

    meld = models.ForeignKey(
        "maintenance.Meld",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bills",
        db_index=True,
    )

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Bill #{self.rentvine_id} - due {self.date_due}"


class BillCharge(models.Model):
    """
    Line-item charge on a RentVine bill.

    In RentVine's transaction system these are transactionTypeID=7 records.
    The sum of non-voided charges on non-voided bills linked to a meld
    gives the total cost of a work order (vendor base + markup).
    """

    rentvine_transaction_id = models.IntegerField(unique=True, db_index=True)

    bill = models.ForeignKey(
        Bill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charges",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    description = models.TextField(blank=True)
    date_posted = models.DateField(null=True, blank=True)
    is_voided = models.BooleanField(default=False)

    raw_data = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["bill"])]

    def __str__(self):
        return f"Charge #{self.rentvine_transaction_id} — ${self.amount}"

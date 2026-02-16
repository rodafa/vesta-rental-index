from django.db import models


class ChartOfAccounts(models.Model):
    """
    Account from RentVine chart of accounts. Supports hierarchical
    parent/child structure via self-referential FK.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)
    parent_account = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sub_accounts",
    )

    account_type_id = models.IntegerField()
    account_category_id = models.IntegerField(null=True, blank=True)
    number = models.CharField(max_length=50)
    name = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)
    is_rent = models.BooleanField(default=False)
    is_deposit = models.BooleanField(default=False)
    is_escrow = models.BooleanField(default=False)
    is_prepayment = models.BooleanField(default=False)
    is_management_fee = models.BooleanField(default=False)
    is_subject_to_management_fees = models.BooleanField(default=False)
    is_subject_to_late_fees = models.BooleanField(default=False)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "chart of accounts"

    def __str__(self):
        return f"{self.number} - {self.name}"


class Ledger(models.Model):
    """
    Ledger from RentVine. Each ledger belongs to a scope (manager,
    portfolio, property, or unit) identified by ledger_type and object_id.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    LEDGER_TYPE_CHOICES = [
        (1, "Manager"),
        (2, "Portfolio"),
        (3, "Property"),
        (4, "Unit"),
    ]
    ledger_type = models.IntegerField(choices=LEDGER_TYPE_CHOICES)

    # Generic FK to the scoped object (portfolioID, propertyID, etc.)
    object_id = models.IntegerField()
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_ledger_type_display()})"


class Transaction(models.Model):
    """
    Financial transaction from RentVine. Covers lease charges, payments,
    bill payouts, management fees, transfers, and more.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    TRANSACTION_TYPE_CHOICES = [
        (1, "Lease Charge"),
        (2, "Lease Payment"),
        (3, "Lease Credit"),
        (4, "Lease Payment Return"),
        (5, "Owner Payment"),
        (6, "Owner Payment Return"),
        (7, "Bill Charge"),
        (8, "Bill Payout Charge"),
        (9, "Bank Fee"),
        (10, "Bank Transfer"),
        (11, "Journal Entry"),
        (12, "Other Payment"),
        (13, "Other Payment Return"),
        (14, "Management Fee"),
        (15, "Ledger Transfer"),
        (16, "Ledger Payout"),
        (17, "Ledger Payout Return"),
        (18, "Bill Payout Charge Return"),
        (19, "Lease Deposit Release Credit"),
        (20, "Lease Payout"),
        (21, "Lease Payout Return"),
        (22, "Bill Credit"),
        (23, "Bill Payout Credit"),
        (24, "Bill Payout Credit Return"),
    ]
    transaction_type = models.IntegerField(choices=TRANSACTION_TYPE_CHOICES)

    primary_ledger = models.ForeignKey(
        Ledger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    bill = models.ForeignKey(
        "Bill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )

    # Cross-references to properties app
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    portfolio = models.ForeignKey(
        "properties.Portfolio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=255, blank=True)

    is_voided = models.BooleanField(default=False)
    date_posted = models.DateField(db_index=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_posted"]

    def __str__(self):
        return (
            f"{self.get_transaction_type_display()} - "
            f"${self.amount} ({self.date_posted})"
        )


class TransactionEntry(models.Model):
    """
    Transaction entry (line item) from RentVine. Each transaction has
    one or more credit/debit entries against specific accounts and ledgers.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    transaction = models.ForeignKey(
        Transaction, on_delete=models.CASCADE, related_name="entries"
    )
    account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entries",
    )
    ledger = models.ForeignKey(
        Ledger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entries",
    )

    ENTRY_TYPE_CHOICES = [
        (1, "Credit"),
        (2, "Debit"),
    ]
    entry_type = models.IntegerField(choices=ENTRY_TYPE_CHOICES)

    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True)

    is_voided = models.BooleanField(default=False)
    is_cash = models.BooleanField(default=False)
    is_accrual = models.BooleanField(default=False)
    date_posted = models.DateField(db_index=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "transaction entries"

    def __str__(self):
        return (
            f"{self.get_entry_type_display()} - "
            f"${self.credit or self.debit}"
        )


class Bill(models.Model):
    """Bill from RentVine. Tracks payables to vendors and other contacts."""

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    bill_type_id = models.IntegerField(null=True, blank=True)
    payee_contact_id = models.IntegerField(null=True, blank=True)

    bill_date = models.DateField()
    date_due = models.DateField()
    reference = models.CharField(max_length=255, blank=True)
    payment_memo = models.TextField(blank=True)

    is_voided = models.BooleanField(default=False)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    markup_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    work_order = models.ForeignKey(
        "maintenance.WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bills",
    )

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Bill #{self.rentvine_id} - due {self.date_due}"

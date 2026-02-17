from django.contrib import admin

from .models import Bill, ChartOfAccounts, Ledger, Transaction, TransactionEntry


@admin.register(ChartOfAccounts)
class ChartOfAccountsAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "is_active", "is_rent", "is_deposit")
    search_fields = ("number", "name")
    list_filter = ("is_active", "is_rent", "is_deposit")


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
    list_display = ("name", "ledger_type", "is_active")
    list_filter = ("ledger_type", "is_active")
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("rentvine_id", "transaction_type", "amount", "date_posted", "property", "is_voided")
    search_fields = ("rentvine_id", "description", "reference")
    list_filter = ("transaction_type", "is_voided")
    date_hierarchy = "date_posted"


@admin.register(TransactionEntry)
class TransactionEntryAdmin(admin.ModelAdmin):
    list_display = ("transaction", "entry_type", "credit", "debit", "account", "date_posted")
    list_filter = ("entry_type", "is_voided")
    date_hierarchy = "date_posted"


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("rentvine_id", "bill_date", "date_due", "reference", "is_voided")
    search_fields = ("rentvine_id", "reference")
    list_filter = ("is_voided",)
    date_hierarchy = "bill_date"

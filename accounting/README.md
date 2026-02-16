# Accounting App

## Domain

The **accounting** app stores financial data from **RentVine** — the chart of accounts, ledgers, transactions, transaction entries, and bills. This provides the financial dimension of the Rental Index, enabling analysis of revenue per unit, expense tracking, and profitability metrics alongside leasing performance.

## Models

### ChartOfAccounts
Account from RentVine's chart of accounts. Supports hierarchical parent/child via self-referential FK.
- `rentvine_id` — RentVine accountID (unique)
- `parent_account` — **FK** to self (self-referential hierarchy)
- `account_type_id`, `account_category_id` — RentVine type system
- `number` — Account number (e.g., "1000")
- `name` — Account name
- `is_active`
- Classification flags: `is_rent`, `is_deposit`, `is_escrow`, `is_prepayment`, `is_management_fee`, `is_subject_to_management_fees`, `is_subject_to_late_fees`
- `raw_data` — Full API response

### Ledger
RentVine ledger scoped to a manager, portfolio, property, or unit.
- `rentvine_id` — RentVine ledgerID (unique)
- `ledger_type` — Manager / Portfolio / Property / Unit
- `object_id` — ID of the scoped entity (portfolioID, propertyID, etc.)
- `name`, `is_active`
- `raw_data` — Full API response

### Transaction
Financial transaction from RentVine. 24 transaction types covering lease charges, payments, credits, returns, bill payouts, management fees, transfers, and more.
- `rentvine_id` — RentVine transactionID (unique)
- `transaction_type` — One of 24 types (Lease Charge, Lease Payment, Bill Charge, Management Fee, etc.)
- `primary_ledger` — **FK** to Ledger
- `bill` — **FK** to Bill (for bill-related transactions)
- `property` — **FK** to properties.Property
- `unit` — **FK** to properties.Unit
- `portfolio` — **FK** to properties.Portfolio
- `amount`, `description`, `reference`
- `is_voided`
- `date_posted` — Posting date (indexed)
- `raw_data` — Full API response

### TransactionEntry
Line item on a transaction. Each transaction has one or more credit/debit entries.
- `rentvine_id` — RentVine transactionEntryID (unique)
- `transaction` — **FK** to Transaction (CASCADE)
- `account` — **FK** to ChartOfAccounts
- `ledger` — **FK** to Ledger
- `entry_type` — Credit / Debit
- `credit`, `debit` — Amounts
- `description`
- `is_voided`, `is_cash`, `is_accrual`
- `date_posted`
- `raw_data` — Full API response

### Bill
Payable bill from RentVine.
- `rentvine_id` — RentVine billID (unique)
- `bill_type_id`, `payee_contact_id`
- `bill_date`, `date_due`
- `reference`, `payment_memo`
- `is_voided`
- `discount_percent`, `markup_percent`
- `work_order` — **FK** to maintenance.WorkOrder (links bills to maintenance work)
- `raw_data` — Full API response

## Key Relationships
- ChartOfAccounts → self (parent/child hierarchy)
- Transaction → Ledger, Bill, Property, Unit, Portfolio (FK)
- TransactionEntry → Transaction (CASCADE), ChartOfAccounts, Ledger (FK)
- Bill → maintenance.WorkOrder (FK)

## Future Services
- **TransactionSyncService** — Daily pull from RentVine `/accounting/transactions/search` with `dateTimeModifiedMin` for incremental sync
- **BillSyncService** — Sync bills from RentVine `/accounting/bills`
- **ChartOfAccountsSyncService** — Sync chart of accounts (infrequent, on-demand)
- **LedgerSyncService** — Sync ledgers from RentVine `/accounting/ledgers/search`
- **RevenueAnalyzer** — Calculate rent revenue per unit/property/portfolio over time
- **ExpenseTracker** — Track maintenance and operating expenses by property
- **DiagnosticsPoller** — Pull RentVine accounting diagnostics (negative bank accounts, escrow mismatches, etc.)

"""
Diagnostic: dump field names from RentVine /accounting/transactions.

Phase 1: Fetch chart of accounts to map internal IDs to account numbers.
Phase 2: Scan transactions for rent-income (4100/4105) and distribution (3250)
          records using the resolved IDs.
Phase 3: Dump one sample of each uncommon transaction type for inspection.

Read-only — writes nothing to the database.

Usage:
    python manage.py dump_rentvine_transactions
    python manage.py dump_rentvine_transactions --max-pages 10
"""

import json

from django.core.management.base import BaseCommand

from integrations.rentvine.client import RentvineClient


# Account numbers we need to find
TARGET_ACCOUNT_NUMBERS = {"4100", "4105", "3250"}


class Command(BaseCommand):
    help = "Dump RentVine chart of accounts and transaction samples for target accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-pages",
            type=int,
            default=30,
            help="Maximum transaction pages to scan (default: 30).",
        )

    def handle(self, *args, **options):
        client = RentvineClient()
        max_pages = options["max_pages"]

        # ── Phase 1: Chart of Accounts ──────────────────────────────
        self.stdout.write("=" * 70)
        self.stdout.write("PHASE 1: CHART OF ACCOUNTS")
        self.stdout.write("=" * 70)

        coa_map = {}  # chargeAccountID -> {number, name, ...}
        target_ids = set()  # chargeAccountIDs matching our target numbers

        for endpoint in [
            "/accounting/chart-of-accounts",
            "/accounting/chartofaccounts",
            "/accounting/accounts",
            "/chartofaccounts",
        ]:
            try:
                data = client.get(endpoint)
                records = client._extract_records(data)
                if records:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Found chart of accounts at {endpoint}: "
                            f"{len(records)} records"
                        )
                    )
                    # Dump first record to see field names
                    if records:
                        first = records[0]
                        acct = (
                            first.get("chartOfAccount", first)
                            if isinstance(first, dict) and "chartOfAccount" in first
                            else first
                        )
                        self.stdout.write("\n  Sample record fields:")
                        if isinstance(acct, dict):
                            for key in sorted(acct.keys()):
                                self.stdout.write(f"    {key}: {_fmt(acct[key])}")

                    # Build the mapping
                    for rec in records:
                        acct = rec
                        for wrapper_key in ("account", "chartOfAccount"):
                            if isinstance(rec, dict) and wrapper_key in rec:
                                acct = rec[wrapper_key]
                                break
                        if not isinstance(acct, dict):
                            continue

                        acct_id = str(
                            acct.get("accountID")
                            or acct.get("chartOfAccountID")
                            or acct.get("id")
                            or ""
                        )
                        acct_number = str(
                            acct.get("number")
                            or acct.get("accountNumber")
                            or acct.get("glNumber")
                            or ""
                        )
                        acct_name = str(
                            acct.get("name")
                            or acct.get("accountName")
                            or ""
                        )

                        if acct_id:
                            coa_map[acct_id] = {
                                "number": acct_number,
                                "name": acct_name,
                            }
                            if acct_number in TARGET_ACCOUNT_NUMBERS:
                                target_ids.add(acct_id)

                    break  # Found the right endpoint
                else:
                    self.stdout.write(f"  {endpoint}: empty response")
            except Exception as exc:
                self.stdout.write(f"  {endpoint}: {exc}")

        if coa_map:
            self.stdout.write(f"\n  Mapped {len(coa_map)} accounts.")
            self.stdout.write("\n  Target accounts found:")
            for acct_id in sorted(target_ids):
                info = coa_map[acct_id]
                self.stdout.write(
                    f"    chargeAccountID={acct_id} -> "
                    f"#{info['number']} {info['name']}"
                )

            # Also print all accounts in the 3000-5000 range for context
            self.stdout.write("\n  All accounts in 3000-5000 range:")
            for acct_id, info in sorted(coa_map.items(), key=lambda x: x[1]["number"]):
                try:
                    num = int(info["number"])
                    if 3000 <= num < 5000:
                        self.stdout.write(
                            f"    ID={acct_id:>4s}  #{info['number']}  {info['name']}"
                        )
                except ValueError:
                    pass
        else:
            self.stdout.write(
                self.style.WARNING(
                    "\n  No chart of accounts found. "
                    "Will scan transactions without account mapping."
                )
            )

        # ── Phase 2: Find target transactions ───────────────────────
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("PHASE 2: SCANNING TRANSACTIONS FOR TARGET ACCOUNTS")
        self.stdout.write("=" * 70)

        if not target_ids:
            self.stdout.write(
                self.style.WARNING(
                    "  No target account IDs resolved — scanning all chargeAccountIDs"
                )
            )

        found = {}  # account_number -> raw record
        seen_types = {}  # transactionTypeID -> count
        type_samples = {}  # transactionTypeID -> first raw record
        total_scanned = 0
        page = 1

        while page <= max_pages:
            data = client.get(
                "/accounting/transactions",
                params={"page": page, "pageSize": 100},
            )

            if not isinstance(data, list) or not data:
                self.stdout.write(f"  Page {page}: empty/non-list response, stopping.")
                break

            for record in data:
                tx = (
                    record.get("transaction", record)
                    if isinstance(record, dict)
                    else record
                )
                total_scanned += 1

                # Track transaction type distribution
                tx_type = str(tx.get("transactionTypeID", "?"))
                seen_types[tx_type] = seen_types.get(tx_type, 0) + 1
                if tx_type not in type_samples:
                    type_samples[tx_type] = record

                # Check chargeAccountID against our resolved targets
                charge_acct_id = str(tx.get("chargeAccountID") or "")
                if charge_acct_id in target_ids:
                    acct_info = coa_map.get(charge_acct_id, {})
                    acct_number = acct_info.get("number", charge_acct_id)
                    if acct_number not in found:
                        found[acct_number] = record
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  Found #{acct_number} ({acct_info.get('name', '?')}) "
                                f"on page {page}, txType={tx_type}"
                            )
                        )

            self.stdout.write(
                f"  Page {page}: {len(data)} records (total: {total_scanned})"
            )

            if len(found) >= len(TARGET_ACCOUNT_NUMBERS):
                self.stdout.write(self.style.SUCCESS("\n  All target accounts found!"))
                break

            if len(data) < 100:
                self.stdout.write("  Last page reached.")
                break

            page += 1

        # ── Phase 3: Report ─────────────────────────────────────────
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("TRANSACTION TYPE DISTRIBUTION")
        self.stdout.write("=" * 70)
        for tx_type, count in sorted(seen_types.items(), key=lambda x: -x[1]):
            # Show what chargeAccountIDs appear for this type
            self.stdout.write(f"  transactionTypeID={tx_type}: {count} records")

        # Dump each found target record
        for acct_number in sorted(found.keys()):
            record = found[acct_number]
            tx = (
                record.get("transaction", record)
                if isinstance(record, dict)
                else record
            )
            acct_name = coa_map.get(
                str(tx.get("chargeAccountID", "")), {}
            ).get("name", "?")

            self.stdout.write("\n" + "=" * 70)
            self.stdout.write(f"ACCOUNT #{acct_number} — {acct_name}")
            self.stdout.write("=" * 70)
            self.stdout.write("\n--- Key fields ---")
            key_fields = [
                "transactionID", "transactionTypeID", "portfolioID",
                "propertyID", "unitID", "leaseID", "chargeAccountID",
                "primaryLedgerID", "amount", "amountPaid", "amountAllocated",
                "description", "datePosted", "transactionDate",
                "dateCreated", "dateTimeCreated", "isVoided",
                "contactID", "payoutID",
            ]
            for key in key_fields:
                if key in tx:
                    self.stdout.write(f"  {key}: {tx[key]}")

            self.stdout.write("\n--- All fields ---")
            for key in sorted(tx.keys()):
                self.stdout.write(f"  {key}: {_fmt(tx[key])}")

        # Report missing
        missing = TARGET_ACCOUNT_NUMBERS - set(found.keys())
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  NOT FOUND after {total_scanned} records: "
                    f"{', '.join(sorted(missing))}"
                )
            )

        # Dump samples for uncommon types
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("SAMPLES OF EACH TRANSACTION TYPE")
        self.stdout.write("=" * 70)
        for tx_type in sorted(type_samples.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            record = type_samples[tx_type]
            tx = (
                record.get("transaction", record)
                if isinstance(record, dict)
                else record
            )
            charge_id = str(tx.get("chargeAccountID") or "")
            acct_info = coa_map.get(charge_id, {})
            acct_label = (
                f"#{acct_info['number']} {acct_info['name']}"
                if acct_info
                else f"chargeAccountID={charge_id}"
            )

            self.stdout.write(
                f"\n  --- Type {tx_type} (n={seen_types.get(tx_type, 0)}) "
                f"| account: {acct_label} ---"
            )
            for key in [
                "transactionID", "portfolioID", "propertyID", "unitID",
                "chargeAccountID", "amount", "description",
                "datePosted", "transactionDate", "dateTimeCreated",
                "isVoided",
            ]:
                if key in tx:
                    self.stdout.write(f"    {key}: {tx[key]}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Scanned {total_scanned} records across {page} pages."
            )
        )


def _fmt(val):
    """Format a value for display, truncating long strings."""
    if isinstance(val, dict):
        return f"{{dict with {len(val)} keys: {list(val.keys())}}}"
    if isinstance(val, list):
        return f"[list with {len(val)} items]"
    s = str(val)
    if len(s) > 120:
        return s[:120] + "..."
    return s

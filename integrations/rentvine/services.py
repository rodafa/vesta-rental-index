"""
Sync services that pull data from Rentvine and upsert into Django models.

Each service:
  - Fetches records via RentvineClient
  - Maps fields via mappers
  - Uses update_or_create for idempotent sync
  - Logs everything to APISyncLog
  - Isolates per-record errors so one bad record doesn't block the rest
"""

import json
import logging
import re
from datetime import date
from decimal import Decimal

from django.utils import timezone

from accounting.models import Bill, BillCharge
from core.models import Lease, Owner, Portfolio, Property, Tenant, Unit
from integrations.models import APISyncLog

from .client import RentvineClient
from .mappers import (
    map_bill,
    map_bill_charge,
    map_lease,
    map_owner,
    map_portfolio,
    map_portfolio_statement,
    map_property,
    map_tenant_from_lease,
    map_unit,
    map_work_order,
)

# RentVine transactionTypeID=7 identifies bill charge transactions
# (vendor payables posted against a bill).
BILL_CHARGE_TRANSACTION_TYPE = 7

# RentVine bill descriptions include "Meld <ref>" when the bill is for
# a PropertyMeld work order.  Used to link Bill → Meld during sync.
_MELD_REF_PATTERN = re.compile(r"\bMeld\s*#?\s*([A-Z0-9]+)", re.IGNORECASE)

logger = logging.getLogger(__name__)


class _BaseSyncService:
    """Shared sync scaffolding."""

    source = "rentvine"
    endpoint = ""
    sync_type = "full"

    def __init__(self, client=None):
        self.client = client or RentvineClient()

    def _create_log(self):
        return APISyncLog.objects.create(
            source=self.source,
            endpoint=self.endpoint,
            sync_type=self.sync_type,
            status="started",
        )

    def _complete_log(self, log, *, created, updated, fetched, errors=None):
        log.status = "completed" if not errors else "partial"
        log.records_fetched = fetched
        log.records_created = created
        log.records_updated = updated
        if errors:
            log.error_message = "\n".join(errors[:50])
        log.completed_at = timezone.now()
        log.save()

    def _fail_log(self, log, error_message):
        log.status = "failed"
        log.error_message = str(error_message)[:2000]
        log.completed_at = timezone.now()
        log.save()


class PortfolioSyncService(_BaseSyncService):
    endpoint = "portfolios/search"

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/portfolios/search")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                rentvine_id, defaults = map_portfolio(record)
                if dry_run:
                    logger.info("DRY RUN portfolio %s: %s", rentvine_id, defaults.get("name"))
                    continue

                _, was_created = Portfolio.objects.update_or_create(
                    rentvine_id=rentvine_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as exc:
                msg = f"Error syncing portfolio record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


class OwnerSyncService(_BaseSyncService):
    endpoint = "owners/search"

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/owners/search")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                rentvine_contact_id, defaults = map_owner(record)
                if dry_run:
                    logger.info("DRY RUN owner %s: %s", rentvine_contact_id, defaults.get("name"))
                    continue

                _, was_created = Owner.objects.update_or_create(
                    rentvine_contact_id=rentvine_contact_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

                # Link to portfolios if portfolio IDs are present
                portfolio_ids = record.get("portfolioIDs") or record.get("portfolio_ids") or []
                if isinstance(portfolio_ids, (list, tuple)) and portfolio_ids:
                    portfolios = Portfolio.objects.filter(rentvine_id__in=portfolio_ids)
                    if portfolios.exists():
                        owner = Owner.objects.get(rentvine_contact_id=rentvine_contact_id)
                        owner.portfolios.set(portfolios)

            except Exception as exc:
                msg = f"Error syncing owner record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


class PropertySyncService(_BaseSyncService):
    endpoint = "properties"

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/properties")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                rentvine_id, portfolio_rentvine_id, defaults = map_property(record)

                if dry_run:
                    logger.info(
                        "DRY RUN property %s: %s (%s)",
                        rentvine_id, defaults.get("name"), defaults.get("address_line_1"),
                    )
                    continue

                # Resolve portfolio FK
                if portfolio_rentvine_id:
                    try:
                        defaults["portfolio"] = Portfolio.objects.get(
                            rentvine_id=portfolio_rentvine_id
                        )
                    except Portfolio.DoesNotExist:
                        logger.warning(
                            "Portfolio %s not found for property %s",
                            portfolio_rentvine_id, rentvine_id,
                        )
                        defaults["portfolio"] = None
                else:
                    defaults["portfolio"] = None

                _, was_created = Property.objects.update_or_create(
                    rentvine_id=rentvine_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as exc:
                msg = f"Error syncing property record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


class UnitSyncService(_BaseSyncService):
    endpoint = "units"

    def sync(self, dry_run=False):
        """
        Sync units by iterating all synced Properties and calling
        /properties/{id}/units for each.

        Address drift fix: after mapping, derives missing unit address
        fields (address_line_1, city, state, postal_code) from the
        parent Property so they are never empty.
        """
        log = self._create_log()
        properties = Property.objects.filter(rentvine_id__isnull=False)

        total_fetched = 0
        created_count = 0
        updated_count = 0
        errors = []

        for prop in properties:
            try:
                records = self.client.get_all(
                    f"/properties/{prop.rentvine_id}/units"
                )
            except Exception as exc:
                msg = f"Error fetching units for property {prop.rentvine_id}: {exc}"
                logger.error(msg)
                errors.append(msg)
                continue

            total_fetched += len(records)

            for record in records:
                try:
                    rentvine_id, _, defaults = map_unit(record)

                    if dry_run:
                        logger.info(
                            "DRY RUN unit %s (property %s): %s",
                            rentvine_id, prop.rentvine_id, defaults.get("name"),
                        )
                        continue

                    defaults["property"] = prop

                    # --- Address drift fix ---
                    # RentVine often only populates full address at the property
                    # level, leaving unit records with empty city/state/postal.
                    # Derive missing fields from the parent property.
                    if not defaults.get("address_line_1"):
                        defaults["address_line_1"] = prop.address_line_1
                    if not defaults.get("city"):
                        defaults["city"] = prop.city
                    if not defaults.get("state"):
                        defaults["state"] = prop.state
                    if not defaults.get("postal_code"):
                        defaults["postal_code"] = prop.postal_code

                    _, was_created = Unit.objects.update_or_create(
                        rentvine_id=rentvine_id,
                        defaults=defaults,
                    )
                    if was_created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as exc:
                    msg = f"Error syncing unit record for property {prop.rentvine_id}: {exc}"
                    logger.error(msg)
                    errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=total_fetched,
            errors=errors,
        )
        return {
            "fetched": total_fetched,
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


class LeaseSyncService(_BaseSyncService):
    """
    Sync leases from Rentvine including inline tenant and recurring charge sync.

    For each lease:
    1. Fetch lease list via /leases/search
    2. Map and upsert Lease record (resolve Unit/Property FKs)
    3. Fetch /leases/{id}/tenants -> upsert Tenant records -> set M2M
    4. Fetch /leases/{id}/recurring-charges -> sum isRent charges -> update rent_amount
    5. Detect renewals: if another lease exists for the same unit, mark as renewal
    """

    endpoint = "leases/search"

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/leases/search")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                rentvine_id, unit_rentvine_id, property_rentvine_id, defaults = map_lease(record)

                if dry_run:
                    logger.info(
                        "DRY RUN lease %s: unit=%s, property=%s, status=%s",
                        rentvine_id, unit_rentvine_id, property_rentvine_id,
                        defaults.get("primary_lease_status"),
                    )
                    continue

                # Resolve Unit FK
                unit = None
                if unit_rentvine_id:
                    try:
                        unit = Unit.objects.get(rentvine_id=unit_rentvine_id)
                    except Unit.DoesNotExist:
                        logger.warning("Unit %s not found for lease %s", unit_rentvine_id, rentvine_id)

                # Resolve Property FK
                prop = None
                if property_rentvine_id:
                    try:
                        prop = Property.objects.get(rentvine_id=property_rentvine_id)
                    except Property.DoesNotExist:
                        logger.warning("Property %s not found for lease %s", property_rentvine_id, rentvine_id)

                # Fall back to unit's property
                if not prop and unit and unit.property:
                    prop = unit.property

                if not unit or not prop:
                    msg = f"Lease {rentvine_id}: missing unit ({unit_rentvine_id}) or property ({property_rentvine_id}), skipping"
                    logger.warning(msg)
                    errors.append(msg)
                    continue

                defaults["unit"] = unit
                defaults["property"] = prop

                # Renewal detection: another lease exists for this unit
                existing_for_unit = Lease.objects.filter(
                    unit=unit
                ).exclude(rentvine_id=rentvine_id)
                if existing_for_unit.exists():
                    defaults["is_renewal"] = True
                    previous = existing_for_unit.order_by("-start_date", "-rentvine_id").first()
                    if previous:
                        defaults["previous_lease"] = previous

                lease_obj, was_created = Lease.objects.update_or_create(
                    rentvine_id=rentvine_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

                # Sync tenants for this lease
                self._sync_lease_tenants(lease_obj, rentvine_id, errors)

                # Sync recurring charges to compute rent_amount
                self._sync_rent_amount(lease_obj, rentvine_id, errors)

            except Exception as exc:
                msg = f"Error syncing lease record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }

    def _sync_lease_tenants(self, lease_obj, rentvine_id, errors):
        """Fetch and upsert tenants for a lease, then set the M2M relationship."""
        try:
            tenant_records = self.client.get(f"/leases/{rentvine_id}/tenants")
            if not isinstance(tenant_records, list):
                tenant_records = [tenant_records]
        except Exception as exc:
            msg = f"Error fetching tenants for lease {rentvine_id}: {exc}"
            logger.warning(msg)
            errors.append(msg)
            return

        tenant_ids = []
        for t_record in tenant_records:
            try:
                contact_id, is_primary, t_defaults = map_tenant_from_lease(t_record)
                tenant_obj, _ = Tenant.objects.update_or_create(
                    rentvine_contact_id=contact_id,
                    defaults=t_defaults,
                )
                tenant_ids.append(tenant_obj.pk)
            except Exception as exc:
                msg = f"Error syncing tenant for lease {rentvine_id}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        if tenant_ids:
            lease_obj.tenants.set(tenant_ids)

    def _sync_rent_amount(self, lease_obj, rentvine_id, errors):
        """Fetch recurring charges and compute rent_amount as sum of active isRent charges."""
        try:
            charge_records = self.client.get(f"/leases/{rentvine_id}/recurring-charges")
            if not isinstance(charge_records, list):
                charge_records = [charge_records]
        except Exception as exc:
            msg = f"Error fetching recurring charges for lease {rentvine_id}: {exc}"
            logger.warning(msg)
            errors.append(msg)
            return

        today = date.today()
        rent_total = Decimal("0")
        pet_rent_total = Decimal("0")
        found_rent = False
        for charge_data in charge_records:
            try:
                account = charge_data.get("account", {}) if isinstance(charge_data, dict) else {}
                charge = charge_data.get("recurringCharge", charge_data) if isinstance(charge_data, dict) else {}

                is_rent = str(account.get("isRent", "0")) == "1"
                if not is_rent:
                    continue

                end_date_str = charge.get("endDate")
                if end_date_str:
                    if date.fromisoformat(end_date_str) < today:
                        continue

                amount = Decimal(str(charge.get("amount") or charge.get("chargeAmount") or 0))
                rent_total += amount
                found_rent = True

                acct_name = account.get("name", "")
                if acct_name == "Pet Rent":
                    pet_rent_total += amount
            except Exception as exc:
                msg = f"Error parsing charge for lease {rentvine_id}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        if found_rent:
            lease_obj.rent_amount = rent_total
            lease_obj.pet_rent_amount = pet_rent_total or None
            lease_obj.save(update_fields=["rent_amount", "pet_rent_amount"])


class BillSyncService(_BaseSyncService):
    """
    Sync bills from RentVine /accounting/bills.

    Links each bill to a Meld by parsing "Meld <ref>" from the bill
    description and matching against Meld.reference_id.
    """

    endpoint = "accounting/bills"

    def sync(self, dry_run=False, from_date=None):
        log = self._create_log()
        try:
            if from_date:
                records = self._fetch_bills_from(from_date)
            else:
                records = self.client.get_all("/accounting/bills", page_size=100)
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        linked_count = 0
        unlinked_count = 0
        no_ref_count = 0
        errors = []

        # Pre-fetch meld reference_id -> pk mapping for fast lookup
        from maintenance.models import Meld

        meld_lookup = {}
        for ref_id, pk in Meld.objects.values_list("reference_id", "pk"):
            if ref_id:
                meld_lookup[ref_id.upper()] = pk

        for record in records:
            try:
                rentvine_id, defaults = map_bill(record)

                # Client-side date guard (belt-and-suspenders for server filter)
                if from_date and defaults.get("bill_date") and defaults["bill_date"] < from_date:
                    continue

                if dry_run:
                    logger.info(
                        "DRY RUN bill %s: %s",
                        rentvine_id,
                        defaults.get("description", "")[:60],
                    )
                    continue

                # Meld linkage via regex on description
                meld_fk = None
                description = defaults.get("description", "")
                match = _MELD_REF_PATTERN.search(description)
                if match:
                    ref = match.group(1).upper()
                    meld_pk = meld_lookup.get(ref)
                    if meld_pk:
                        meld_fk = meld_pk
                        linked_count += 1
                    else:
                        unlinked_count += 1
                        logger.info(
                            "Bill %s references meld '%s' but no matching "
                            "Meld.reference_id found",
                            rentvine_id,
                            ref,
                        )
                else:
                    no_ref_count += 1

                defaults["meld_id"] = meld_fk

                _, was_created = Bill.objects.update_or_create(
                    rentvine_id=rentvine_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as exc:
                msg = f"Error syncing bill record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        result = {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "linked": linked_count,
            "unlinked": unlinked_count,
            "no_ref": no_ref_count,
            "errors": len(errors),
        }
        logger.info("BillSync result: %s", result)
        return result

    def _fetch_bills_from(self, from_date):
        """Paginate /accounting/bills with server-side billDateFrom filter."""
        all_records = []
        page = 1
        page_size = 100
        while True:
            params = {
                "page": page,
                "pageSize": page_size,
                "billDateFrom": str(from_date),
            }
            data = self.client.get("/accounting/bills", params=params)
            records = self.client._extract_records(data)
            if not records:
                break
            all_records.extend(records)
            logger.info(
                "Fetched page %d from /accounting/bills (%d records, %d total)",
                page, len(records), len(all_records),
            )
            if len(records) < page_size:
                break
            page += 1
        logger.info(
            "Fetched %d bills from %s onward", len(all_records), from_date
        )
        return all_records


class BillChargeSyncService(_BaseSyncService):
    """
    Sync bill charges (transactionTypeID=7) from RentVine.

    Fetches all transactions from /accounting/transactions, keeps only
    type-7 records (bill charges), and links each to its parent Bill
    via billID.
    """

    endpoint = "accounting/transactions"

    def sync(self, dry_run=False, from_date=None):
        log = self._create_log()

        try:
            records = self._fetch_type7_transactions(from_date=from_date)
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        orphaned_count = 0
        errors = []

        # Pre-fetch bill rentvine_id -> pk mapping
        bill_lookup = dict(Bill.objects.values_list("rentvine_id", "pk"))

        for record in records:
            try:
                tx_id, rentvine_bill_id, defaults = map_bill_charge(record)

                if dry_run:
                    logger.info(
                        "DRY RUN charge %s: bill=%s amount=%s",
                        tx_id,
                        rentvine_bill_id,
                        defaults.get("amount"),
                    )
                    continue

                # Resolve bill FK
                bill_pk = None
                if rentvine_bill_id:
                    bill_pk = bill_lookup.get(rentvine_bill_id)
                    if not bill_pk:
                        orphaned_count += 1
                        logger.info(
                            "Charge %s references billID %s with no matching Bill",
                            tx_id,
                            rentvine_bill_id,
                        )

                defaults["bill_id"] = bill_pk

                _, was_created = BillCharge.objects.update_or_create(
                    rentvine_transaction_id=tx_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

            except Exception as exc:
                msg = f"Error syncing bill charge record: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )
        result = {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "orphaned": orphaned_count,
            "errors": len(errors),
        }
        logger.info("BillChargeSync result: %s", result)
        return result

    def _fetch_type7_transactions(self, from_date=None):
        """
        Paginate /accounting/transactions, keeping only transactionTypeID=7.

        The transactions endpoint does not support server-side date filtering,
        so from_date is applied client-side on each record's datePosted.
        """
        from integrations.utils import safe_date

        all_records = []
        page = 1
        page_size = 100
        while True:
            data = self.client.get(
                "/accounting/transactions",
                params={"page": page, "pageSize": page_size},
            )
            if not isinstance(data, list) or not data:
                break
            for record in data:
                tx = (
                    record.get("transaction", record)
                    if isinstance(record, dict)
                    else record
                )
                if (
                    str(tx.get("transactionTypeID"))
                    != str(BILL_CHARGE_TRANSACTION_TYPE)
                ):
                    continue
                # Client-side date filter
                if from_date:
                    posted = safe_date(tx.get("datePosted"))
                    if posted and posted < from_date:
                        continue
                all_records.append(record)
            if len(data) < page_size:
                break
            page += 1
        return all_records


class PortfolioStatementSyncService(_BaseSyncService):
    """
    Sync owner statements from RentVine for each portfolio.

    Per-portfolio: GET /portfolios/{id}/statements?type=owner&sort=-endDate
    Filters client-side to statementStatusID == "2" (posted), takes newest.
    Idempotent upsert keyed on rentvine_statement_id.
    """

    endpoint = "portfolios/{id}/statements"

    def sync(self, dry_run=False, portfolio_id=None, limit=None):
        from accounting.models import PortfolioStatement

        log = self._create_log()
        portfolios = Portfolio.objects.filter(rentvine_id__isnull=False, is_active=True)
        if portfolio_id:
            portfolios = portfolios.filter(rentvine_id=portfolio_id)

        total_fetched = 0
        created_count = 0
        updated_count = 0
        errors = []
        portfolios_processed = 0

        for portfolio in portfolios:
            if limit and portfolios_processed >= limit:
                break

            try:
                data = self.client.get(
                    f"/portfolios/{portfolio.rentvine_id}/statements",
                    params={
                        "type": "owner",
                        "sort": "-endDate",
                        "limit": 5,
                    },
                )
                records = self.client._extract_records(data)
            except Exception as exc:
                msg = (
                    f"Error fetching statements for portfolio "
                    f"{portfolio.rentvine_id} ({portfolio.name}): {exc}"
                )
                logger.error(msg)
                errors.append(msg)
                continue

            total_fetched += len(records)
            posted_found = False

            for record in records:
                try:
                    stmt_id, portfolio_rv_id, defaults = map_portfolio_statement(record)
                    status_raw = defaults.get("status", "")

                    if str(status_raw) != "2":
                        logger.info(
                            "rentvine_statement_non_posted_status",
                            extra={
                                "portfolio": portfolio.name,
                                "statement_id": stmt_id,
                                "status": status_raw,
                            },
                        )
                        continue

                    posted_found = True

                    if dry_run:
                        logger.info(
                            "DRY RUN statement %s: portfolio=%s, period=%s to %s, "
                            "income=%s, expenses=%s",
                            stmt_id,
                            portfolio.name,
                            defaults.get("period_start"),
                            defaults.get("period_end"),
                            defaults.get("total_income"),
                            defaults.get("total_expenses"),
                        )
                        continue

                    defaults["portfolio"] = portfolio

                    _, was_created = PortfolioStatement.objects.update_or_create(
                        rentvine_statement_id=stmt_id,
                        defaults=defaults,
                    )
                    if was_created:
                        created_count += 1
                    else:
                        updated_count += 1

                    # Take only the newest posted statement per portfolio
                    break

                except Exception as exc:
                    msg = (
                        f"Error syncing statement for portfolio "
                        f"{portfolio.rentvine_id}: {exc}"
                    )
                    logger.error(msg)
                    errors.append(msg)

            if not posted_found and records:
                logger.info(
                    "rentvine_no_posted_statement",
                    extra={
                        "portfolio": portfolio.name,
                        "rentvine_id": portfolio.rentvine_id,
                        "records_checked": len(records),
                    },
                )

            portfolios_processed += 1

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=total_fetched,
            errors=errors,
        )
        return {
            "portfolios_processed": portfolios_processed,
            "fetched": total_fetched,
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


def link_owners_from_portfolio_contacts():
    """
    Parse Portfolio.raw_data.portfolio.contacts to link Owners to Portfolios.

    RentVine's owner API doesn't return portfolioIDs, but the portfolio API
    stores contacts as a JSON string in raw_data.portfolio.contacts:
      [{"contactID": "1084", "contactTypeID": "1", ...}, ...]

    contactTypeID=1 = Owner. We match contactID to Owner.rentvine_contact_id.
    Returns counts dict.
    """
    linked = 0
    skipped = 0

    for portfolio in Portfolio.objects.all():
        contacts_raw = (
            portfolio.raw_data.get("portfolio", {}).get("contacts")
        )
        if not contacts_raw:
            continue

        if isinstance(contacts_raw, str):
            try:
                contacts = json.loads(contacts_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Portfolio %s: invalid contacts JSON", portfolio.rentvine_id
                )
                continue
        else:
            contacts = contacts_raw

        if not isinstance(contacts, list):
            continue

        for contact in contacts:
            contact_id_str = contact.get("contactID")
            if not contact_id_str:
                continue

            try:
                contact_id = int(contact_id_str)
            except (ValueError, TypeError):
                continue

            try:
                owner = Owner.objects.get(rentvine_contact_id=contact_id)
                owner.portfolios.add(portfolio)
                linked += 1
            except Owner.DoesNotExist:
                skipped += 1
                logger.debug(
                    "Owner contact %d not found for portfolio %s",
                    contact_id,
                    portfolio.rentvine_id,
                )

    return {"linked": linked, "skipped": skipped}


class WorkOrderSyncService(_BaseSyncService):
    """
    Sync work orders from RentVine GET /maintenance/work-orders.

    Resolves FKs to core.Property, core.Unit, core.Portfolio, and core.Lease
    by RentVine ID. Leaves FKs null when the referenced object hasn't been
    synced yet — never crashes on a missing reference.
    """

    endpoint = "maintenance/work-orders"

    def sync(self, dry_run=False):
        from maintenance.models import WorkOrder

        log = self._create_log()
        try:
            records = self.client.get_all("/maintenance/work-orders")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []
        fetched_rv_ids = set()
        resolved_property = 0
        resolved_unit = 0
        resolved_portfolio = 0
        resolved_lease = 0
        resolved_vendor = 0

        for record in records:
            try:
                (
                    rentvine_id,
                    property_rv_id,
                    unit_rv_id,
                    portfolio_rv_id,
                    lease_rv_id,
                    defaults,
                ) = map_work_order(record)

                if dry_run:
                    logger.info(
                        "DRY RUN work_order %s: number=%s, property=%s, unit=%s",
                        rentvine_id,
                        defaults.get("work_order_number"),
                        property_rv_id,
                        unit_rv_id,
                    )
                    continue

                # Resolve Property FK
                prop = None
                if property_rv_id:
                    try:
                        prop = Property.objects.get(rentvine_id=property_rv_id)
                        resolved_property += 1
                    except Property.DoesNotExist:
                        logger.warning(
                            "Property %s not found for work order %s",
                            property_rv_id,
                            rentvine_id,
                        )
                defaults["property"] = prop

                # Resolve Unit FK
                unit = None
                if unit_rv_id:
                    try:
                        unit = Unit.objects.get(rentvine_id=unit_rv_id)
                        resolved_unit += 1
                    except Unit.DoesNotExist:
                        logger.warning(
                            "Unit %s not found for work order %s",
                            unit_rv_id,
                            rentvine_id,
                        )
                defaults["unit"] = unit

                # Resolve Portfolio FK (with fallback through property)
                portfolio = None
                if portfolio_rv_id:
                    try:
                        portfolio = Portfolio.objects.get(
                            rentvine_id=portfolio_rv_id
                        )
                    except Portfolio.DoesNotExist:
                        logger.warning(
                            "Portfolio %s not found for work order %s",
                            portfolio_rv_id,
                            rentvine_id,
                        )
                if portfolio is None and prop and prop.portfolio_id:
                    portfolio = prop.portfolio
                if portfolio:
                    resolved_portfolio += 1
                defaults["portfolio"] = portfolio

                # Resolve Lease FK
                lease = None
                if lease_rv_id:
                    try:
                        lease = Lease.objects.get(rentvine_id=lease_rv_id)
                        resolved_lease += 1
                    except Lease.DoesNotExist:
                        logger.warning(
                            "Lease %s not found for work order %s",
                            lease_rv_id,
                            rentvine_id,
                        )
                defaults["lease"] = lease

                # Vendor resolution counter
                if defaults.get("vendor_contact_id"):
                    resolved_vendor += 1

                defaults["is_active"] = True
                fetched_rv_ids.add(rentvine_id)
                _, was_created = WorkOrder.objects.update_or_create(
                    rentvine_id=rentvine_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

            except Exception as exc:
                msg = f"Error syncing work order record: {exc}"
                logger.error(msg)
                errors.append(msg)

        # ── Reconcile deletions (soft-delete) ───────────────────────
        # Only run if the fetch clearly succeeded: no errors AND we
        # actually received records.  A partial or failed fetch must
        # never deactivate everything.
        deactivated_count = 0
        deactivated_ids = []
        if not dry_run and not errors and len(fetched_rv_ids) > 0:
            stale_qs = WorkOrder.objects.filter(is_active=True).exclude(
                rentvine_id__in=fetched_rv_ids
            )
            deactivated_ids = list(
                stale_qs.values_list("rentvine_id", flat=True)
            )
            if deactivated_ids:
                deactivated_count = stale_qs.update(is_active=False)
                logger.info(
                    "work_order_reconcile_deactivated",
                    extra={
                        "deactivated_count": deactivated_count,
                        "rentvine_ids": deactivated_ids,
                    },
                )
        elif not dry_run and (errors or len(fetched_rv_ids) == 0):
            logger.warning(
                "work_order_reconcile_skipped",
                extra={
                    "reason": "fetch_errors" if errors else "zero_records",
                    "error_count": len(errors),
                    "fetched": len(records),
                },
            )

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            fetched=len(records),
            errors=errors,
        )

        result = {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
            "deactivated": deactivated_count,
            "resolved_property": resolved_property,
            "resolved_unit": resolved_unit,
            "resolved_portfolio": resolved_portfolio,
            "resolved_lease": resolved_lease,
            "resolved_vendor": resolved_vendor,
        }
        logger.info("WorkOrderSync result: %s", result)
        return result

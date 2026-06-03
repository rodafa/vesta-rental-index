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
    map_property,
    map_tenant_from_lease,
    map_unit,
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

"""
Sync services that pull data from Rentvine and upsert into Django models.

Each service:
  - Fetches records via RentvineClient
  - Maps fields via mappers
  - Uses update_or_create for idempotent sync
  - Logs everything to APISyncLog
  - Isolates per-record errors so one bad record doesn't block the rest
"""

import logging
from datetime import date, datetime

from django.utils import timezone

from decimal import Decimal

from integrations.models import APISyncLog
from leasing.models import Lease, Tenant
from properties.models import Portfolio, Owner, Property, Unit

from .client import RentvineClient
from .mappers import (
    map_portfolio, map_owner, map_property, map_unit,
    map_lease, map_tenant_from_lease,
)

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
            log.error_message = "\n".join(errors[:50])  # Cap stored errors
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

        # Link owners to portfolios from portfolio.contacts JSON
        from integrations.management.commands.link_owner_portfolios import (
            link_owners_from_portfolio_contacts,
        )
        if not dry_run:
            link_result = link_owners_from_portfolio_contacts()
            logger.info(
                "Owner-portfolio links: %d linked, %d skipped",
                link_result["linked"],
                link_result["skipped"],
            )

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
        /properties/{id}/units for each. Errors on individual
        properties are logged and skipped.
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
    3. Fetch /leases/{id}/tenants → upsert Tenant records → set M2M
    4. Fetch /leases/{id}/recurring-charges → sum isRent charges → update rent_amount
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
                    # Link to most recent previous lease
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

                # --- Sync tenants for this lease ---
                self._sync_lease_tenants(lease_obj, rentvine_id, errors)

                # --- Sync recurring charges to compute rent_amount ---
                self._sync_rent_amount(lease_obj, rentvine_id, errors)

            except Exception as exc:
                msg = f"Error syncing lease record {record}: {exc}"
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
                # Single record or nested response
                tenant_records = tenant_records if isinstance(tenant_records, list) else [tenant_records]
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
                charge_records = charge_records if isinstance(charge_records, list) else [charge_records]
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
                # Unwrap envelope: {"recurringCharge": {...}, "account": {...}}
                account = charge_data.get("account", {}) if isinstance(charge_data, dict) else {}
                charge = charge_data.get("recurringCharge", charge_data) if isinstance(charge_data, dict) else {}

                is_rent = str(account.get("isRent", "0")) == "1"
                if not is_rent:
                    continue

                # Skip expired charges (one-time pro-rates, old rent amounts)
                end_date_str = charge.get("endDate")
                if end_date_str:
                    if date.fromisoformat(end_date_str) < today:
                        continue

                amount = Decimal(str(charge.get("amount") or charge.get("chargeAmount") or 0))
                rent_total += amount
                found_rent = True

                # Track pet rent separately
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

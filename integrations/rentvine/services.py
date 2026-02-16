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
from datetime import datetime

from django.utils import timezone

from integrations.models import APISyncLog
from properties.models import Portfolio, Owner, Property, Unit

from .client import RentvineClient
from .mappers import map_portfolio, map_owner, map_property, map_unit

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

"""
Sync services that pull data from RentEngine and upsert into Django models.

Each service:
  - Fetches records via RentEngineClient
  - Maps fields via mappers
  - Uses update_or_create for idempotent sync
  - Logs everything to APISyncLog
  - Isolates per-record errors so one bad record doesn't block the rest
"""

import logging

from django.db.models import Q
from django.utils import timezone

from integrations.models import APISyncLog
from market.models import DailyUnitSnapshot, DailyLeasingSummary
from properties.models import Unit

from .client import RentEngineClient
from .mappers import map_re_unit, map_daily_snapshot, map_leasing_performance

logger = logging.getLogger(__name__)


class _BaseSyncService:
    """Shared sync scaffolding."""

    source = "rentengine"
    endpoint = ""
    sync_type = "full"

    def __init__(self, client=None):
        self.client = client or RentEngineClient()

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


class UnitSyncService(_BaseSyncService):
    """
    Sync units from RentEngine /units endpoint.

    - Fetches all units from RentEngine
    - Matches to existing Unit records by rentengine_id (if already set) or by address
    - Sets Unit.rentengine_id on matched records
    - Creates DailyUnitSnapshot for today via update_or_create(unit, snapshot_date)
    """

    endpoint = "units"

    def sync(self, dry_run=False):
        log = self._create_log()
        today = timezone.now().date()

        try:
            records = self.client.get_all("/units")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        snapshot_count = 0
        errors = []

        for record in records:
            try:
                rentengine_id, defaults = map_re_unit(record)

                if dry_run:
                    logger.info(
                        "DRY RUN unit %s: %s (%s, %s)",
                        rentengine_id,
                        defaults.get("name"),
                        defaults.get("address_line_1"),
                        defaults.get("city"),
                    )
                    continue

                # Try to find an existing unit
                unit = self._match_unit(rentengine_id, defaults)

                if unit is None:
                    logger.warning(
                        "RentEngine unit %s (%s) could not be matched to an existing unit, skipping",
                        rentengine_id, defaults.get("address_line_1"),
                    )
                    continue

                # Set the rentengine_id if not already set
                if unit.rentengine_id != rentengine_id:
                    unit.rentengine_id = rentengine_id
                    unit.save(update_fields=["rentengine_id", "updated_at"])
                    created_count += 1
                else:
                    updated_count += 1

                # Create/update DailyUnitSnapshot for today
                snapshot_defaults = map_daily_snapshot(record, today)
                DailyUnitSnapshot.objects.update_or_create(
                    unit=unit,
                    snapshot_date=today,
                    defaults=snapshot_defaults,
                )
                snapshot_count += 1

            except Exception as exc:
                msg = f"Error syncing RentEngine unit record: {exc}"
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
            "snapshots": snapshot_count,
            "errors": len(errors),
        }

    def _match_unit(self, rentengine_id, defaults):
        """
        Match a RentEngine unit to an existing Unit record.

        Priority:
        1. By rentengine_id (already linked)
        2. By address (address_line_1 + city + state + postal_code)
        """
        # 1. Direct match by rentengine_id
        try:
            return Unit.objects.get(rentengine_id=rentengine_id)
        except Unit.DoesNotExist:
            pass

        # 2. Address match
        addr = defaults.get("address_line_1", "").strip()
        city = defaults.get("city", "").strip()
        state = defaults.get("state", "").strip()
        postal = defaults.get("postal_code", "").strip()

        if addr:
            query = Q(address_line_1__iexact=addr)
            if city:
                query &= Q(city__iexact=city)
            if state:
                query &= Q(state__iexact=state)
            if postal:
                query &= Q(postal_code=postal)

            matches = Unit.objects.filter(query, rentengine_id__isnull=True)
            if matches.count() == 1:
                return matches.first()
            elif matches.count() > 1:
                logger.warning(
                    "Multiple units match address '%s, %s, %s %s' — skipping ambiguous match",
                    addr, city, state, postal,
                )

        return None


class LeasingPerformanceSyncService(_BaseSyncService):
    """
    Sync leasing performance from RentEngine
    /reporting/leasing-performance/units/{unitId} endpoint.

    - Iterates all units with rentengine_id set
    - Fetches leasing performance with today as start/end date
    - Creates DailyLeasingSummary with showing counts (completed vs missed)
    - Per-unit error isolation
    """

    endpoint = "reporting/leasing-performance"

    def sync(self, dry_run=False):
        log = self._create_log()
        today = timezone.now().date()
        today_str = today.isoformat()

        units = Unit.objects.filter(rentengine_id__isnull=False)

        total_fetched = 0
        created_count = 0
        updated_count = 0
        errors = []

        for unit in units:
            try:
                data = self.client.get(
                    f"/reporting/leasing-performance/units/{unit.rentengine_id}",
                    params={"start": today_str, "end": today_str},
                )
            except Exception as exc:
                msg = f"Error fetching leasing performance for unit {unit.rentengine_id}: {exc}"
                logger.error(msg)
                errors.append(msg)
                continue

            total_fetched += 1

            try:
                if dry_run:
                    logger.info(
                        "DRY RUN leasing performance unit %s: %s",
                        unit.rentengine_id, data,
                    )
                    continue

                defaults = map_leasing_performance(data)
                defaults["property_display_name"] = str(unit.property) if unit.property_id else ""

                _, was_created = DailyLeasingSummary.objects.update_or_create(
                    unit=unit,
                    summary_date=today,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

            except Exception as exc:
                msg = f"Error syncing leasing performance for unit {unit.rentengine_id}: {exc}"
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

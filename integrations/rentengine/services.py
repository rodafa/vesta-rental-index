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
import re

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

    @staticmethod
    def _extract_unit_id(unit):
        """
        Extract the unit/apartment identifier from a Rentvine Unit record.

        Checks (in order):
        - address_line_2: "Unit 5", "Apt B", "Unit A - Upstairs Unit", "Unit #7", "A"
        - name: "1", "A", "Unit 4", "245 - 3"
        - address_line_1 suffix: "130 Reems Creek Road - 1", "588 Ray Hill Road - A"

        Returns the normalised identifier (e.g. "5", "B", "A") or empty string.
        """
        # Pattern: "Unit 5", "Unit #7", "Apt B", "Unit A - Top Level", "#7"
        unit_id_re = re.compile(
            r'(?:unit|apt|#)\s*#?\s*([A-Za-z0-9]+)', re.IGNORECASE
        )

        for field in (unit.address_line_2, unit.name):
            val = (field or "").strip()
            if not val:
                continue
            m = unit_id_re.search(val)
            if m:
                return m.group(1).upper()
            # Bare single token like "A", "3", "B"
            if re.fullmatch(r'[A-Za-z0-9]+', val):
                return val.upper()

        # address_line_1 suffix: "130 Reems Creek Road - 1"
        addr = (unit.address_line_1 or "").strip()
        m = re.search(r'\s*[-–]\s*([A-Za-z0-9]+)\s*$', addr)
        if m:
            return m.group(1).upper()

        return ""

    def _match_unit(self, rentengine_id, defaults):
        """
        Match a RentEngine unit to an existing Unit record.

        Priority:
        1. By rentengine_id (already linked)
        2. By postal_code + street_number — unique match
        3. By postal_code + street_number + unit_number disambiguation
        4. Fallback: full address_line_1 iexact match
        """
        # 1. Direct match by rentengine_id
        try:
            return Unit.objects.get(rentengine_id=rentengine_id)
        except Unit.DoesNotExist:
            pass

        # 1.5. Match via RentVine listing ID from extracted_from URL
        extracted_from = defaults.get("extracted_from") or ""
        if extracted_from and "/listings/" in extracted_from:
            try:
                rv_id = int(extracted_from.rsplit("/", 1)[-1])
                return Unit.objects.get(rentvine_id=rv_id)
            except (ValueError, Unit.DoesNotExist):
                pass

        postal = defaults.get("postal_code", "").strip()
        street_num = defaults.get("street_number", "").strip()
        unit_num = defaults.get("unit_number", "").strip().upper()
        addr = defaults.get("address_line_1", "").strip()

        # 2. Match by postal_code + address starts with street_number
        if postal and street_num:
            matches = list(Unit.objects.filter(
                postal_code=postal,
                address_line_1__istartswith=f"{street_num} ",
                rentengine_id__isnull=True,
            ))
            if len(matches) == 1:
                return matches[0]

            # 3. Multiple candidates — disambiguate by unit number
            if len(matches) > 1 and unit_num:
                for candidate in matches:
                    if self._extract_unit_id(candidate) == unit_num:
                        return candidate
                logger.warning(
                    "Unit number '%s' did not match any candidate at postal_code=%s street_number=%s",
                    unit_num, postal, street_num,
                )
                return None

            if len(matches) > 1:
                logger.warning(
                    "Multiple units match postal_code=%s street_number=%s and no unit number to disambiguate",
                    postal, street_num,
                )
                return None

        # 4. Fallback: exact address match
        if addr:
            city = defaults.get("city", "").strip()
            query = Q(address_line_1__iexact=addr)
            if city:
                query &= Q(city__iexact=city)
            if postal:
                query &= Q(postal_code=postal)

            matches = Unit.objects.filter(query, rentengine_id__isnull=True)
            if matches.count() == 1:
                return matches.first()

        return None


class LeasingPerformanceSyncService(_BaseSyncService):
    """
    Sync leasing performance from RentEngine
    /reporting/leasing-performance/units/{unitId} endpoint.

    - Iterates all units with rentengine_id set
    - Fetches cumulative leasing performance (full listing period to today)
    - Creates DailyLeasingSummary with showing counts (completed vs missed)
    - Writes days_on_market from leasing report to DailyUnitSnapshot
    - Per-unit error isolation
    """

    endpoint = "reporting/leasing-performance"

    def sync(self, dry_run=False):
        log = self._create_log()
        today = timezone.now().date()
        today_str = today.isoformat()
        # Query the full year to capture cumulative leasing stats
        from datetime import timedelta
        start_date = (today - timedelta(days=365)).isoformat()

        units = Unit.objects.filter(rentengine_id__isnull=False)

        total_fetched = 0
        created_count = 0
        updated_count = 0
        errors = []

        for unit in units:
            try:
                data = self.client.get(
                    f"/reporting/leasing-performance/units/{unit.rentengine_id}",
                    params={
                        "start": f"{start_date}T00:00:00Z",
                        "end": f"{today_str}T23:59:59Z",
                    },
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

                summary_defaults, extra = map_leasing_performance(data)
                summary_defaults["property_display_name"] = str(unit.property) if unit.property_id else ""

                _, was_created = DailyLeasingSummary.objects.update_or_create(
                    unit=unit,
                    summary_date=today,
                    defaults=summary_defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

                # Write days_on_market to the DailyUnitSnapshot if available
                dom = extra.get("days_on_market")
                if dom is not None:
                    DailyUnitSnapshot.objects.filter(
                        unit=unit, snapshot_date=today,
                    ).update(days_on_market=dom)

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

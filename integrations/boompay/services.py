"""
Sync services that pull data from BoomPay/BoomScreen and upsert into Django models.

Each service:
  - Fetches records via BoompayClient
  - Maps fields via mappers
  - Uses update_or_create for idempotent sync
  - Logs everything to APISyncLog
  - Isolates per-record errors so one bad record doesn't block the rest
"""

import logging

from django.utils import timezone

from integrations.models import APISyncLog
from properties.models import Unit
from screening.models import ScreeningApplication, ScreeningReport

from .client import BoompayClient
from .mappers import map_application, map_report

logger = logging.getLogger(__name__)


class _BaseSyncService:
    """Shared sync scaffolding."""

    source = "boompay"
    endpoint = ""
    sync_type = "full"

    def __init__(self, client=None):
        self.client = client or BoompayClient()

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


class ApplicationSyncService(_BaseSyncService):
    endpoint = "applications"

    def _resolve_unit(self, unit_address, unit_id):
        """Try to match a Unit by address or external ID."""
        if unit_id:
            unit = Unit.objects.filter(rentvine_id=unit_id).first()
            if unit:
                return unit

        if unit_address:
            unit = Unit.objects.filter(address_line_1__iexact=unit_address).first()
            if unit:
                return unit

        return None

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/applications")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                boompay_id, defaults, unit_address, unit_id = map_application(record)

                if dry_run:
                    logger.info(
                        "DRY RUN application %s: %s (%s)",
                        boompay_id, defaults.get("applicant_name"),
                        defaults.get("status"),
                    )
                    continue

                # Try to resolve unit FK
                unit = self._resolve_unit(unit_address, unit_id)
                if unit:
                    defaults["unit"] = unit

                _, was_created = ScreeningApplication.objects.update_or_create(
                    boompay_id=boompay_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as exc:
                msg = f"Error syncing application record: {exc}"
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


class ReportSyncService(_BaseSyncService):
    endpoint = "applications/{id}/reports"

    def sync(self, dry_run=False):
        """
        Sync reports by iterating all ScreeningApplications with a boompay_id
        and fetching /applications/{id}/reports for each.
        """
        log = self._create_log()
        applications = ScreeningApplication.objects.filter(
            boompay_id__isnull=False,
        ).exclude(boompay_id="")

        total_fetched = 0
        created_count = 0
        updated_count = 0
        errors = []

        for app in applications:
            try:
                records = self.client.get_all(
                    f"/applications/{app.boompay_id}/reports"
                )
            except Exception as exc:
                msg = f"Error fetching reports for application {app.boompay_id}: {exc}"
                logger.error(msg)
                errors.append(msg)
                continue

            total_fetched += len(records)

            for record in records:
                try:
                    boompay_id, defaults = map_report(record)

                    if dry_run:
                        logger.info(
                            "DRY RUN report %s (application %s): %s - %s",
                            boompay_id, app.boompay_id,
                            defaults.get("report_type"), defaults.get("decision"),
                        )
                        continue

                    defaults["screening_application"] = app

                    _, was_created = ScreeningReport.objects.update_or_create(
                        boompay_id=boompay_id,
                        defaults=defaults,
                    )
                    if was_created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as exc:
                    msg = f"Error syncing report for application {app.boompay_id}: {exc}"
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

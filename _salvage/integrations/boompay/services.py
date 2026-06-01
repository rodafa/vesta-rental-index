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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone

from integrations.models import APISyncLog
from leasing.models import DailyLeasingMetric
from market.models import DailyLeasingSummary
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
            # Only attempt rentvine_id lookup if value is numeric
            try:
                numeric_id = int(unit_id)
                unit = Unit.objects.filter(rentvine_id=numeric_id).first()
                if unit:
                    return unit
            except (ValueError, TypeError):
                pass

        if unit_address:
            unit = Unit.objects.filter(address_line_1__iexact=unit_address).first()
            if unit:
                return unit

        return None

    def sync(self, dry_run=False):
        log = self._create_log()
        try:
            records = self.client.get_all("/partner/v1/applications")
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
                    f"/partner/v1/applications/{app.boompay_id}/reports"
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


class ApplicationCountService:
    """
    Populate DailyLeasingSummary.applications_count from ScreeningApplication
    records instead of RentEngine data.

    For each unit with screening applications, counts all applications where
    submitted_at <= target_date (cumulative) and writes the total to
    DailyLeasingSummary.applications_count.
    """

    source = "boompay"
    endpoint = "application_counts"

    def __init__(self, client=None):
        # No API client needed — this service queries local DB only.
        pass

    def _create_log(self):
        return APISyncLog.objects.create(
            source=self.source,
            endpoint=self.endpoint,
            sync_type="derived",
            status="started",
        )

    def _complete_log(self, log, *, created, updated, errors=None):
        log.status = "completed" if not errors else "partial"
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

    def sync(self, target_date=None, dry_run=False):
        target_date = target_date or date.today()
        log = self._create_log()

        try:
            return self._do_sync(log, target_date, dry_run)
        except Exception as exc:
            self._fail_log(log, exc)
            raise

    def _do_sync(self, log, target_date, dry_run):
        created_count = 0
        updated_count = 0
        errors = []

        # Get cumulative application counts per unit up to target_date
        unit_counts = (
            ScreeningApplication.objects.filter(
                unit__isnull=False,
                submitted_at__date__lte=target_date,
            )
            .values("unit_id")
            .annotate(app_count=Count("id"))
        )

        unit_count_map = {row["unit_id"]: row["app_count"] for row in unit_counts}

        # Update existing DailyLeasingSummary rows for target_date
        existing_summaries = DailyLeasingSummary.objects.filter(
            summary_date=target_date,
        ).select_related("unit")

        seen_unit_ids = set()

        for dls in existing_summaries:
            try:
                count = unit_count_map.get(dls.unit_id, 0)
                seen_unit_ids.add(dls.unit_id)

                if dls.applications_count == count:
                    continue

                if dry_run:
                    logger.info(
                        "DRY RUN %s: applications_count %d -> %d",
                        dls.unit, dls.applications_count, count,
                    )
                    continue

                dls.applications_count = count
                dls.save(update_fields=["applications_count"])
                updated_count += 1
            except Exception as exc:
                msg = f"Error updating DLS for unit {dls.unit_id}: {exc}"
                logger.error(msg)
                errors.append(msg)

        # Create DLS rows for units that have applications but no DLS yet
        for unit_id, count in unit_count_map.items():
            if unit_id in seen_unit_ids:
                continue
            try:
                unit = Unit.objects.get(pk=unit_id)
                if dry_run:
                    logger.info(
                        "DRY RUN %s: would create DLS with applications_count=%d",
                        unit, count,
                    )
                    continue

                DailyLeasingSummary.objects.create(
                    summary_date=target_date,
                    unit=unit,
                    applications_count=count,
                    property_display_name=str(unit),
                )
                created_count += 1
            except Exception as exc:
                msg = f"Error creating DLS for unit {unit_id}: {exc}"
                logger.error(msg)
                errors.append(msg)

        self._complete_log(
            log,
            created=created_count,
            updated=updated_count,
            errors=errors,
        )

        logger.info(
            "ApplicationCountService: date=%s updated=%d created=%d errors=%d",
            target_date, updated_count, created_count, len(errors),
        )

        return {
            "fetched": len(unit_count_map),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }


class DLMApplicationCountService:
    """
    Populate DailyLeasingMetric.applications_submitted from local
    ScreeningApplication records, using Eastern day boundaries.

    This is the single source of truth for application counts —
    neither the RentEngine batch nor webhooks write this field.
    """

    EASTERN = ZoneInfo("America/New_York")

    source = "boompay"
    endpoint = "dlm_application_counts"

    def _create_log(self):
        return APISyncLog.objects.create(
            source=self.source,
            endpoint=self.endpoint,
            sync_type="derived",
            status="started",
        )

    def _complete_log(self, log, *, created, updated, zero_filled, errors=None):
        log.status = "completed" if not errors else "partial"
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

    def sync(self, start_date=None, end_date=None, dry_run=False):
        yesterday = date.today() - timedelta(days=1)
        start_date = start_date or yesterday
        end_date = end_date or yesterday

        log = self._create_log()
        try:
            return self._do_sync(log, start_date, end_date, dry_run)
        except Exception as exc:
            self._fail_log(log, exc)
            raise

    def _do_sync(self, log, start_date, end_date, dry_run):
        total_created = 0
        total_updated = 0
        total_zero_filled = 0
        total_skipped_null_unit = 0
        errors = []

        # One-time warning: apps with null submitted_at
        null_submitted = ScreeningApplication.objects.filter(
            submitted_at__isnull=True,
        ).count()
        if null_submitted:
            logger.warning(
                "DLMApplicationCountService: %d ScreeningApplication(s) "
                "with null submitted_at (excluded globally)",
                null_submitted,
            )

        day = start_date
        while day <= end_date:
            try:
                result = self._sync_day(day, dry_run)
                total_created += result["created"]
                total_updated += result["updated"]
                total_zero_filled += result["zero_filled"]
                total_skipped_null_unit += result["skipped_null_unit"]
            except Exception as exc:
                msg = f"Error syncing day {day}: {exc}"
                logger.error(msg)
                errors.append(msg)
            day += timedelta(days=1)

        self._complete_log(
            log,
            created=total_created,
            updated=total_updated,
            zero_filled=total_zero_filled,
            errors=errors,
        )

        totals = {
            "created": total_created,
            "updated": total_updated,
            "zero_filled": total_zero_filled,
            "skipped_null_unit": total_skipped_null_unit,
            "skipped_null_submitted_at": null_submitted,
            "errors": len(errors),
        }
        logger.info("DLMApplicationCountService: %s", totals)
        return totals

    def _sync_day(self, day, dry_run):
        # Eastern midnight-to-midnight boundaries in UTC
        local_start = datetime(day.year, day.month, day.day, tzinfo=self.EASTERN)
        local_end = local_start + timedelta(days=1)

        # Per-unit application counts for this Eastern day
        qs = (
            ScreeningApplication.objects.filter(
                unit__isnull=False,
                submitted_at__isnull=False,
                submitted_at__gte=local_start,
                submitted_at__lt=local_end,
            )
            .values("unit_id")
            .annotate(count=Count("id"))
        )
        unit_counts = {row["unit_id"]: row["count"] for row in qs}

        # Warn about null-unit apps in this day window
        null_unit_count = ScreeningApplication.objects.filter(
            unit__isnull=True,
            submitted_at__isnull=False,
            submitted_at__gte=local_start,
            submitted_at__lt=local_end,
        ).count()
        if null_unit_count:
            logger.warning(
                "DLMApplicationCountService: %d app(s) on %s with null unit (skipped)",
                null_unit_count,
                day,
            )

        created = 0
        updated = 0

        for unit_id, count in unit_counts.items():
            if dry_run:
                existing = DailyLeasingMetric.objects.filter(
                    unit_id=unit_id, date=day,
                ).first()
                old_val = existing.applications_submitted if existing else 0
                logger.info(
                    "DRY RUN unit_id=%s date=%s: applications_submitted %d -> %d",
                    unit_id, day, old_val, count,
                )
                continue

            metric, was_created = DailyLeasingMetric.objects.get_or_create(
                unit_id=unit_id,
                date=day,
                defaults={"source": "boompay"},
            )
            if was_created:
                metric.applications_submitted = count
                metric.save(update_fields=["applications_submitted"])
                created += 1
            elif metric.applications_submitted != count:
                metric.applications_submitted = count
                metric.save(update_fields=["applications_submitted"])
                updated += 1

        # Zero-fill: existing DLM rows with nonzero applications_submitted
        # where the unit had no BoomPay apps this day. Exclude csv_backfill.
        zero_filled = 0
        if not dry_run:
            stale = DailyLeasingMetric.objects.filter(
                date=day,
                applications_submitted__gt=0,
            ).exclude(
                unit_id__in=unit_counts.keys(),
            ).exclude(
                source="csv_backfill",
            )
            zero_filled = stale.update(applications_submitted=0)
        else:
            stale = DailyLeasingMetric.objects.filter(
                date=day,
                applications_submitted__gt=0,
            ).exclude(
                unit_id__in=unit_counts.keys(),
            ).exclude(
                source="csv_backfill",
            )
            for m in stale:
                logger.info(
                    "DRY RUN zero-fill unit_id=%s date=%s: "
                    "applications_submitted %d -> 0",
                    m.unit_id, day, m.applications_submitted,
                )
            zero_filled = stale.count()

        return {
            "created": created,
            "updated": updated,
            "zero_filled": zero_filled,
            "skipped_null_unit": null_unit_count,
        }

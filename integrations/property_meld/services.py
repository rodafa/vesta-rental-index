"""
Sync service that pulls Meld data from Property Meld and upserts into the Meld model.
"""

import logging

from django.utils import timezone

from integrations.models import APISyncLog
from maintenance.models import Meld

from .client import PropertyMeldClient
from .mappers import map_meld

logger = logging.getLogger(__name__)


class _BaseSyncService:
    """Shared sync scaffolding (mirrors boompay/services.py pattern)."""

    source = "property_meld"
    endpoint = ""
    sync_type = "full"

    def __init__(self, client=None):
        self.client = client or PropertyMeldClient()

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


class MeldSyncService(_BaseSyncService):
    endpoint = "meld"

    def sync(self, dry_run=False):
        """
        Fetch all melds from /meld/, map them, and upsert on property_meld_id.
        Returns {"fetched", "created", "updated", "errors"}.
        """
        log = self._create_log()
        try:
            records = self.client.get_all("/meld/")
        except Exception as exc:
            self._fail_log(log, exc)
            raise

        created_count = 0
        updated_count = 0
        errors = []

        for record in records:
            try:
                property_meld_id, defaults = map_meld(record)

                if dry_run:
                    logger.info(
                        "DRY RUN meld %s: %s | status=%s | priority=%s | vendor=%s",
                        property_meld_id,
                        defaults.get("brief_description", "")[:60],
                        defaults.get("status"),
                        defaults.get("priority"),
                        defaults.get("assigned_vendor_name"),
                    )
                    continue

                _, was_created = Meld.objects.update_or_create(
                    property_meld_id=property_meld_id,
                    defaults=defaults,
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

            except Exception as exc:
                msg = f"Error syncing meld record: {exc}"
                logger.error(msg)
                errors.append(msg)

        if not dry_run:
            self._complete_log(
                log,
                created=created_count,
                updated=updated_count,
                fetched=len(records),
                errors=errors,
            )
        else:
            log.status = "completed"
            log.records_fetched = len(records)
            log.completed_at = timezone.now()
            log.save()

        return {
            "fetched": len(records),
            "created": created_count,
            "updated": updated_count,
            "errors": len(errors),
        }

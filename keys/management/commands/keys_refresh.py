"""Management command: refresh KeyBot access-note data from upstream APIs."""

import logging
import sys
import time
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Unit
from keys.loader import load_access_notes
from keys.slack import post_message
from keys.sources import fetch_rentengine_index, fetch_rentvine_key_notes

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _et_timestamp():
    """Current time in ET as '2:05 PM ET', portable across OS."""
    now = datetime.now(_ET)
    hour = now.hour % 12 or 12
    minute = now.strftime("%M")
    ampm = "AM" if now.hour < 12 else "PM"
    return f"{hour}:{minute} {ampm} ET"


def _alert(message):
    """Post a single failure alert to the KeyBot channel."""
    try:
        channel = settings.KEYBOT_CHANNEL_ID
        post_message(channel, message)
    except Exception:
        logger.exception("keys_refresh_alert_failed")


class Command(BaseCommand):
    help = "Sync upstream data and refresh KeyBot access notes."

    def handle(self, *args, **options):
        t0 = time.time()

        try:
            # ── 1. Sync core models ──────────────────────────────
            call_command(
                "sync_rentvine_properties",
                stdout=self.stdout,
            )
            call_command(
                "sync_rentvine_units",
                stdout=self.stdout,
            )

            re_err = StringIO()
            call_command(
                "sync_rentengine_units",
                apply=True,
                stdout=self.stdout,
                stderr=re_err,
            )
            re_err_text = re_err.getvalue()
            if "aborting" in re_err_text.lower():
                raise RuntimeError(
                    f"sync_rentengine_units aborted: "
                    f"{re_err_text.splitlines()[0][:200]}"
                )

            # ── 2. Fetch access-note sources ─────────────────────
            db_units = list(
                Unit.objects.filter(rentvine_id__isnull=False)
                .select_related("property")
            )
            if not db_units:
                raise RuntimeError("No Rentvine-linked units in DB")

            unit_notes, property_notes, rv_errors = (
                fetch_rentvine_key_notes(db_units)
            )
            re_index, re_errors = fetch_rentengine_index(db_units)

            # Merge fetch errors — sources.py records per-unit errors
            # instead of raising.
            fetch_errors = {}
            for uid, errs in rv_errors.items():
                fetch_errors.setdefault(uid, []).extend(errs)
            for uid, errs in re_errors.items():
                fetch_errors.setdefault(uid, []).extend(errs)

            if fetch_errors:
                raise RuntimeError(
                    f"Fetch errors on {len(fetch_errors)} unit(s)"
                )

            # ── 3. Load access notes (never tags) ────────────────
            with transaction.atomic():
                note_stats = load_access_notes(
                    db_units, unit_notes, property_notes, re_index,
                )

        except Exception as exc:
            elapsed = time.time() - t0
            summary = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.error(
                f"keys_refresh_failed"
                f" error={summary}"
                f" elapsed_s={elapsed:.1f}"
            )
            _alert(
                f"\u26a0\ufe0f KeyBot refresh failed at "
                f"{_et_timestamp()}: "
                f"{summary}. Data may be stale."
            )
            sys.exit(1)

        elapsed = time.time() - t0
        self.stdout.write(
            f"Done: "
            f"notes_created={note_stats['created']} "
            f"notes_updated={note_stats['updated']} "
            f"notes_unchanged={note_stats['unchanged']} "
            f"units_synced={len(db_units)} "
            f"duration_s={elapsed:.1f}"
        )
        logger.info(
            f"keys_refresh_ok"
            f" notes_created={note_stats['created']}"
            f" notes_updated={note_stats['updated']}"
            f" notes_unchanged={note_stats['unchanged']}"
            f" units_synced={len(db_units)}"
            f" elapsed_s={elapsed:.1f}"
        )

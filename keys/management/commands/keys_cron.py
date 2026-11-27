"""Hourly cron entry point for KeyBot.

Always runs keys_remind.  Runs keys_refresh only at ET hours 6 and 18.
Monday 9 AM ET: posts key health report to #keys.
Daily 6 AM ET: upserts KeyHealthSnapshot (after refresh).
Exits non-zero if any command failed.
"""

import logging
import sys
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from keys.slack import post_message

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_REFRESH_HOURS = {6, 18}


class Command(BaseCommand):
    help = "Hourly KeyBot cron: reminders every hour, refresh at 6/18 ET."

    def handle(self, *args, **options):
        now_et = datetime.now(ET)
        hour = now_et.hour
        failed = False

        # --- Always run reminders ---
        try:
            call_command("keys_remind", stdout=StringIO())
        except SystemExit:
            # keys_remind doesn't sys.exit, but guard anyway.
            logger.error("keys_remind exited non-zero")
            _alert("keys_remind exited non-zero")
            failed = True
        except Exception as exc:
            logger.exception(f"keys_remind crashed: {exc}")
            _alert(f"keys_remind crashed: {exc}")
            failed = True

        # --- Monday 9 AM ET: post key health to #keys ---
        if now_et.weekday() == 0 and hour == 9:
            try:
                from keys.replies import format_key_health
                from keys.selectors import key_health

                health = key_health()
                text, blocks = format_key_health(health)
                post_message(
                    settings.KEYBOT_CHANNEL_ID, text, blocks=blocks,
                )
            except Exception as exc:
                logger.error(f"key_health_post crashed: {exc}")
                _alert(f"key_health_post crashed: {exc}")
                failed = True

        # --- Refresh at 6 and 18 ET only ---
        refresh_failed = False
        if hour in _REFRESH_HOURS:
            try:
                call_command("keys_refresh", stdout=StringIO())
            except SystemExit as exc:
                if exc.code != 0:
                    logger.error(f"keys_refresh exited code={exc.code}")
                    # keys_refresh already posted its own Slack alert.
                    refresh_failed = True
                    failed = True
            except Exception as exc:
                logger.exception(f"keys_refresh crashed: {exc}")
                refresh_failed = True
                failed = True

        # --- Daily 6 AM ET: snapshot key health counts ---
        if hour == 6:
            try:
                from keys.models import KeyHealthSnapshot
                from keys.selectors import key_health

                if refresh_failed:
                    logger.warning(
                        "key_health_snapshot on stale data (refresh failed)"
                    )
                health = key_health()
                counts = _snapshot_counts(health)
                KeyHealthSnapshot.objects.update_or_create(
                    date=now_et.date(),
                    defaults={"counts": counts},
                )
            except Exception as exc:
                logger.error(f"key_health_snapshot crashed: {exc}")
                _alert(f"key_health_snapshot crashed: {exc}")
                failed = True

        status = "FAIL" if failed else "ok"
        self.stdout.write(f"keys_cron hour_et={hour} status={status}")
        logger.info(f"keys_cron hour_et={hour} status={status}")

        if failed:
            sys.exit(1)


def _alert(message: str) -> None:
    """Best-effort Slack alert to #keys."""
    try:
        channel = settings.KEYBOT_CHANNEL_ID
        post_message(channel, f":warning: *KeyBot cron:* {message}")
    except Exception:
        logger.exception("keys_cron alert post failed")


def _snapshot_counts(health: dict) -> dict:
    """Extract integer counts from a key_health() result for snapshot storage."""
    counts = {
        "vacant_no_key_no_code": len(health["vacant_no_key_no_code"]),
        "vacant_no_key": len(health["vacant_no_key"]),
        "vacant_no_code": len(health["vacant_no_code"]),
        "overdue_checkouts": len(health["overdue_checkouts"]),
        "missing_tags": len(health["missing_tags"]),
        "duplicate_tags": len(health["duplicate_tags"]),
        "building_tags": len(health.get("building_tags", [])),
        "orphan_tags": len(health["orphan_tags"]),
        "occupied_no_key": len(health["occupied_no_key"]),
        "occupied_no_code": len(health["occupied_no_code"]),
        "unknown_no_key": health["unknown_no_key_count"],
        "unknown_no_code": health["unknown_no_code_count"],
    }
    # Coverage totals for trend chart — store only when present so the
    # snapshot never crashes over a missing coverage key.
    if "total_units" in health:
        counts["total_units"] = health["total_units"]
        counts["with_key"] = health["with_key_count"]
        counts["with_code"] = health["with_code_count"]
        counts["with_both"] = health["with_both_count"]
    return counts

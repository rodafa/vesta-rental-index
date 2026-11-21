"""Hourly cron entry point for KeyBot.

Always runs keys_remind.  Runs keys_refresh only at ET hours 6 and 18.
Exits non-zero if either command failed.
keys_remind failure posts an alert to #keys but does not block keys_refresh.
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

        # --- Refresh at 6 and 18 ET only ---
        if hour in _REFRESH_HOURS:
            try:
                call_command("keys_refresh", stdout=StringIO())
            except SystemExit as exc:
                if exc.code != 0:
                    logger.error(f"keys_refresh exited code={exc.code}")
                    # keys_refresh already posted its own Slack alert.
                    failed = True
            except Exception as exc:
                logger.exception(f"keys_refresh crashed: {exc}")
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

"""Send key-checkout reminders to the #keys channel.

Runs hourly via keys_cron. Only sends during 9 AM – 4:59 PM ET.
Checkouts >= 72 h get ⚠️ prefix; younger ones get ⏰.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from keys.checkouts import due_reminders, in_reminder_window, record_reminder_sent
from keys.replies import reminder_blocks
from keys.slack import post_message

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_OVERDUE_THRESHOLD = timedelta(hours=72)


class Command(BaseCommand):
    help = "Send checkout reminders to #keys for overdue key tags."

    def handle(self, *args, **options):
        now_et = datetime.now(ET)
        if not in_reminder_window(now_et):
            self.stdout.write(f"Outside reminder window (ET hour={now_et.hour}). Skipping.")
            return

        now = timezone.now()
        channel = settings.KEYBOT_CHANNEL_ID
        checkouts = due_reminders(now)
        sent = 0

        for co in checkouts:
            is_overdue = (now - co.checked_out_at) >= _OVERDUE_THRESHOLD
            text, blocks = reminder_blocks(
                key_id=co.tag.key_id,
                slack_user_id=co.checked_out_by_slack_id,
                checked_out_at=co.checked_out_at,
                now=now,
                checkout_id=co.pk,
                is_overdue=is_overdue,
            )
            try:
                post_message(channel, text, blocks=blocks)
                record_reminder_sent(co)
                sent += 1
            except Exception:
                logger.exception(
                    f"keys_remind_send_failed tag={co.tag.key_id}"
                    f" checkout={co.pk}"
                )

        self.stdout.write(f"keys_remind sent={sent}")
        logger.info(f"keys_remind sent={sent}")

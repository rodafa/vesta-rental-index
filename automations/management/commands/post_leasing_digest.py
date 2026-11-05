"""
Post a leasing digest to Slack.

Usage:
    python manage.py post_leasing_digest --daily --dry-run
    python manage.py post_leasing_digest --daily --live
    python manage.py post_leasing_digest --daily --live --scheduled
    python manage.py post_leasing_digest --weekly --dry-run
    python manage.py post_leasing_digest --weekly --live
    python manage.py post_leasing_digest --weekly --live --scheduled
    python manage.py post_leasing_digest --weekly --dry-run --as-of 2026-10-26

Re-runs will post a duplicate — there is no state table tracking what has
already been posted. Check Slack before re-running manually.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from automations.digest_config import DOM_THRESHOLD
from automations.digest_data import fetch_daily_data, fetch_weekly_data
from automations.digest_format import (
    check_dom_list_render,
    format_daily_digest,
    format_weekly_digest,
)
from automations.digest_metrics import (
    _available_rows,
    compute_apps_submitted,
    compute_call_answer_rate,
    compute_daily_showings,
    compute_daily_tasks,
    compute_feedback_captured,
    compute_lead_to_showing,
    compute_reply_time,
    compute_reply_time_history,
    compute_showing_completion,
    compute_stale_apps,
    compute_units_leased,
    compute_units_past_dom,
    compute_week_window,
    compute_yesterday_leads,
    score_measurables,
)
from automations.services import notify_slack
from integrations.rentengine.client import RentEngineClient

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

DAILY_HOUR_ET = 8
WEEKLY_HOUR_ET = 7


class Command(BaseCommand):
    help = "Post a leasing digest (daily or weekly) to Slack."

    def add_arguments(self, parser):
        kind = parser.add_mutually_exclusive_group(required=True)
        kind.add_argument("--daily", action="store_true", default=False)
        kind.add_argument("--weekly", action="store_true", default=False)

        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true", default=False)
        mode.add_argument("--live", action="store_true", default=False)

        parser.add_argument(
            "--as-of", type=str, default=None,
            help="Override today (YYYY-MM-DD). Weekly: this is the Sunday ending the week.",
        )
        parser.add_argument(
            "--scheduled", action="store_true", default=False,
            help="Unattended cron mode. Exits 0 if wrong hour.",
        )

    def handle(self, *args, **options):
        is_daily = options["daily"]
        is_weekly = options["weekly"]
        dry_run = options["dry_run"]
        kind_label = "daily" if is_daily else "weekly"
        mode_label = "dry-run" if dry_run else "live"

        # Resolve today
        if options["as_of"]:
            today = date.fromisoformat(options["as_of"])
        else:
            today = datetime.now(ET).date()

        logger.info(
            "digest_started",
            extra={"kind": kind_label, "as_of": str(today), "mode": mode_label},
        )

        # Hour guard for --scheduled
        if options["scheduled"]:
            now_et = datetime.now(ET)
            expected_hour = DAILY_HOUR_ET if is_daily else WEEKLY_HOUR_ET
            if now_et.hour != expected_hour:
                logger.info(
                    "digest_skipped_wrong_hour",
                    extra={
                        "kind": kind_label,
                        "current_hour_et": now_et.hour,
                        "expected_hour": expected_hour,
                    },
                )
                return
            if is_weekly and now_et.weekday() != 0:  # Monday
                logger.info(
                    "digest_skipped_wrong_day",
                    extra={"kind": kind_label, "weekday": now_et.weekday()},
                )
                return

        webhook_url = settings.SLACK_LEASING_WEBHOOK_URL
        if not webhook_url and not dry_run:
            raise CommandError("SLACK_LEASING_WEBHOOK_URL not configured")

        try:
            client = RentEngineClient()
        except Exception as exc:
            self._fail(kind_label, "client_init", str(exc), dry_run, webhook_url)
            raise CommandError(str(exc)) from exc

        try:
            if is_daily:
                messages = self._build_daily(client, today)
            else:
                messages = self._build_weekly(client, today)
        except Exception as exc:
            self._fail(kind_label, "data_fetch", str(exc), dry_run, webhook_url)
            raise CommandError(str(exc)) from exc

        if dry_run:
            for i, msg in enumerate(messages, 1):
                self.stdout.write(f"\n{'='*60}")
                self.stdout.write(f"MESSAGE {i} of {len(messages)} ({len(msg)} chars)")
                self.stdout.write(f"{'='*60}")
                self.stdout.write(msg)
            return

        # Live mode: post to Slack
        for i, msg in enumerate(messages, 1):
            try:
                notify_slack(msg, webhook_url=webhook_url)
            except Exception as exc:
                logger.error(
                    "digest_failed",
                    extra={
                        "kind": kind_label,
                        "step": "slack_post",
                        "error": str(exc),
                        "messages_sent": i - 1,
                        "messages_total": len(messages),
                    },
                )
                if i > 1:
                    try:
                        notify_slack(
                            f"\u26a0\ufe0f Leasing digest incomplete \u2014 "
                            f"message {i} of {len(messages)} failed. Check logs.",
                            webhook_url=webhook_url,
                        )
                    except Exception:
                        pass
                raise CommandError(f"Slack post failed: {exc}") from exc

        logger.info(
            "digest_posted",
            extra={
                "kind": kind_label,
                "char_count": sum(len(m) for m in messages),
                "message_count": len(messages),
            },
        )

    def _build_daily(self, client, today):
        now_utc = datetime.now(ZoneInfo("UTC"))
        data = fetch_daily_data(client, today)

        tasks_by_type = compute_daily_tasks(data["tasks"])
        showings = compute_daily_showings(data["showings_today"], today)
        stale = compute_stale_apps(data["application_groups"], now_utc)
        yesterday_leads = compute_yesterday_leads(data["marketing_sources_yesterday"])

        # Yesterday's calls and reply time from overview
        overview_yest = data["overview_yesterday"]
        calls_answered = overview_yest["answered_inbound_calls"]
        calls_total = overview_yest["total_inbound_calls"]
        call_rate = compute_call_answer_rate(overview_yest)
        reply_time = compute_reply_time(overview_yest)

        # DOM count from property performance
        dom_result = compute_units_past_dom(data["property_performance"], today)

        return format_daily_digest(
            tasks_by_type=tasks_by_type,
            showings_today=showings,
            open_apps=stale["open_groups"],
            stale_count=stale["stale_count"],
            yesterday_leads=yesterday_leads,
            calls_answered=calls_answered,
            calls_total=calls_total,
            call_rate=call_rate,
            reply_time=reply_time,
            dom_count=dom_result["count"],
            app_stages=data["app_stages"],
            address_lookup=data["address_lookup"],
            today_label=f"{today.strftime('%b')} {today.day}",
        )

    def _build_weekly(self, client, today):
        week_start, week_end = compute_week_window(today)

        now_utc = datetime.now(ZoneInfo("UTC"))

        data = fetch_weekly_data(client, today, week_start, week_end)

        # Compute all metrics
        metrics = {}
        metrics["units_leased"] = compute_units_leased(data["pp_weekly"], week_start, week_end)
        metrics["apps_submitted"] = compute_apps_submitted(
            data["application_groups"], week_start, week_end,
        )

        showing_stats = compute_showing_completion(data["showings"])
        metrics["showing_completion"] = showing_stats["rate_pct"]

        metrics["lead_to_showing"] = compute_lead_to_showing(data["marketing_sources"])
        metrics["call_answer_rate"] = compute_call_answer_rate(data["overview_weekly"])

        reply_time = compute_reply_time(data["overview_weekly"])

        feedback = compute_feedback_captured(data["showings"])
        metrics["feedback_captured"] = feedback["rate_pct"]

        stale = compute_stale_apps(data["application_groups"], now_utc)
        metrics["stale_apps"] = stale["stale_count"]

        dom_result = compute_units_past_dom(data["pp_trailing"], today)
        metrics["units_past_dom"] = dom_result["count"]

        scorecard = score_measurables(metrics)
        reply_time_history = compute_reply_time_history(data["overview_history"])

        week_label = (
            f"{week_start.strftime('%b')} {week_start.day} \u2013 "
            f"{week_end.strftime('%b')} {week_end.day}"
        )

        messages = format_weekly_digest(
            scorecard=scorecard,
            reply_time=reply_time,
            reply_time_history=reply_time_history,
            dom_units=dom_result["units"],
            week_label=week_label,
        )

        # --- ASSERTION ---
        check_dom_list_render(messages, dom_result["count"])

        # --- DRY-RUN RECONCILIATION OUTPUT ---
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("RECONCILIATION")
        self.stdout.write("=" * 60)

        avail_weekly = _available_rows(data["pp_weekly"])
        avail_trailing = _available_rows(data["pp_trailing"])
        self.stdout.write(
            f"Available units (weekly pp): {len(avail_weekly)}"
        )
        self.stdout.write(
            f"Available units (trailing pp): {len(avail_trailing)}"
        )
        self.stdout.write(
            f"Units with DOM >= {DOM_THRESHOLD} (trailing): {dom_result['count']}"
        )
        self.stdout.write(
            f"Measurable 9 value: {metrics['units_past_dom']}"
        )

        self.stdout.write(
            f"\nUnits leased ({week_start} to {week_end}): "
            f"{metrics['units_leased']}"
        )

        return messages

    def _fail(self, kind, step, error, dry_run, webhook_url):
        logger.error(
            "digest_failed",
            extra={"kind": kind, "step": step, "error": error},
        )
        if not dry_run and webhook_url:
            try:
                notify_slack(
                    f"\u26a0\ufe0f Leasing digest failed: {step} \u2014 {error}. "
                    "No digest posted.",
                    webhook_url=webhook_url,
                )
            except Exception:
                pass

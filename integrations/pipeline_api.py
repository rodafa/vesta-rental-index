import datetime
import io
import logging
import threading
from typing import Optional

from django.core.management import call_command
from django.utils import timezone
from ninja import Router, Schema

from integrations.models import PipelineRun

logger = logging.getLogger(__name__)

router = Router(tags=["Pipeline"])


# --- Schemas ---


class TriggerIn(Schema):
    include_reports: bool = False
    force: bool = False


class TriggerOut(Schema):
    status: str
    run_id: int
    message: str


class StatusOut(Schema):
    run_id: int
    status: str
    include_reports: bool
    started_at: str
    completed_at: Optional[str] = None
    output: str = ""


# --- Background runner ---


def _run_pipeline(run_id, include_reports):
    """Execute run_pipeline in a background thread, capturing output."""
    from django.db import connection

    buf = io.StringIO()
    try:
        run = PipelineRun.objects.get(pk=run_id)
        run.status = "running"
        run.save(update_fields=["status"])

        cmd_kwargs = {"stdout": buf, "include_reports": include_reports}
        call_command("run_pipeline", **cmd_kwargs)

        run.refresh_from_db()
        run.status = "completed"
        run.output = buf.getvalue()
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "output", "completed_at"])

    except Exception as exc:
        logger.exception("Pipeline run %s failed", run_id)
        try:
            run = PipelineRun.objects.get(pk=run_id)
            run.status = "failed"
            run.output = buf.getvalue() + "\n\nERROR: %s" % exc
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "output", "completed_at"])
        except Exception:
            logger.exception("Could not update PipelineRun %s after failure", run_id)
    finally:
        connection.close()


# --- Endpoints ---
# Auth is inherited from the API-level default: [VestaAPIKey(), SessionAuth()]
# Both API key (for external cron) and session (for dashboard users) work.


@router.post(
    "/trigger",
    response={200: TriggerOut, 409: TriggerOut},
    summary="Trigger the full data pipeline",
)
def trigger_pipeline(request, payload: TriggerIn = TriggerIn()):
    # Mark stale runs (running for >30 min) as failed — likely killed by redeploy
    stale_cutoff = timezone.now() - datetime.timedelta(minutes=30)
    PipelineRun.objects.filter(
        status__in=["started", "running"],
        started_at__lt=stale_cutoff,
    ).update(status="failed", completed_at=timezone.now(), output="Marked as failed: likely killed by redeploy")

    # Force flag: mark ALL active runs as failed
    if payload.force:
        PipelineRun.objects.filter(
            status__in=["started", "running"],
        ).update(status="failed", completed_at=timezone.now(), output="Force-reset by user")

    # Guard: reject if a run is already in progress
    active = PipelineRun.objects.filter(status__in=["started", "running"]).first()
    if active:
        return 409, {
            "status": "conflict",
            "run_id": active.pk,
            "message": "Pipeline already running (run #%d)" % active.pk,
        }

    run = PipelineRun.objects.create(include_reports=payload.include_reports)

    thread = threading.Thread(
        target=_run_pipeline,
        args=(run.pk, payload.include_reports),
        daemon=True,
    )
    thread.start()

    return 200, {
        "status": "started",
        "run_id": run.pk,
        "message": "Pipeline started in background",
    }


@router.post(
    "/import-historical",
    response={200: dict},
    summary="One-shot: import historical XLSX + CSV + backfill aggregations",
)
def import_historical(request):
    """
    Runs the three historical import commands in a background thread.
    Safe to call multiple times — commands use update_or_create.
    Protected by the same VESTA_API_KEY auth as the pipeline trigger.
    """
    import datetime

    def _run():
        buf = io.StringIO()
        try:
            logger.info("Historical import: starting import_weekly_sheets (live)")
            call_command("import_weekly_sheets", dir="data/xlsx/live", stdout=buf, stderr=buf)
            logger.info("Historical import: starting import_weekly_sheets (non-active)")
            call_command("import_weekly_sheets", dir="data/xlsx/non-active", stdout=buf, stderr=buf)
            logger.info("Historical import: starting import_historical_events")
            call_command("import_historical_events", stdout=buf, stderr=buf)
            logger.info("Historical import: starting aggregate_market_data backfill (skip-weekly)")
            call_command(
                "aggregate_market_data",
                backfill=True,
                start="2025-11-17",
                end=datetime.date.today().isoformat(),
                skip_weekly=True,
                stdout=buf,
                stderr=buf,
            )
            logger.info("Historical import: complete")
        except Exception:
            logger.exception("Historical import failed")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "started", "message": "Historical import running in background — check server logs"}


@router.post(
    "/rebuild-weekly-data",
    response={200: dict},
    summary="Fix WeeklyLeasingSummary by clearing and re-importing from XLSX, then re-aggregating monthly reports",
)
def rebuild_weekly_data(request):
    """
    Corrects WeeklyLeasingSummary data that was corrupted by the backfill overwriting XLSX-based records.
    Steps:
      1. Delete all WeeklyLeasingSummary and MonthlyMarketReport records
      2. Re-run import_weekly_sheets from XLSX (live + non-active)
      3. Re-run monthly aggregation for Nov 2025 – current month
    """
    from market.models import MonthlyMarketReport, WeeklyLeasingSummary

    def _run():
        buf = io.StringIO()
        try:
            deleted_weekly = WeeklyLeasingSummary.objects.all().delete()
            deleted_monthly = MonthlyMarketReport.objects.all().delete()
            logger.info(
                "rebuild-weekly-data: deleted %s WeeklyLeasingSummary, %s MonthlyMarketReport",
                deleted_weekly[0], deleted_monthly[0],
            )

            logger.info("rebuild-weekly-data: re-importing XLSX (live)")
            call_command("import_weekly_sheets", dir="data/xlsx/live", stdout=buf, stderr=buf)
            logger.info("rebuild-weekly-data: re-importing XLSX (non-active)")
            call_command("import_weekly_sheets", dir="data/xlsx/non-active", stdout=buf, stderr=buf)

            # Re-run monthly aggregation for each month (Nov 2025 – today)
            today = datetime.date.today()
            current_month = datetime.date(2025, 11, 1)
            end_month = datetime.date(today.year, today.month, 1)
            while current_month <= end_month:
                month_str = f"{current_month.year}-{current_month.month:02d}"
                logger.info("rebuild-weekly-data: monthly aggregation for %s", month_str)
                call_command("aggregate_market_data", monthly=month_str, stdout=buf, stderr=buf)
                if current_month.month == 12:
                    current_month = datetime.date(current_month.year + 1, 1, 1)
                else:
                    current_month = datetime.date(current_month.year, current_month.month + 1, 1)

            logger.info("rebuild-weekly-data: complete")
        except Exception:
            logger.exception("rebuild-weekly-data failed")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "started", "message": "Weekly data rebuild running in background — check server logs"}


@router.get(
    "/status",
    response={200: StatusOut, 404: dict},
    summary="Check pipeline run status",
)
def pipeline_status(request, run_id: int = None):
    if run_id:
        run = PipelineRun.objects.filter(pk=run_id).first()
    else:
        run = PipelineRun.objects.first()  # latest by ordering

    if not run:
        return 404, {"detail": "No pipeline runs found"}

    return 200, {
        "run_id": run.pk,
        "status": run.status,
        "include_reports": run.include_reports,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "output": run.output,
    }

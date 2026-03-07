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
    # Mark stale runs (running for >60 min) as failed — likely killed by redeploy
    stale_cutoff = timezone.now() - datetime.timedelta(minutes=60)
    PipelineRun.objects.filter(
        status__in=["started", "running"],
        started_at__lt=stale_cutoff,
    ).update(status="failed", completed_at=timezone.now(), output="Marked as failed: likely killed by redeploy")

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

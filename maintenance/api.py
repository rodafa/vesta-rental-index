import io
import logging
import threading

from django.core.management import call_command
from ninja import Router

logger = logging.getLogger(__name__)

router = Router(tags=["Maintenance"])


def _run_daily_summary():
    """Run the daily summary command in a background thread."""
    try:
        call_command("property_meld_daily_summary")
    except Exception:
        logger.exception("Error running property_meld_daily_summary")


@router.post("/daily-summary/trigger")
def trigger_daily_summary(request):
    """Trigger the Property Meld daily maintenance summary Slack post."""
    threading.Thread(target=_run_daily_summary, daemon=True).start()
    return {"status": "started", "message": "Daily summary posting to Slack"}


@router.post("/daily-summary/debug")
def debug_daily_summary(request):
    """Run daily summary synchronously and return output + errors for debugging."""
    buf = io.StringIO()
    err = io.StringIO()
    try:
        call_command("property_meld_daily_summary", stdout=buf, stderr=err)
        return {"status": "ok", "output": buf.getvalue(), "errors": err.getvalue()}
    except Exception as exc:
        return {"status": "error", "output": buf.getvalue(), "errors": err.getvalue(), "exception": str(exc)}

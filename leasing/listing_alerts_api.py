import logging
import threading

from django.core.management import call_command
from ninja import Router

logger = logging.getLogger(__name__)

router = Router(tags=["Listing Alerts"])


@router.post("/check", response={200: dict})
def check_listings(request, limit: int = 0):
    """Trigger listing check in background thread. limit=N to cap listings processed per run."""

    def _run():
        from django.db import connection
        try:
            kwargs = {"limit": limit} if limit else {}
            call_command("check_new_listings", **kwargs)
        except Exception:
            logger.exception("check_new_listings failed")
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    msg = "Listing check running (limit=%d)" % limit if limit else "Listing check running in background"
    return {"status": "started", "message": msg}

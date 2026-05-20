import threading
from datetime import date
from typing import Optional

from ninja import Router, Schema

from reports.models import OwnerReportLog

from weekly_reports.api import router as weekly_update_router

router = Router(tags=["Reports"])
router.add_router("/weekly-update/", weekly_update_router)

# Module-level state for background generation jobs.
# Key: "YYYY-MM" string. Fine for single-worker deployments (Railway).
_active_runs: set = set()
_last_run_results: dict = {}


class NoteUpdate(Schema):
    generated_note: str


class GenerateSchema(Schema):
    month: str  # YYYY-MM — used as DB key and run-status polling key
    dry_run: bool = False
    owner_id: Optional[str] = None
    portfolio_name: Optional[str] = None  # exact match (case-insensitive) to RentVine portfolio name
    property_id: Optional[int] = None
    start_date: Optional[str] = None  # YYYY-MM-DD; overrides month start when provided
    end_date: Optional[str] = None    # YYYY-MM-DD inclusive; overrides month end when provided


def _note_dict(r):
    return {
        "id": r.id,
        "owner_name": r.owner_name,
        "portfolio_name": r.portfolio_name,
        "report_month": r.report_month.strftime("%Y-%m"),
        "status": r.status,
        "generated_note": r.generated_note,
        "error_message": r.error_message,
        "word_count": len(r.generated_note.split()) if r.generated_note else 0,
        "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%S"),
        # Financial statement fields
        "statement_period": r.statement_period,
        "beginning_balance": float(r.beginning_balance),
        "total_income": float(r.total_income),
        "total_expenses": float(r.total_expenses),
        "total_adjustments": float(r.total_adjustments),
        "ending_balance": float(r.ending_balance),
        "total_distribution": float(r.total_distribution),
        # Workflow timestamps
        "approved_at": r.approved_at.strftime("%Y-%m-%dT%H:%M:%S") if r.approved_at else None,
        "sent_at": r.sent_at.strftime("%Y-%m-%dT%H:%M:%S") if r.sent_at else None,
    }


# Statuses that represent a note with generated content worth showing
_ACTIVE_STATUSES = ["success", "pending", "approved", "sent"]


@router.get("/owner-notes/months")
def list_months(request):
    months = (
        OwnerReportLog.objects
        .filter(status__in=_ACTIVE_STATUSES)
        .dates("report_month", "month", order="DESC")
    )
    return [{"value": m.strftime("%Y-%m"), "label": m.strftime("%B %Y")} for m in months]


@router.get("/owner-notes/run-status")
def run_status(request, month: str):
    return {
        "running": month in _active_runs,
        "result": _last_run_results.get(month),
    }


@router.get("/owner-notes")
def list_notes(request, month: str, status: str = None):
    year, mon = int(month[:4]), int(month[5:7])
    qs = OwnerReportLog.objects.filter(
        report_month__year=year,
        report_month__month=mon,
    )
    if status:
        qs = qs.filter(status=status)
    qs = qs.order_by("portfolio_name", "owner_name", "-created_at")

    # Return only the latest entry per owner+portfolio so re-runs don't duplicate
    seen = set()
    results = []
    for r in qs:
        key = (r.owner_id, r.portfolio_name)
        if key not in seen:
            seen.add(key)
            results.append(_note_dict(r))
    return results


@router.post("/owner-notes/generate")
def generate_notes(request, payload: GenerateSchema):
    if payload.month in _active_runs:
        return {"ok": False, "error": "A run is already in progress for this month."}

    try:
        month = date.fromisoformat(f"{payload.month}-01")
    except ValueError:
        return {"ok": False, "error": f"Invalid month format: {payload.month}. Use YYYY-MM."}

    month_str = payload.month
    owner_id = payload.owner_id
    portfolio_name = payload.portfolio_name or None
    property_id = payload.property_id
    dry_run = payload.dry_run

    # Parse optional date range overrides
    start_date = None
    end_date = None
    if payload.start_date:
        try:
            start_date = date.fromisoformat(payload.start_date)
        except ValueError:
            return {"ok": False, "error": f"Invalid start_date: {payload.start_date}. Use YYYY-MM-DD."}
    if payload.end_date:
        try:
            end_date = date.fromisoformat(payload.end_date)
        except ValueError:
            return {"ok": False, "error": f"Invalid end_date: {payload.end_date}. Use YYYY-MM-DD."}

    _active_runs.add(month_str)

    def _run():
        try:
            from reports.services.monthly_owner_report import run_monthly_report
            result = run_monthly_report(
                month=month,
                owner_id=owner_id,
                portfolio_name=portfolio_name,
                property_id=property_id,
                dry_run=dry_run,
                start_date=start_date,
                end_date=end_date,
            )
            _last_run_results[month_str] = result
        except Exception as exc:
            _last_run_results[month_str] = {
                "generated": 0, "failed": 1, "skipped": 0, "error": str(exc)
            }
        finally:
            _active_runs.discard(month_str)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "month": month_str}


@router.post("/owner-notes/approve-all")
def approve_all_pending(request, month: Optional[str] = None):
    """
    Approve all reports with status='pending' (or 'success') for a given month.
    Pass ?month=YYYY-MM to restrict to a single month (recommended from the UI).
    Returns {"approved": N, "total": N}.
    """
    from reports.services.send_owner_report import approve_owner_report

    qs = OwnerReportLog.objects.filter(status__in=["pending", "success"])
    if month:
        try:
            year, mon = int(month[:4]), int(month[5:7])
            qs = qs.filter(report_month__year=year, report_month__month=mon)
        except (ValueError, IndexError):
            pass

    approved_count = 0
    for report in qs:
        try:
            approve_owner_report(report.id)
            approved_count += 1
        except Exception:
            pass

    return {"approved": approved_count, "total": approved_count}


@router.post("/owner-notes/delete-all")
def delete_all_notes(request, month: Optional[str] = None):
    """
    Hard-delete all non-sent notes for a given month.
    Pass ?month=YYYY-MM to restrict to a single month (recommended from the UI).
    Returns {"deleted": N}.
    """
    deletable = ["pending", "approved", "failed", "skipped"]
    qs = OwnerReportLog.objects.filter(status__in=deletable)
    if month:
        try:
            year, mon = int(month[:4]), int(month[5:7])
            qs = qs.filter(report_month__year=year, report_month__month=mon)
        except (ValueError, IndexError):
            pass

    count = qs.count()
    qs.delete()
    return {"deleted": count}


@router.post("/owner-notes/send-all")
def send_all_approved(request, month: Optional[str] = None):
    """
    Send all reports with status='approved'.
    Pass ?month=YYYY-MM to restrict to a single month (recommended from the UI).
    Returns {"sent": N, "failed": N, "total": N}.
    """
    from reports.services.send_owner_report import send_owner_report

    qs = OwnerReportLog.objects.filter(status="approved")
    if month:
        try:
            year, mon = int(month[:4]), int(month[5:7])
            qs = qs.filter(report_month__year=year, report_month__month=mon)
        except (ValueError, IndexError):
            pass

    sent_count = 0
    failed_count = 0
    for report in qs:
        success, _ = send_owner_report(report.id)
        if success:
            sent_count += 1
        else:
            failed_count += 1

    return {"sent": sent_count, "failed": failed_count, "total": sent_count + failed_count}


# ---------------------------------------------------------------------------
# Parameterised routes — must come AFTER all fixed-path routes above
# ---------------------------------------------------------------------------

@router.delete("/owner-notes/{note_id}")
def delete_note(request, note_id: int):
    """Hard-delete a note. Only allowed for non-sent statuses."""
    deletable = ["pending", "approved", "failed", "skipped"]
    try:
        note = OwnerReportLog.objects.get(id=note_id, status__in=deletable)
    except OwnerReportLog.DoesNotExist:
        from ninja.errors import HttpError
        raise HttpError(404, "Note not found or already sent.")
    note.delete()
    return {"ok": True, "id": note_id}


@router.put("/owner-notes/{note_id}")
def update_note(request, note_id: int, payload: NoteUpdate):
    note = OwnerReportLog.objects.get(
        id=note_id, status__in=["success", "pending", "approved"]
    )
    note.generated_note = payload.generated_note
    note.save(update_fields=["generated_note"])
    return _note_dict(note)


@router.post("/owner-notes/{note_id}/approve")
def approve_note(request, note_id: int):
    """Mark report as approved, ready to send."""
    from reports.services.send_owner_report import approve_owner_report
    report = approve_owner_report(note_id)
    return _note_dict(report)


@router.post("/owner-notes/{note_id}/send")
def send_note(request, note_id: int):
    """Send the report via SendGrid dynamic template."""
    from reports.services.send_owner_report import send_owner_report
    success, message = send_owner_report(note_id)
    report = OwnerReportLog.objects.get(id=note_id)
    result = _note_dict(report)
    result["ok"] = success
    result["message"] = message
    return result

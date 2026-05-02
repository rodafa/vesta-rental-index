import threading
from datetime import date
from typing import Optional

from ninja import Router, Schema

from reports.models import OwnerReportLog

router = Router(tags=["Reports"])

# Module-level state for background generation jobs.
# Key: "YYYY-MM" string. Fine for single-worker deployments (Railway).
_active_runs: set = set()
_last_run_results: dict = {}


class NoteUpdate(Schema):
    generated_note: str


class GenerateSchema(Schema):
    month: str  # YYYY-MM
    dry_run: bool = False
    owner_id: Optional[str] = None
    property_id: Optional[int] = None


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
def list_notes(request, month: str):
    year, mon = int(month[:4]), int(month[5:7])
    qs = OwnerReportLog.objects.filter(
        report_month__year=year,
        report_month__month=mon,
    ).order_by("owner_name", "portfolio_name", "-created_at")

    # Return only the latest entry per owner+portfolio so re-runs don't duplicate
    seen = set()
    results = []
    for r in qs:
        key = (r.owner_id, r.portfolio_name)
        if key not in seen:
            seen.add(key)
            results.append(_note_dict(r))
    return results


@router.put("/owner-notes/{note_id}")
def update_note(request, note_id: int, payload: NoteUpdate):
    note = OwnerReportLog.objects.get(
        id=note_id, status__in=["success", "pending", "approved"]
    )
    note.generated_note = payload.generated_note
    note.save(update_fields=["generated_note"])
    return _note_dict(note)


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
    property_id = payload.property_id
    dry_run = payload.dry_run

    _active_runs.add(month_str)

    def _run():
        try:
            from reports.services.monthly_owner_report import run_monthly_report
            result = run_monthly_report(
                month=month,
                owner_id=owner_id,
                property_id=property_id,
                dry_run=dry_run,
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


# ---------------------------------------------------------------------------
# Approve / Send workflow
# ---------------------------------------------------------------------------

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

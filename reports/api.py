from ninja import Router, Schema
from reports.models import OwnerReportLog

router = Router(tags=["Reports"])


class NoteUpdate(Schema):
    generated_note: str


def _note_dict(r):
    return {
        "id": r.id,
        "owner_name": r.owner_name,
        "portfolio_name": r.portfolio_name,
        "report_month": r.report_month.strftime("%Y-%m"),
        "generated_note": r.generated_note,
        "word_count": len(r.generated_note.split()) if r.generated_note else 0,
    }


@router.get("/owner-notes/months")
def list_months(request):
    months = (
        OwnerReportLog.objects
        .filter(status="success")
        .dates("report_month", "month", order="DESC")
    )
    return [{"value": m.strftime("%Y-%m"), "label": m.strftime("%B %Y")} for m in months]


@router.get("/owner-notes")
def list_notes(request, month: str):
    year, mon = int(month[:4]), int(month[5:7])
    qs = OwnerReportLog.objects.filter(
        status="success",
        report_month__year=year,
        report_month__month=mon,
    ).order_by("owner_name", "portfolio_name")
    return [_note_dict(r) for r in qs]


@router.put("/owner-notes/{note_id}")
def update_note(request, note_id: int, payload: NoteUpdate):
    note = OwnerReportLog.objects.get(id=note_id, status="success")
    note.generated_note = payload.generated_note
    note.save(update_fields=["generated_note"])
    return _note_dict(note)

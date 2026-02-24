from datetime import date
from typing import Optional

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from .models import OwnerReportNote

router = Router(tags=["Dashboard"])


class OwnerReportNoteSchema(Schema):
    id: int
    owner_id: int
    owner_name: str
    status: str
    notes_text: str
    email_body: str
    email_subject: str
    report_date: date
    sent_at: Optional[str] = None
    updated_at: str


class OwnerReportNoteCreateSchema(Schema):
    owner_id: int
    report_date: date
    notes_text: str = ""
    email_body: str = ""
    email_subject: str = ""
    status: str = "draft"


class OwnerReportNoteUpdateSchema(Schema):
    notes_text: Optional[str] = None
    email_body: Optional[str] = None
    email_subject: Optional[str] = None
    status: Optional[str] = None


def _note_to_dict(note):
    return {
        "id": note.id,
        "owner_id": note.owner_id,
        "owner_name": note.owner.name,
        "status": note.status,
        "notes_text": note.notes_text,
        "email_body": note.email_body,
        "email_subject": note.email_subject,
        "report_date": note.report_date,
        "sent_at": note.sent_at.isoformat() if note.sent_at else None,
        "updated_at": note.updated_at.isoformat(),
    }


@router.get("/owner-notes", response=list[OwnerReportNoteSchema])
def list_owner_notes(
    request,
    owner_id: Optional[int] = None,
    report_date: Optional[date] = None,
    status: Optional[str] = None,
):
    qs = OwnerReportNote.objects.select_related("owner").all()
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if report_date:
        qs = qs.filter(report_date=report_date)
    if status:
        qs = qs.filter(status=status)
    return [_note_to_dict(n) for n in qs]


@router.post("/owner-notes", response=OwnerReportNoteSchema)
def create_owner_note(request, data: OwnerReportNoteCreateSchema):
    note, created = OwnerReportNote.objects.update_or_create(
        owner_id=data.owner_id,
        report_date=data.report_date,
        defaults={
            "notes_text": data.notes_text,
            "email_body": data.email_body,
            "email_subject": data.email_subject,
            "status": data.status,
        },
    )
    note.refresh_from_db()
    note.owner  # force load
    return _note_to_dict(
        OwnerReportNote.objects.select_related("owner").get(pk=note.pk)
    )


@router.put("/owner-notes/{note_id}", response=OwnerReportNoteSchema)
def update_owner_note(request, note_id: int, data: OwnerReportNoteUpdateSchema):
    note = get_object_or_404(
        OwnerReportNote.objects.select_related("owner"), pk=note_id
    )
    if data.notes_text is not None:
        note.notes_text = data.notes_text
    if data.email_body is not None:
        note.email_body = data.email_body
    if data.email_subject is not None:
        note.email_subject = data.email_subject
    if data.status is not None:
        note.status = data.status
    note.save()
    return _note_to_dict(note)


@router.post("/owner-notes/{note_id}/send", response=OwnerReportNoteSchema)
def send_owner_note(request, note_id: int):
    """Mock email send — marks the note as sent."""
    note = get_object_or_404(
        OwnerReportNote.objects.select_related("owner"), pk=note_id
    )
    note.status = "sent"
    note.sent_at = timezone.now()
    note.save(update_fields=["status", "sent_at"])
    return _note_to_dict(note)

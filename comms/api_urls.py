"""URL patterns for the monthly owner notes API (sub-endpoints)."""

from django.urls import path

from . import api

urlpatterns = [
    path("generate", api.generate_notes, name="notes-generate"),
    path("send-all", api.send_all, name="notes-send-all"),
    path("approve-all", api.approve_all, name="notes-approve-all"),
    path("delete-all", api.delete_all, name="notes-delete-all"),
    path("<int:draft_id>", api.note_detail, name="notes-detail"),
    path("<int:draft_id>/approve", api.approve_note, name="notes-approve"),
    path("<int:draft_id>/send", api.send_note, name="notes-send"),
]

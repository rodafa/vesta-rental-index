"""URL patterns for the maintenance-notes API."""

from django.urls import path

from . import maintenance_api

urlpatterns = [
    path("generate", maintenance_api.generate_maintenance_notes_api, name="maintenance-notes-generate"),
    path("progress", maintenance_api.maintenance_generation_progress, name="maintenance-notes-progress"),
    path("approve-all", maintenance_api.approve_all_maintenance_notes, name="maintenance-notes-approve-all"),
    path("delete-all", maintenance_api.delete_all_maintenance_notes, name="maintenance-notes-delete-all"),
    path("<int:note_id>", maintenance_api.maintenance_note_detail, name="maintenance-notes-detail"),
    path("<int:note_id>/approve", maintenance_api.approve_maintenance_note, name="maintenance-notes-approve"),
]

from django.urls import path

from leasing import dashboard_views

urlpatterns = [
    path(
        "",
        dashboard_views.leasing_notes_list,
        name="leasing-notes-list",
    ),
    path(
        "<int:note_id>/",
        dashboard_views.leasing_note_detail_or_edit,
        name="leasing-notes-detail",
    ),
]

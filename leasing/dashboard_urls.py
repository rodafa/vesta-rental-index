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
    path(
        "<int:note_id>/approve/",
        dashboard_views.leasing_note_approve,
        name="leasing-notes-approve",
    ),
    path(
        "assemble/",
        dashboard_views.leasing_notes_assemble,
        name="leasing-notes-assemble",
    ),
    path(
        "drafts/",
        dashboard_views.leasing_drafts_list,
        name="leasing-drafts-list",
    ),
    path(
        "drafts/<int:draft_id>/test-send/",
        dashboard_views.leasing_draft_test_send,
        name="leasing-draft-test-send",
    ),
    path(
        "drafts/send/",
        dashboard_views.leasing_drafts_send,
        name="leasing-drafts-send",
    ),
]

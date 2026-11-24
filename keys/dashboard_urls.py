from django.urls import path

from keys.dashboard_views import (
    action_assign,
    action_create_tag,
    action_edit_ring_notes,
    action_found,
    action_hide_note,
    action_mark_missing,
    action_retire,
    action_set_code,
    key_dashboard,
    search_units,
)

urlpatterns = [
    path("", key_dashboard, name="key-dashboard"),
    path("set-code/", action_set_code, name="key-set-code"),
    path("assign/", action_assign, name="key-assign"),
    path("retire/", action_retire, name="key-retire"),
    path("mark-missing/", action_mark_missing, name="key-mark-missing"),
    path("found/", action_found, name="key-found"),
    path("create-tag/", action_create_tag, name="key-create-tag"),
    path("hide-note/", action_hide_note, name="key-hide-note"),
    path("edit-ring-notes/", action_edit_ring_notes, name="key-edit-ring-notes"),
    path("search-units/", search_units, name="key-search-units"),
]

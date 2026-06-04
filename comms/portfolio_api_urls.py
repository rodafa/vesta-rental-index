"""URL patterns for the portfolio-grain notes API."""

from django.urls import path

from . import portfolio_api

urlpatterns = [
    path("generate", portfolio_api.generate_portfolio_notes_api, name="portfolio-notes-generate"),
    path("approve-all", portfolio_api.approve_all_portfolio_notes, name="portfolio-notes-approve-all"),
    path("delete-all", portfolio_api.delete_all_portfolio_notes, name="portfolio-notes-delete-all"),
    path("<int:note_id>", portfolio_api.portfolio_note_detail, name="portfolio-notes-detail"),
    path("<int:note_id>/approve", portfolio_api.approve_portfolio_note, name="portfolio-notes-approve"),
]

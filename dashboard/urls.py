from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.daily_pulse, name="daily_pulse"),
    path("property/<int:unit_id>/", views.property_detail, name="property_detail"),
    path("portfolio/", views.portfolio_analytics, name="portfolio_analytics"),
    path("renewals/month/<str:month>/", views.renewal_month_detail, name="renewal_month_detail"),
    path("renewals/", views.renewal_pipeline, name="renewal_pipeline"),
    path("owner-reports/", views.owner_reports, name="owner_reports"),
    path("leasing/", views.leasing_pipeline, name="leasing_pipeline"),
    path("leasing-intelligence/", views.leasing_intelligence, name="leasing_intelligence"),
    path("maintenance-emails/", views.maintenance_emails, name="maintenance_emails"),
    path("owner-notes/", views.owner_notes, name="owner_notes"),
    path("monthly-notes/", views.monthly_notes, name="monthly_notes"),
    path("weekly-reports/", views.weekly_reports, name="weekly_reports"),
    path("weekly-reports/<int:unit_id>/", views.weekly_report_detail, name="weekly_report_detail"),
    # Redirect old bookmark
    path("weekly-sheets/", RedirectView.as_view(pattern_name="dashboard:weekly_reports", permanent=True), name="weekly_sheets"),
]

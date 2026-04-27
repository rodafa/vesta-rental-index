from django.urls import path

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
    path("maintenance-emails/", views.maintenance_emails, name="maintenance_emails"),
    path("owner-notes/", views.owner_notes, name="owner_notes"),
]

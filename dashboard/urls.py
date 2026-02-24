from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.daily_pulse, name="daily_pulse"),
    path("property/<int:unit_id>/", views.property_detail, name="property_detail"),
    path("portfolio/", views.portfolio_analytics, name="portfolio_analytics"),
    path("owner-reports/", views.owner_reports, name="owner_reports"),
]

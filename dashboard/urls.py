from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.daily_pulse, name="daily_pulse"),
    path("property/<int:unit_id>/", views.property_detail, name="property_detail"),
    path("portfolio/", views.portfolio_analytics, name="portfolio_analytics"),
    path("revenue/", views.revenue_intelligence, name="revenue_intelligence"),
    path("renewals/", views.renewal_pipeline, name="renewal_pipeline"),
    path("owner-reports/", views.owner_reports, name="owner_reports"),
]

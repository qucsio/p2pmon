from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("volumes/", views.volumes, name="volumes"),
    path("net-profit/", views.net_profit, name="net_profit"),
    path("reports/daily/", views.daily_report, name="daily_report"),
    path("reports/weekly/", views.weekly_report, name="weekly_report"),
    path("reports/monthly/", views.monthly_report, name="monthly_report"),
]

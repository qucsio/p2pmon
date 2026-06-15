from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("net-profit/", views.net_profit, name="net_profit"),
    path("reports/daily/", views.daily_report, name="daily_report"),
    path("reports/weekly/", views.weekly_report, name="weekly_report"),
]

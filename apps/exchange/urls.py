from django.urls import path

from apps.exchange import views

app_name = "exchange"

urlpatterns = [
    path("", views.sync_log_list, name="sync_logs"),
    path("refresh/", views.sync_manual, name="sync_manual"),
]

from django.urls import path

from apps.reconciliation import views

app_name = "reconciliation"

urlpatterns = [
    path("", views.reconciliation_list, name="list"),
    path("add/", views.reconciliation_create, name="create"),
    path("<int:pk>/correction/<str:account_type>/", views.create_correction, name="correction"),
]

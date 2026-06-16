from django.urls import path

from apps.investors import views

app_name = "investors"

urlpatterns = [
    path("", views.investor_list, name="list"),
    path("add/", views.investor_create, name="create"),
    path("<int:pk>/", views.investor_detail, name="detail"),
    path("<int:pk>/edit/", views.investor_edit, name="edit"),
    path("<int:pk>/deposit/", views.investor_deposit, name="deposit"),
    path("<int:pk>/withdraw/", views.investor_withdraw, name="withdraw"),
    path("history/", views.contribution_history, name="history"),
    path("allocation/", views.calculate_allocation, name="allocation"),
    path("allocation/<int:pk>/settle/", views.allocation_settle, name="allocation_settle"),
]

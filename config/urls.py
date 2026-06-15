from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.reports.urls")),
    path("orders/", include("apps.orders.urls")),
    path("adjustments/", include("apps.ledger.urls")),
    path("investors/", include("apps.investors.urls")),
    path("reconciliation/", include("apps.reconciliation.urls")),
    path("sync/", include("apps.exchange.urls")),
    path("export/", include("apps.exports.urls")),
]

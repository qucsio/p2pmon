from django.contrib import admin

from apps.reconciliation.models import BalanceSnapshot


@admin.register(BalanceSnapshot)
class BalanceSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "snapshot_at", "bank_balance_fact", "exchange_balance_fact",
        "bank_diff", "exchange_diff",
    )

from django.contrib import admin

from apps.ledger.models import DailySnapshot, LedgerAdjustment, LedgerEvent, WeeklySnapshot


@admin.register(LedgerAdjustment)
class LedgerAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("type", "account", "amount_rub", "amount_usdt", "currency", "effective_at", "is_deleted")
    list_filter = ("type", "account", "is_deleted")


@admin.register(LedgerEvent)
class LedgerEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "occurred_at_moscow", "amount_rub", "amount_usdt")
    list_filter = ("event_type",)


@admin.register(DailySnapshot)
class DailySnapshotAdmin(admin.ModelAdmin):
    list_display = ("day", "total_equity", "daily_total_equity_pnl", "daily_wac_realized_pnl")
    list_filter = ("exchange_account",)


@admin.register(WeeklySnapshot)
class WeeklySnapshotAdmin(admin.ModelAdmin):
    list_display = ("week", "total_equity", "daily_wac_realized_pnl")
    list_filter = ("exchange_account",)

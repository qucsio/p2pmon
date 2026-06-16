from django.contrib import admin

from apps.orders.models import IgnoredOrderRule, P2POrder, RawP2POrder


@admin.register(RawP2POrder)
class RawP2POrderAdmin(admin.ModelAdmin):
    list_display = ("bybit_order_id", "exchange_account", "fetched_at", "detail_fetched_at")
    search_fields = ("bybit_order_id",)
    readonly_fields = ("raw_list_payload", "raw_detail_payload")


@admin.register(P2POrder)
class P2POrderAdmin(admin.ModelAdmin):
    list_display = (
        "bybit_order_id", "side", "amount_rub", "quantity_net",
        "include_in_ledger", "show_in_orders", "created_at_moscow",
    )
    list_filter = ("side", "include_in_ledger", "show_in_orders")
    search_fields = ("bybit_order_id", "counterparty_name")
    actions = ["mark_ignored"]

    @admin.action(description="Mark selected as ignored (exclude from ledger)")
    def mark_ignored(self, request, queryset):
        queryset.update(
            include_in_ledger=False,
            show_in_orders=False,
            show_in_export=False,
            ignore_reason="Admin bulk ignore",
        )


@admin.register(IgnoredOrderRule)
class IgnoredOrderRuleAdmin(admin.ModelAdmin):
    list_display = (
        "bybit_order_id",
        "exchange_account",
        "applied_at",
        "include_in_ledger",
        "show_in_orders",
        "show_in_export",
    )
    list_filter = ("exchange_account", "applied_at")
    search_fields = ("bybit_order_id", "reason")

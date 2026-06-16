from django.contrib import admin

from apps.exchange.forms import ExchangeAccountAdminForm
from apps.exchange.models import ExchangeAccount, FeeRule, SyncLog


@admin.register(ExchangeAccount)
class ExchangeAccountAdmin(admin.ModelAdmin):
    form = ExchangeAccountAdminForm
    list_display = ("name", "exchange", "user", "is_active", "last_successful_sync_at", "ledger_start_at")
    list_filter = ("is_active", "exchange")
    readonly_fields = ("last_successful_sync_at", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        api_key = form.cleaned_data.get("api_key_plain")
        api_secret = form.cleaned_data.get("api_secret_plain")
        if api_key and api_secret:
            obj.set_api_credentials(api_key, api_secret)
        elif api_key and change:
            obj.set_api_credentials(api_key, obj.get_api_secret())
        elif api_secret and change:
            obj.set_api_credentials(obj.get_api_key(), api_secret)
        super().save_model(request, obj, form, change)


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "id", "exchange_account", "status", "mode", "started_at",
        "orders_fetched", "orders_created", "orders_updated", "errors_count",
    )
    list_filter = ("status", "mode")
    readonly_fields = (
        "started_at", "finished_at", "orders_fetched", "orders_created",
        "orders_updated", "details_fetched", "raw_error",
    )


@admin.register(FeeRule)
class FeeRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "exchange_account", "side", "fee_rate", "fee_currency", "is_active")
    list_filter = ("is_active", "side")

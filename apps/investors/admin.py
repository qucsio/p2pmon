from django.contrib import admin

from apps.investors.models import Investor, ProfitAllocation, TaxSetting


@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ("name", "share_percent", "is_active", "user")
    list_filter = ("is_active",)


@admin.register(TaxSetting)
class TaxSettingAdmin(admin.ModelAdmin):
    list_display = ("name", "tax_rate", "effective_from", "is_active")
    list_filter = ("is_active",)


@admin.register(ProfitAllocation)
class ProfitAllocationAdmin(admin.ModelAdmin):
    list_display = ("investor", "period_from", "period_to", "net_profit", "share_percent")

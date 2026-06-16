from django.contrib import admin

from apps.investors.models import (
    Investor,
    InvestorCapitalTransaction,
    InvestorProfitRule,
    TaxSetting,
)


@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ("name", "profit_share_mode", "is_active", "user")
    list_filter = ("is_active", "profit_share_mode")


@admin.register(InvestorProfitRule)
class InvestorProfitRuleAdmin(admin.ModelAdmin):
    list_display = ("investor", "mode", "effective_from", "effective_to")
    list_filter = ("mode",)


@admin.register(InvestorCapitalTransaction)
class InvestorCapitalTransactionAdmin(admin.ModelAdmin):
    list_display = ("investor", "type", "amount_rub", "effective_at")
    list_filter = ("type",)


@admin.register(TaxSetting)
class TaxSettingAdmin(admin.ModelAdmin):
    list_display = ("name", "tax_rate", "effective_from", "is_active")
    list_filter = ("is_active",)

from django.contrib import admin

from apps.investors.models import (
    Investor,
    InvestorCapitalTransaction,
    InvestorPositionSnapshot,
    ProfitAllocation,
    TaxSetting,
)


@admin.register(InvestorPositionSnapshot)
class InvestorPositionSnapshotAdmin(admin.ModelAdmin):
    list_display = ("investor", "period_from", "period_to", "closing_units", "capital_share_pct", "earned_profit_rub")


@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ("name", "profit_share_mode", "profit_share_multiplier", "is_active", "user")
    list_filter = ("is_active", "profit_share_mode")


@admin.register(InvestorCapitalTransaction)
class InvestorCapitalTransactionAdmin(admin.ModelAdmin):
    list_display = ("investor", "type", "amount_rub", "units_delta", "unit_price", "effective_at")
    list_filter = ("type",)


@admin.register(TaxSetting)
class TaxSettingAdmin(admin.ModelAdmin):
    list_display = ("name", "tax_rate", "effective_from", "is_active")
    list_filter = ("is_active",)


@admin.register(ProfitAllocation)
class ProfitAllocationAdmin(admin.ModelAdmin):
    list_display = ("investor", "period_from", "period_to", "net_profit", "profit_share_pct", "status")
    list_filter = ("status",)

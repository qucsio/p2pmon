from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.common.models import TimestampedModel

# Fund units carry more precision than money.
DECIMAL_UNITS = {"max_digits": 30, "decimal_places": 8}


class Investor(TimestampedModel):
    PROFIT_SAME_AS_CAPITAL = "same_as_capital"
    PROFIT_MULTIPLIER = "multiplier"
    PROFIT_FIXED_PCT = "fixed_pct"
    PROFIT_NONE = "none"
    PROFIT_SPLIT = "split_from_investor"
    PROFIT_MODE_CHOICES = [
        (PROFIT_SAME_AS_CAPITAL, "Как доля капитала"),
        (PROFIT_MULTIPLIER, "Множитель от доли капитала"),
        (PROFIT_FIXED_PCT, "Фиксированный процент"),
        (PROFIT_NONE, "Без прибыли"),
        (PROFIT_SPLIT, "Доля от прибыли другого инвестора"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investors",
    )
    name = models.CharField(max_length=128)
    # Deprecated: kept for backward compat / migration. Use capital units instead.
    share_percent = models.DecimalField(**settings.DECIMAL_RATE, default=0)

    profit_share_mode = models.CharField(
        max_length=20, choices=PROFIT_MODE_CHOICES, default=PROFIT_SAME_AS_CAPITAL
    )
    profit_share_multiplier = models.DecimalField(**settings.DECIMAL_RATE, default=Decimal("1"))
    profit_share_fixed_pct = models.DecimalField(
        **settings.DECIMAL_RATE, null=True, blank=True
    )
    # For split_from_investor: receive `split_percent` of source_investor's profit.
    source_investor = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="profit_recipients",
    )
    split_percent = models.DecimalField(**settings.DECIMAL_RATE, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.profit_share_mode == self.PROFIT_FIXED_PCT:
            pct = self.profit_share_fixed_pct
            if pct is None:
                raise ValidationError("Укажите фиксированный процент прибыли.")
            if pct < 0 or pct > Decimal("100"):
                raise ValidationError("Фиксированный процент прибыли должен быть в пределах 0–100%.")
        if self.profit_share_mode == self.PROFIT_MULTIPLIER and self.profit_share_multiplier < 0:
            raise ValidationError("Множитель прибыли не может быть отрицательным.")
        if self.profit_share_mode == self.PROFIT_SPLIT:
            if not self.source_investor_id:
                raise ValidationError("Для режима «доля от прибыли» укажите источник.")
            if self.split_percent is None or self.split_percent < 0 or self.split_percent > Decimal("100"):
                raise ValidationError("Процент доли должен быть в пределах 0–100%.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    # --- Capital (unit-based) ---
    @property
    def units(self) -> Decimal:
        # Units come ONLY from real capital events (deposit/withdrawal/correction).
        capital_types = [
            InvestorCapitalTransaction.TYPE_DEPOSIT,
            InvestorCapitalTransaction.TYPE_WITHDRAWAL,
            InvestorCapitalTransaction.TYPE_CORRECTION,
        ]
        return (
            self.capital_transactions.filter(type__in=capital_types)
            .aggregate(t=Sum("units_delta"))["t"]
            or Decimal("0")
        )

    def earned_profit_total(self) -> Decimal:
        return self.allocations.aggregate(t=Sum("net_profit"))["t"] or Decimal("0")

    def settled_total(self, status) -> Decimal:
        return (
            self.allocations.filter(status=status).aggregate(t=Sum("net_profit"))["t"]
            or Decimal("0")
        )

    def unpaid_total(self) -> Decimal:
        return self.settled_total(ProfitAllocation.STATUS_UNPAID_CLAIM)

    def retained_total(self) -> Decimal:
        return self.settled_total(ProfitAllocation.STATUS_RETAINED)

    @property
    def profit_is_claim(self) -> bool:
        """True when this participant's profit is a payable claim (not in NAV)."""
        return self.profit_share_mode in (self.PROFIT_SPLIT, self.PROFIT_FIXED_PCT)


class InvestorCapitalTransaction(TimestampedModel):
    TYPE_DEPOSIT = "deposit"
    TYPE_WITHDRAWAL = "withdrawal"
    TYPE_PROFIT_REINVEST = "profit_reinvest"
    TYPE_PROFIT_PAYOUT = "profit_payout"
    TYPE_CORRECTION = "correction"
    TYPE_CHOICES = [
        (TYPE_DEPOSIT, "Депозит"),
        (TYPE_WITHDRAWAL, "Вывод"),
        (TYPE_PROFIT_REINVEST, "Реинвест прибыли"),
        (TYPE_PROFIT_PAYOUT, "Выплата прибыли"),
        (TYPE_CORRECTION, "Корректировка"),
    ]

    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, related_name="capital_transactions"
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    units_delta = models.DecimalField(**DECIMAL_UNITS, default=0)
    unit_price = models.DecimalField(**DECIMAL_UNITS, default=0)
    effective_at = models.DateTimeField()
    linked_ledger_adjustment = models.ForeignKey(
        "ledger.LedgerAdjustment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investor_transactions",
    )
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["effective_at", "id"]

    def __str__(self):
        return f"{self.investor.name} {self.type} {self.amount_rub}₽ ({self.units_delta} u)"


def active_share_total(user, exclude_pk=None) -> Decimal:
    qs = Investor.objects.filter(user=user, is_active=True)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.aggregate(total=Sum("share_percent"))["total"] or Decimal("0")


def shares_fully_allocated(user) -> bool:
    return active_share_total(user) == Decimal("100")


class TaxSetting(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tax_settings",
    )
    name = models.CharField(max_length=128)
    tax_rate = models.DecimalField(**settings.DECIMAL_RATE)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        return f"{self.name} ({self.tax_rate * 100}%)"


class ProfitAllocation(models.Model):
    # A capital investor's own share is already in NAV → retained, not payable.
    STATUS_RETAINED = "retained_in_capital"
    # Profit owed to a non-capital participant (split/fixed) → a real claim.
    STATUS_UNPAID_CLAIM = "unpaid_claim"
    STATUS_PAID_OUT = "paid_out"
    STATUS_REINVESTED = "reinvested"
    # Legacy value kept only so old rows load; no longer assigned.
    STATUS_UNPAID = "unpaid"
    STATUS_CHOICES = [
        (STATUS_RETAINED, "В капитале (NAV)"),
        (STATUS_UNPAID_CLAIM, "Невыплаченное требование"),
        (STATUS_PAID_OUT, "Выплачено"),
        (STATUS_REINVESTED, "Реинвестировано"),
        (STATUS_UNPAID, "Не выплачено (устар.)"),
    ]
    # Statuses that represent a payable claim (settle-able).
    CLAIM_STATUSES = (STATUS_UNPAID_CLAIM,)

    period_from = models.DateField()
    period_to = models.DateField()
    investor = models.ForeignKey(
        Investor,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    # share_percent kept as the effective profit share (legacy column name).
    share_percent = models.DecimalField(**settings.DECIMAL_RATE, default=0)
    capital_share_pct = models.DecimalField(**settings.DECIMAL_RATE, default=0)
    profit_share_pct = models.DecimalField(**settings.DECIMAL_RATE, default=0)
    gross_profit = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    fees_part = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    tax_part = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RETAINED)
    settled_at = models.DateTimeField(null=True, blank=True)
    settlement_txn = models.ForeignKey(
        InvestorCapitalTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settled_allocation",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_from", "investor__name"]

    def __str__(self):
        return f"{self.investor.name} {self.period_from} - {self.period_to}"


class InvestorPositionSnapshot(models.Model):
    """Frozen, auditable per-period position + profit for one investor."""
    period_from = models.DateField()
    period_to = models.DateField()
    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, related_name="position_snapshots"
    )
    opening_units = models.DecimalField(**DECIMAL_UNITS, default=0)
    closing_units = models.DecimalField(**DECIMAL_UNITS, default=0)
    unit_price = models.DecimalField(**DECIMAL_UNITS, default=0)
    capital_value_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    capital_share_pct = models.DecimalField(**settings.DECIMAL_RATE, default=0)
    profit_share_pct = models.DecimalField(**settings.DECIMAL_RATE, default=0)
    earned_profit_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    cumulative_earned_profit_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    paid_out_profit_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    reinvested_profit_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    unpaid_profit_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_to", "investor__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["investor", "period_from", "period_to"],
                name="unique_position_snapshot_period",
            )
        ]

    def __str__(self):
        return f"{self.investor.name} {self.period_from}–{self.period_to}"

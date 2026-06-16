from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.common.models import TimestampedModel

# Fund units carry more precision than money.
DECIMAL_UNITS = {"max_digits": 30, "decimal_places": 8}

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


def _validate_profit_fields(mode, multiplier, fixed_pct, source_id, split_percent):
    if mode == PROFIT_FIXED_PCT:
        if fixed_pct is None or fixed_pct < 0 or fixed_pct > Decimal("100"):
            raise ValidationError("Фиксированный процент прибыли должен быть в пределах 0–100%.")
    if mode == PROFIT_MULTIPLIER and (multiplier is None or multiplier < 0):
        raise ValidationError("Множитель прибыли не может быть отрицательным.")
    if mode == PROFIT_SPLIT:
        if not source_id:
            raise ValidationError("Для режима «доля от прибыли» укажите источник.")
        if split_percent is None or split_percent < 0 or split_percent > Decimal("100"):
            raise ValidationError("Процент доли должен быть в пределах 0–100%.")


class Investor(TimestampedModel):
    # Re-exported for backward compatibility.
    PROFIT_SAME_AS_CAPITAL = PROFIT_SAME_AS_CAPITAL
    PROFIT_MULTIPLIER = PROFIT_MULTIPLIER
    PROFIT_FIXED_PCT = PROFIT_FIXED_PCT
    PROFIT_NONE = PROFIT_NONE
    PROFIT_SPLIT = PROFIT_SPLIT
    PROFIT_MODE_CHOICES = PROFIT_MODE_CHOICES

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="investors")
    name = models.CharField(max_length=128)

    # Current/default profit agreement. Used as the open-ended fallback rule when
    # the investor has no dated InvestorProfitRule rows. For historically-correct
    # changes, add an InvestorProfitRule with effective_from instead.
    profit_share_mode = models.CharField(
        max_length=20, choices=PROFIT_MODE_CHOICES, default=PROFIT_SAME_AS_CAPITAL)
    profit_share_multiplier = models.DecimalField(**settings.DECIMAL_RATE, default=Decimal("1"))
    profit_share_fixed_pct = models.DecimalField(**settings.DECIMAL_RATE, null=True, blank=True)
    source_investor = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="profit_recipients")
    split_percent = models.DecimalField(**settings.DECIMAL_RATE, null=True, blank=True)
    residual_investor = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="residual_recipients")

    is_active = models.BooleanField(default=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        _validate_profit_fields(
            self.profit_share_mode, self.profit_share_multiplier,
            self.profit_share_fixed_pct, self.source_investor_id, self.split_percent)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def units(self) -> Decimal:
        # Units come ONLY from real capital events (deposit/withdrawal/correction).
        return (
            self.capital_transactions.filter(type__in=[
                InvestorCapitalTransaction.TYPE_DEPOSIT,
                InvestorCapitalTransaction.TYPE_WITHDRAWAL,
                InvestorCapitalTransaction.TYPE_CORRECTION,
            ]).aggregate(t=Sum("units_delta"))["t"]
            or Decimal("0")
        )

    # Lifetime profit & economic capital are computed live in
    # services.investor_report() — never from stored allocation rows.


class InvestorProfitRule(TimestampedModel):
    """Versioned (effective-dated) profit agreement. The applicable rule for a
    given day is the one whose [effective_from, effective_to] contains that day.
    Editing it does NOT change units; it only affects profit attribution from its
    effective date forward, so history is not rewritten retroactively."""

    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, related_name="profit_rules")
    mode = models.CharField(max_length=20, choices=PROFIT_MODE_CHOICES,
                            default=PROFIT_SAME_AS_CAPITAL)
    profit_share_multiplier = models.DecimalField(**settings.DECIMAL_RATE, default=Decimal("1"))
    profit_share_fixed_pct = models.DecimalField(**settings.DECIMAL_RATE, null=True, blank=True)
    source_investor = models.ForeignKey(
        Investor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rule_profit_sources")
    split_percent = models.DecimalField(**settings.DECIMAL_RATE, null=True, blank=True)
    residual_investor = models.ForeignKey(
        Investor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rule_residual_targets")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["investor", "effective_from"]

    def __str__(self):
        return f"{self.investor.name}: {self.mode} from {self.effective_from}"

    def clean(self):
        _validate_profit_fields(
            self.mode, self.profit_share_multiplier, self.profit_share_fixed_pct,
            self.source_investor_id, self.split_percent)
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Дата окончания раньше даты начала.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InvestorCapitalTransaction(TimestampedModel):
    TYPE_DEPOSIT = "deposit"
    TYPE_WITHDRAWAL = "withdrawal"
    TYPE_CORRECTION = "correction"
    # Legacy strings kept ONLY so historical rows still load. Never created now;
    # they carry no units and are ignored by every report.
    TYPE_PROFIT_REINVEST = "profit_reinvest"
    TYPE_PROFIT_PAYOUT = "profit_payout"
    TYPE_CHOICES = [
        (TYPE_DEPOSIT, "Депозит"),
        (TYPE_WITHDRAWAL, "Вывод"),
        (TYPE_CORRECTION, "Корректировка"),
    ]

    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, related_name="capital_transactions")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    units_delta = models.DecimalField(**DECIMAL_UNITS, default=0)
    unit_price = models.DecimalField(**DECIMAL_UNITS, default=0)
    effective_at = models.DateTimeField()
    linked_ledger_adjustment = models.ForeignKey(
        "ledger.LedgerAdjustment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="investor_transactions")
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["effective_at", "id"]

    def __str__(self):
        return f"{self.investor.name} {self.type} {self.amount_rub}₽ ({self.units_delta} u)"


class TaxSetting(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tax_settings")
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

from django.conf import settings
from django.db import models

from apps.common.models import TimestampedModel
from apps.exchange.models import ExchangeAccount


class LedgerAdjustment(TimestampedModel):
    ACCOUNT_BANK = "bank"
    ACCOUNT_EXCHANGE = "exchange"
    ACCOUNT_CHOICES = [
        (ACCOUNT_BANK, "Bank"),
        (ACCOUNT_EXCHANGE, "Exchange"),
    ]

    TYPE_DEPOSIT = "deposit"
    TYPE_WITHDRAWAL = "withdrawal"
    TYPE_CORRECTION = "correction"
    TYPE_TAX_PAYMENT = "tax_payment"
    TYPE_FEE_CORRECTION = "fee_correction"
    TYPE_INVESTOR_DEPOSIT = "investor_deposit"
    TYPE_INVESTOR_WITHDRAWAL = "investor_withdrawal"
    TYPE_CHOICES = [
        (TYPE_DEPOSIT, "Deposit"),
        (TYPE_WITHDRAWAL, "Withdrawal"),
        (TYPE_CORRECTION, "Correction"),
        (TYPE_TAX_PAYMENT, "Tax Payment"),
        (TYPE_FEE_CORRECTION, "Fee Correction"),
        (TYPE_INVESTOR_DEPOSIT, "Investor Deposit"),
        (TYPE_INVESTOR_WITHDRAWAL, "Investor Withdrawal"),
    ]

    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="adjustments",
    )
    account = models.CharField(max_length=16, choices=ACCOUNT_CHOICES)
    type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    currency = models.CharField(max_length=16)
    amount = models.DecimalField(**settings.DECIMAL_RUB)
    effective_at = models.DateTimeField()
    comment = models.TextField(blank=True)
    include_in_ledger = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjustments",
    )
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_adjustments",
    )

    class Meta:
        ordering = ["effective_at", "id"]

    def __str__(self):
        return f"{self.type} {self.amount} {self.currency} @ {self.effective_at:%Y-%m-%d}"


class LedgerEvent(models.Model):
    EVENT_BUY = "buy"
    EVENT_SELL = "sell"
    EVENT_BANK_DEPOSIT = "bank_deposit"
    EVENT_BANK_WITHDRAWAL = "bank_withdrawal"
    EVENT_BANK_CORRECTION = "bank_correction"
    EVENT_EXCHANGE_DEPOSIT = "exchange_deposit"
    EVENT_EXCHANGE_WITHDRAWAL = "exchange_withdrawal"
    EVENT_EXCHANGE_CORRECTION = "exchange_correction"
    EVENT_FEE = "fee"
    EVENT_TAX_PAYMENT = "tax_payment"
    EVENT_INVESTOR_DEPOSIT = "investor_deposit"
    EVENT_INVESTOR_WITHDRAWAL = "investor_withdrawal"
    EVENT_CHOICES = [
        (EVENT_BUY, "Buy"),
        (EVENT_SELL, "Sell"),
        (EVENT_BANK_DEPOSIT, "Bank Deposit"),
        (EVENT_BANK_WITHDRAWAL, "Bank Withdrawal"),
        (EVENT_BANK_CORRECTION, "Bank Correction"),
        (EVENT_EXCHANGE_DEPOSIT, "Exchange Deposit"),
        (EVENT_EXCHANGE_WITHDRAWAL, "Exchange Withdrawal"),
        (EVENT_EXCHANGE_CORRECTION, "Exchange Correction"),
        (EVENT_FEE, "Fee"),
        (EVENT_TAX_PAYMENT, "Tax Payment"),
        (EVENT_INVESTOR_DEPOSIT, "Investor Deposit"),
        (EVENT_INVESTOR_WITHDRAWAL, "Investor Withdrawal"),
    ]

    SOURCE_ORDER = "order"
    SOURCE_ADJUSTMENT = "adjustment"
    SOURCE_CHOICES = [
        (SOURCE_ORDER, "Order"),
        (SOURCE_ADJUSTMENT, "Adjustment"),
    ]

    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="ledger_events",
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    source_id = models.BigIntegerField()
    occurred_at_utc = models.DateTimeField()
    occurred_at_moscow = models.DateTimeField()
    currency = models.CharField(max_length=16, blank=True)
    amount_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    amount_usdt = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    price = models.DecimalField(**settings.DECIMAL_PRICE, default=0)
    fee_amount = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    fee_currency = models.CharField(max_length=16, blank=True)
    include_in_ledger = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at_moscow", "event_type", "source_id"]
        indexes = [
            models.Index(fields=["exchange_account", "occurred_at_moscow"]),
        ]


class DailySnapshot(TimestampedModel):
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="daily_snapshots",
    )
    day = models.DateField()

    bank_balance = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    exchange_balance = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    total_equity = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    daily_total_equity_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    daily_wac_realized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    daily_wac_unrealized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    running_wac_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    gross_realized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    fees = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit_before_tax = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    tax_accrual = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit_after_tax = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    volume_usdt = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    volume_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    last_price = models.DecimalField(**settings.DECIMAL_PRICE, default=0)
    wac_price = models.DecimalField(**settings.DECIMAL_PRICE, default=0)
    wac_qty = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    wac_cost = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_account", "day"],
                name="unique_daily_snapshot",
            )
        ]
        ordering = ["day"]


class WeeklySnapshot(TimestampedModel):
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="weekly_snapshots",
    )
    week = models.CharField(max_length=16)

    bank_balance = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    exchange_balance = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    total_equity = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    daily_total_equity_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    daily_wac_realized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    daily_wac_unrealized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    running_wac_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    gross_realized_pnl = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    fees = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit_before_tax = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    tax_accrual = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit_after_tax = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    volume_usdt = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    volume_rub = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    last_price = models.DecimalField(**settings.DECIMAL_PRICE, default=0)
    wac_price = models.DecimalField(**settings.DECIMAL_PRICE, default=0)
    wac_qty = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    wac_cost = models.DecimalField(**settings.DECIMAL_RUB, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_account", "week"],
                name="unique_weekly_snapshot",
            )
        ]
        ordering = ["week"]

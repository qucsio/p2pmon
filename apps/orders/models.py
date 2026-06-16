from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common.models import TimestampedModel
from apps.exchange.models import ExchangeAccount


class RawP2POrder(TimestampedModel):
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="raw_orders",
    )
    bybit_order_id = models.CharField(max_length=64)
    raw_list_payload = models.JSONField(default=dict)
    raw_detail_payload = models.JSONField(default=dict, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_account", "bybit_order_id"],
                name="unique_raw_order_per_account",
            )
        ]
        ordering = ["-fetched_at"]

    def __str__(self):
        return self.bybit_order_id


class P2POrder(TimestampedModel):
    SIDE_BUY = "BUY"
    SIDE_SELL = "SELL"
    SIDE_CHOICES = [(SIDE_BUY, "Buy"), (SIDE_SELL, "Sell")]

    FEE_SOURCE_API = "api"
    FEE_SOURCE_CALCULATED = "calculated"
    FEE_SOURCE_MANUAL = "manual"
    FEE_SOURCE_NONE = "none"
    FEE_SOURCE_CHOICES = [
        (FEE_SOURCE_API, "API"),
        (FEE_SOURCE_CALCULATED, "Calculated"),
        (FEE_SOURCE_MANUAL, "Manual"),
        (FEE_SOURCE_NONE, "None"),
    ]

    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="p2p_orders",
    )
    raw_order = models.OneToOneField(
        RawP2POrder,
        on_delete=models.CASCADE,
        related_name="normalized_order",
    )
    bybit_order_id = models.CharField(max_length=64)

    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    bybit_side = models.IntegerField()
    order_type = models.CharField(max_length=32, blank=True)
    status = models.IntegerField()

    token_id = models.CharField(max_length=16, default="USDT")
    currency_id = models.CharField(max_length=16, default="RUB")

    price = models.DecimalField(**settings.DECIMAL_PRICE)
    quantity_gross = models.DecimalField(**settings.DECIMAL_USDT)
    quantity_net = models.DecimalField(**settings.DECIMAL_USDT, default=Decimal("0"))
    amount_gross = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    amount_net = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    amount_rub = models.DecimalField(**settings.DECIMAL_RUB)

    fee_amount = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    fee_currency = models.CharField(max_length=16, blank=True)
    maker_fee = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    taker_fee = models.DecimalField(**settings.DECIMAL_USDT, default=0)
    fee_source = models.CharField(
        max_length=16,
        choices=FEE_SOURCE_CHOICES,
        default=FEE_SOURCE_NONE,
    )

    manual_fee_amount = models.DecimalField(**settings.DECIMAL_USDT, null=True, blank=True)
    manual_fee_currency = models.CharField(max_length=16, blank=True)
    manual_fee_comment = models.TextField(blank=True)
    manual_fee_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fee_overrides",
    )
    manual_fee_updated_at = models.DateTimeField(null=True, blank=True)

    counterparty_name = models.CharField(max_length=256, blank=True)
    counterparty_nickname = models.CharField(max_length=128, blank=True)
    counterparty_user_id = models.CharField(max_length=64, blank=True)

    created_at_bybit_raw_ms = models.BigIntegerField()
    created_at_utc = models.DateTimeField()
    created_at_moscow = models.DateTimeField()
    completed_at_bybit_utc = models.DateTimeField(null=True, blank=True)
    completed_at_moscow = models.DateTimeField(null=True, blank=True)

    include_in_ledger = models.BooleanField(default=True)
    show_in_orders = models.BooleanField(default=True)
    show_in_export = models.BooleanField(default=True)

    ignore_reason = models.TextField(blank=True)
    ignored_at = models.DateTimeField(null=True, blank=True)
    ignored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ignored_orders",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_account", "bybit_order_id"],
                name="unique_p2p_order_per_account",
            )
        ]
        ordering = ["-created_at_moscow"]
        indexes = [
            models.Index(fields=["created_at_moscow"]),
            models.Index(fields=["side"]),
            models.Index(fields=["include_in_ledger"]),
        ]

    def __str__(self):
        return f"{self.bybit_order_id} {self.side}"

    @property
    def is_ignored(self):
        return not self.include_in_ledger and not self.show_in_orders


class IgnoredOrderRule(TimestampedModel):
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="ignored_order_rules",
    )
    bybit_order_id = models.CharField(max_length=64)
    reason = models.TextField()
    include_in_ledger = models.BooleanField(default=False)
    show_in_orders = models.BooleanField(default=False)
    show_in_export = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange_account", "bybit_order_id"],
                name="unique_ignored_order_rule_per_account",
            )
        ]
        ordering = ["bybit_order_id"]

    def __str__(self):
        return f"{self.bybit_order_id} ({self.exchange_account_id})"

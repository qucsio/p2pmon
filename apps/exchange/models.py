from django.conf import settings
from django.db import models

from apps.common.encryption import decrypt_value, encrypt_value
from apps.common.models import TimestampedModel


class ExchangeAccount(TimestampedModel):
    EXCHANGE_BYBIT = "bybit"
    EXCHANGE_CHOICES = [(EXCHANGE_BYBIT, "Bybit")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exchange_accounts",
    )
    name = models.CharField(max_length=128)
    exchange = models.CharField(max_length=32, choices=EXCHANGE_CHOICES, default=EXCHANGE_BYBIT)
    api_key_encrypted = models.BinaryField(blank=True, default=b"")
    api_secret_encrypted = models.BinaryField(blank=True, default=b"")
    is_active = models.BooleanField(default=True)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    ledger_start_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Orders before this Moscow-time boundary are excluded from ledger/UI/export.",
    )
    ledger_start_inclusive = models.BooleanField(
        default=False,
        help_text="If True, orders at ledger_start_at are also ignored (<= boundary).",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.exchange})"

    def set_api_credentials(self, api_key: str, api_secret: str):
        self.api_key_encrypted = encrypt_value(api_key)
        self.api_secret_encrypted = encrypt_value(api_secret)

    def get_api_key(self) -> str:
        return decrypt_value(self.api_key_encrypted)

    def get_api_secret(self) -> str:
        return decrypt_value(self.api_secret_encrypted)


class SyncLog(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_PARTIAL = "partial"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    MODE_HOURLY = "hourly"
    MODE_MANUAL = "manual"
    MODE_BACKFILL = "backfill"
    MODE_PERIOD_RESYNC = "period_resync"
    MODE_CHOICES = [
        (MODE_HOURLY, "Hourly"),
        (MODE_MANUAL, "Manual"),
        (MODE_BACKFILL, "Backfill"),
        (MODE_PERIOD_RESYNC, "Period Resync"),
    ]

    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="sync_logs",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES)
    period_from = models.DateTimeField(null=True, blank=True)
    period_to = models.DateTimeField(null=True, blank=True)
    orders_fetched = models.PositiveIntegerField(default=0)
    orders_created = models.PositiveIntegerField(default=0)
    orders_updated = models.PositiveIntegerField(default=0)
    orders_ignored = models.PositiveIntegerField(default=0)
    details_fetched = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)
    warnings_count = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    raw_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_logs",
    )

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Sync {self.id} [{self.status}] {self.started_at:%Y-%m-%d %H:%M}"


class FeeRule(TimestampedModel):
    SIDE_BUY = "buy"
    SIDE_SELL = "sell"
    SIDE_BOTH = "both"
    SIDE_CHOICES = [
        (SIDE_BUY, "Buy"),
        (SIDE_SELL, "Sell"),
        (SIDE_BOTH, "Both"),
    ]

    ROLE_MAKER = "maker"
    ROLE_TAKER = "taker"
    ROLE_BOTH = "both"
    ROLE_CHOICES = [
        (ROLE_MAKER, "Maker"),
        (ROLE_TAKER, "Taker"),
        (ROLE_BOTH, "Both"),
    ]

    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="fee_rules",
    )
    name = models.CharField(max_length=128)
    side = models.CharField(max_length=8, choices=SIDE_CHOICES, default=SIDE_BOTH)
    ad_type = models.CharField(max_length=32, blank=True)
    role = models.CharField(max_length=8, choices=ROLE_CHOICES, default=ROLE_MAKER)
    fee_rate = models.DecimalField(**settings.DECIMAL_RATE)
    fee_currency = models.CharField(max_length=16, default="USDT")
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        return self.name

from django.conf import settings
from django.db import models

from apps.exchange.models import ExchangeAccount


class BalanceSnapshot(models.Model):
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.CASCADE,
        related_name="balance_snapshots",
    )
    snapshot_at = models.DateTimeField()
    bank_balance_fact = models.DecimalField(**settings.DECIMAL_RUB)
    exchange_balance_fact = models.DecimalField(**settings.DECIMAL_USDT)
    bank_balance_calculated = models.DecimalField(**settings.DECIMAL_RUB)
    exchange_balance_calculated = models.DecimalField(**settings.DECIMAL_USDT)
    bank_diff = models.DecimalField(**settings.DECIMAL_RUB)
    exchange_diff = models.DecimalField(**settings.DECIMAL_USDT)
    comment = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_at"]

    def __str__(self):
        return f"Reconciliation {self.snapshot_at:%Y-%m-%d %H:%M}"

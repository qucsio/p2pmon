from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.common.models import TimestampedModel


class Investor(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investors",
    )
    name = models.CharField(max_length=128)
    share_percent = models.DecimalField(**settings.DECIMAL_RATE)
    is_active = models.BooleanField(default=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.share_percent}%)"

    def clean(self):
        if not self.is_active:
            return
        total = (
            Investor.objects.filter(user=self.user, is_active=True)
            .exclude(pk=self.pk)
            .aggregate(total=Sum("share_percent"))["total"]
            or Decimal("0")
        )
        total += self.share_percent
        if total != Decimal("100"):
            raise ValidationError(
                f"Active investor shares must sum to 100%, currently would be {total}%"
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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
    period_from = models.DateField()
    period_to = models.DateField()
    investor = models.ForeignKey(
        Investor,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    share_percent = models.DecimalField(**settings.DECIMAL_RATE)
    gross_profit = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    fees_part = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    tax_part = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    net_profit = models.DecimalField(**settings.DECIMAL_RUB, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_from", "investor__name"]

    def __str__(self):
        return f"{self.investor.name} {self.period_from} - {self.period_to}"

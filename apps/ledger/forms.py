from decimal import Decimal

from django import forms

from apps.common.decimal_utils import q_rub, q_usdt
from apps.ledger.models import LedgerAdjustment


class AdjustmentForm(forms.ModelForm):
    amount = forms.DecimalField(
        max_digits=24,
        decimal_places=8,
        label="Amount",
        help_text="RUB for bank adjustments, USDT for exchange adjustments",
    )

    class Meta:
        model = LedgerAdjustment
        fields = ("account", "type", "effective_at", "comment", "include_in_ledger")
        widgets = {
            "effective_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "comment": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["amount"].initial = self.instance.display_amount
        # Investor deposits/withdrawals are managed from the Investors tab, not here.
        investor_types = {
            LedgerAdjustment.TYPE_INVESTOR_DEPOSIT,
            LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL,
        }
        self.fields["type"].choices = [
            c for c in self.fields["type"].choices if c[0] not in investor_types
        ]

    def save(self, commit=True):
        obj = super().save(commit=False)
        amount = self.cleaned_data["amount"]
        if obj.account == LedgerAdjustment.ACCOUNT_BANK:
            obj.amount_rub = q_rub(amount)
            obj.amount_usdt = Decimal("0")
            obj.currency = "RUB"
        else:
            obj.amount_usdt = q_usdt(amount)
            obj.amount_rub = Decimal("0")
            obj.currency = "USDT"
        if commit:
            obj.save()
        return obj

from decimal import Decimal

from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.ledger.models import LedgerAdjustment
from apps.ledger.services.engine import LedgerEngine
from apps.ledger.tasks import rebuild_ledger
from apps.reconciliation.models import BalanceSnapshot


class ReconciliationForm(forms.ModelForm):
    class Meta:
        model = BalanceSnapshot
        fields = ("snapshot_at", "bank_balance_fact", "exchange_balance_fact", "comment")
        widgets = {
            "snapshot_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "comment": forms.Textarea(attrs={"rows": 2}),
        }


@login_required
def reconciliation_list(request):
    account = get_active_account(request.user)
    snapshots = []
    calculated_bank = calculated_exchange = None

    if account:
        snapshots = BalanceSnapshot.objects.filter(exchange_account=account)[:20]
        state = LedgerEngine(account).get_current_state()
        calculated_bank = state.bank
        calculated_exchange = state.exchange

    return render(request, "reconciliation/list.html", {
        "account": account,
        "snapshots": snapshots,
        "calculated_bank": calculated_bank,
        "calculated_exchange": calculated_exchange,
    })


@login_required
def reconciliation_create(request):
    account = get_active_account(request.user)
    if not account:
        return redirect("reports:dashboard")

    state = LedgerEngine(account).get_current_state()
    form = ReconciliationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        snap = form.save(commit=False)
        snap.exchange_account = account
        snap.created_by = request.user
        snap.bank_balance_calculated = state.bank
        snap.exchange_balance_calculated = state.exchange
        snap.bank_diff = snap.bank_balance_fact - state.bank
        snap.exchange_diff = snap.exchange_balance_fact - state.exchange
        snap.save()
        return redirect("reconciliation:list")

    return render(request, "reconciliation/form.html", {
        "account": account,
        "form": form,
        "calculated_bank": state.bank,
        "calculated_exchange": state.exchange,
    })


@login_required
def create_correction(request, pk, account_type):
    snap = BalanceSnapshot.objects.get(pk=pk, exchange_account__user=request.user)
    if request.method == "POST":
        if account_type == "bank" and snap.bank_diff != 0:
            LedgerAdjustment.objects.create(
                exchange_account=snap.exchange_account,
                account=LedgerAdjustment.ACCOUNT_BANK,
                type=LedgerAdjustment.TYPE_CORRECTION,
                currency="RUB",
                amount_rub=snap.bank_diff,
                amount_usdt=Decimal("0"),
                effective_at=snap.snapshot_at,
                comment=f"Reconciliation correction from snapshot {snap.id}",
                created_by=request.user,
            )
        elif account_type == "exchange" and snap.exchange_diff != 0:
            LedgerAdjustment.objects.create(
                exchange_account=snap.exchange_account,
                account=LedgerAdjustment.ACCOUNT_EXCHANGE,
                type=LedgerAdjustment.TYPE_CORRECTION,
                currency="USDT",
                amount_rub=Decimal("0"),
                amount_usdt=snap.exchange_diff,
                effective_at=snap.snapshot_at,
                comment=f"Reconciliation correction from snapshot {snap.id}",
                created_by=request.user,
            )
        rebuild_ledger.delay(snap.exchange_account_id)
        return redirect("reconciliation:list")
    return render(request, "reconciliation/correction_confirm.html", {
        "snapshot": snap,
        "account_type": account_type,
    })

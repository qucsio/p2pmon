from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.ledger.models import LedgerAdjustment
from apps.ledger.tasks import rebuild_ledger


class AdjustmentForm(forms.ModelForm):
    class Meta:
        model = LedgerAdjustment
        fields = ("account", "type", "currency", "amount", "effective_at", "comment", "include_in_ledger")
        widgets = {
            "effective_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "comment": forms.Textarea(attrs={"rows": 2}),
        }


@login_required
def adjustment_list(request):
    account = get_active_account(request.user)
    adjustments = LedgerAdjustment.objects.none()
    if account:
        adjustments = LedgerAdjustment.objects.filter(
            exchange_account=account,
            is_deleted=False,
        ).order_by("-effective_at")
    return render(request, "ledger/adjustment_list.html", {
        "account": account,
        "adjustments": adjustments,
    })


@login_required
def adjustment_create(request):
    account = get_active_account(request.user)
    if not account:
        return redirect("reports:dashboard")

    form = AdjustmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        adj = form.save(commit=False)
        adj.exchange_account = account
        adj.created_by = request.user
        if adj.account == LedgerAdjustment.ACCOUNT_BANK:
            adj.currency = "RUB"
        else:
            adj.currency = "USDT"
        adj.save()
        rebuild_ledger.delay(account.id)
        return redirect("ledger:adjustment_list")

    return render(request, "ledger/adjustment_form.html", {
        "account": account,
        "form": form,
        "title": "Add Adjustment",
    })


@login_required
def adjustment_edit(request, pk):
    account = get_active_account(request.user)
    adj = get_object_or_404(LedgerAdjustment, pk=pk, exchange_account__user=request.user, is_deleted=False)
    form = AdjustmentForm(request.POST or None, instance=adj)
    if request.method == "POST" and form.is_valid():
        form.save()
        rebuild_ledger.delay(account.id)
        return redirect("ledger:adjustment_list")
    return render(request, "ledger/adjustment_form.html", {
        "account": account,
        "form": form,
        "title": "Edit Adjustment",
    })


@login_required
def adjustment_delete(request, pk):
    adj = get_object_or_404(LedgerAdjustment, pk=pk, exchange_account__user=request.user, is_deleted=False)
    if request.method == "POST":
        adj.is_deleted = True
        adj.deleted_at = timezone.now()
        adj.deleted_by = request.user
        adj.save()
        rebuild_ledger.delay(adj.exchange_account_id)
        return redirect("ledger:adjustment_list")
    return render(request, "ledger/adjustment_delete.html", {"adjustment": adj})

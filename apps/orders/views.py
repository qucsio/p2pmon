from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.ledger.models import LedgerAdjustment
from apps.ledger.tasks import rebuild_ledger
from apps.orders.models import P2POrder


class OrderFilterForm(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    side = forms.ChoiceField(required=False, choices=[("", "All"), ("BUY", "Buy"), ("SELL", "Sell")], widget=forms.Select(attrs={"class": "form-select"}))
    status = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={"class": "form-control"}))
    show_ignored = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    search = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Order ID or counterparty"}))


class IgnoreOrderForm(forms.Form):
    ignore_reason = forms.CharField(widget=forms.Textarea, min_length=3)


@login_required
def order_list(request):
    account = get_active_account(request.user)
    form = OrderFilterForm(request.GET or None)
    orders = P2POrder.objects.none()

    if account:
        orders = P2POrder.objects.filter(exchange_account=account)
        if not form.data.get("show_ignored"):
            orders = orders.filter(show_in_orders=True)

        if form.is_valid():
            if form.cleaned_data.get("date_from"):
                orders = orders.filter(created_at_moscow__date__gte=form.cleaned_data["date_from"])
            if form.cleaned_data.get("date_to"):
                orders = orders.filter(created_at_moscow__date__lte=form.cleaned_data["date_to"])
            if form.cleaned_data.get("side"):
                orders = orders.filter(side=form.cleaned_data["side"])
            if form.cleaned_data.get("status"):
                orders = orders.filter(status=form.cleaned_data["status"])
            if form.cleaned_data.get("search"):
                q = form.cleaned_data["search"]
                orders = orders.filter(
                    models_Q_search(q)
                )

        orders = orders.order_by("-created_at_moscow")[:500]

    return render(request, "orders/list.html", {
        "account": account,
        "orders": orders,
        "form": form,
    })


def models_Q_search(q):
    from django.db.models import Q
    return Q(bybit_order_id__icontains=q) | Q(counterparty_name__icontains=q)


@login_required
def order_ignore(request, pk):
    order = get_object_or_404(P2POrder, pk=pk, exchange_account__user=request.user)
    form = IgnoreOrderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        order.include_in_ledger = False
        order.show_in_orders = False
        order.show_in_export = False
        order.ignore_reason = form.cleaned_data["ignore_reason"]
        order.ignored_at = timezone.now()
        order.ignored_by = request.user
        order.save()
        rebuild_ledger.delay(order.exchange_account_id)
        return redirect("orders:list")
    return render(request, "orders/ignore.html", {"order": order, "form": form})


@login_required
def order_restore(request, pk):
    order = get_object_or_404(P2POrder, pk=pk, exchange_account__user=request.user)
    if request.method == "POST":
        order.include_in_ledger = True
        order.show_in_orders = True
        order.show_in_export = True
        order.ignore_reason = ""
        order.ignored_at = None
        order.ignored_by = None
        order.save()
        rebuild_ledger.delay(order.exchange_account_id)
        return redirect("orders:list")
    return render(request, "orders/restore.html", {"order": order})


@login_required
def order_raw_json(request, pk):
    order = get_object_or_404(P2POrder, pk=pk, exchange_account__user=request.user)
    import json
    data = {
        "list": order.raw_order.raw_list_payload,
        "detail": order.raw_order.raw_detail_payload,
    }
    return render(request, "orders/raw_json.html", {
        "order": order,
        "json_data": json.dumps(data, indent=2, ensure_ascii=False),
    })

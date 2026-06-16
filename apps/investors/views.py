import json
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.investors import services
from apps.investors.models import Investor, InvestorCapitalTransaction, ProfitAllocation


class InvestorForm(forms.ModelForm):
    class Meta:
        model = Investor
        fields = (
            "name", "profit_share_mode", "profit_share_multiplier",
            "profit_share_fixed_pct", "is_active", "comment",
        )
        labels = {
            "name": "Имя",
            "profit_share_mode": "Режим доли прибыли",
            "profit_share_multiplier": "Множитель прибыли",
            "profit_share_fixed_pct": "Фикс. % прибыли",
            "is_active": "Активен",
            "comment": "Комментарий",
        }


class CapitalTxnForm(forms.Form):
    amount_rub = forms.DecimalField(min_value=Decimal("0.01"), label="Сумма, ₽")
    effective_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
        label="Дата/время",
    )
    comment = forms.CharField(required=False, widget=forms.Textarea, label="Комментарий")


class AllocationPeriodForm(forms.Form):
    period_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="С даты")
    period_to = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="По дату")


@login_required
def investor_list(request):
    account = get_active_account(request.user)
    unit_price = services.current_unit_price(request.user, account)
    total_units = services.total_units(request.user)
    equity = services.portfolio_equity(account)

    investors = Investor.objects.filter(user=request.user).order_by("name")
    rows = []
    for inv in investors:
        units = inv.units
        last_alloc = inv.allocations.order_by("-period_to").first()
        rows.append({
            "inv": inv,
            "units": units,
            "capital_value": units * unit_price,
            "capital_share_pct": (units / total_units * 100) if total_units > 0 else Decimal("0"),
            "profit_mode": inv.get_profit_share_mode_display(),
            "profit_share_pct": last_alloc.profit_share_pct if last_alloc else None,
            "earned": inv.earned_profit_total(),
            "paid": inv.settled_total(ProfitAllocation.STATUS_PAID_OUT),
            "reinvested": inv.settled_total(ProfitAllocation.STATUS_REINVESTED),
        })

    return render(request, "investors/list.html", {
        "rows": rows,
        "equity": equity,
        "unit_price": unit_price,
        "total_units": total_units,
    })


@login_required
def investor_create(request):
    form = InvestorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inv = form.save(commit=False)
        inv.user = request.user
        inv.save()
        messages.success(request, "Инвестор добавлен.")
        return redirect("investors:list")
    return render(request, "investors/form.html", {"form": form, "title": "Добавить инвестора"})


@login_required
def investor_edit(request, pk):
    investor = get_object_or_404(Investor, pk=pk, user=request.user)
    form = InvestorForm(request.POST or None, instance=investor)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Инвестор обновлён.")
        return redirect("investors:detail", pk=pk)
    return render(request, "investors/form.html", {"form": form, "title": "Изменить инвестора"})


@login_required
def investor_detail(request, pk):
    investor = get_object_or_404(Investor, pk=pk, user=request.user)
    account = get_active_account(request.user)
    unit_price = services.current_unit_price(request.user, account)

    txns = list(investor.capital_transactions.order_by("effective_at", "id"))
    cap_labels, cap_values = [], []
    cum_units = Decimal("0")
    for t in txns:
        cum_units += t.units_delta
        cap_labels.append(t.effective_at.strftime("%d.%m.%Y"))
        cap_values.append(float(cum_units * t.unit_price))

    allocs = list(investor.allocations.order_by("period_to"))
    earn_labels, earn_values = [], []
    cum = Decimal("0")
    for a in allocs:
        cum += a.net_profit
        earn_labels.append(a.period_to.strftime("%d.%m.%Y"))
        earn_values.append(float(cum))

    units = investor.units
    return render(request, "investors/detail.html", {
        "investor": investor,
        "units": units,
        "unit_price": unit_price,
        "capital_value": units * unit_price,
        "capital_share_pct": services.capital_share_pct(investor, request.user),
        "txns": list(reversed(txns)),
        "allocations": investor.allocations.order_by("-period_to"),
        "earned": investor.earned_profit_total(),
        "paid": investor.settled_total(ProfitAllocation.STATUS_PAID_OUT),
        "reinvested": investor.settled_total(ProfitAllocation.STATUS_REINVESTED),
        "cap_labels": json.dumps(cap_labels),
        "cap_values": json.dumps(cap_values),
        "earn_labels": json.dumps(earn_labels),
        "earn_values": json.dumps(earn_values),
    })


@login_required
def investor_deposit(request, pk):
    return _capital_txn(request, pk, is_deposit=True)


@login_required
def investor_withdraw(request, pk):
    return _capital_txn(request, pk, is_deposit=False)


def _capital_txn(request, pk, is_deposit):
    investor = get_object_or_404(Investor, pk=pk, user=request.user)
    account = get_active_account(request.user)
    initial = {"effective_at": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")}
    form = CapitalTxnForm(request.POST or None, initial=initial)
    title = ("Депозит" if is_deposit else "Вывод") + f" — {investor.name}"

    if request.method == "POST" and form.is_valid():
        try:
            fn = services.deposit if is_deposit else services.withdraw
            fn(
                investor,
                form.cleaned_data["amount_rub"],
                form.cleaned_data["effective_at"],
                account, request.user,
                comment=form.cleaned_data["comment"],
            )
            messages.success(request, f"{'Депозит' if is_deposit else 'Вывод'} проведён. Пересчитайте леджер для обновления equity.")
            return redirect("investors:detail", pk=pk)
        except ValidationError as e:
            form.add_error(None, e)

    return render(request, "investors/form.html", {"form": form, "title": title})


@login_required
def calculate_allocation(request):
    account = get_active_account(request.user)
    form = AllocationPeriodForm(request.GET or None)
    preview = None
    period_from = period_to = None

    if account and form.is_valid():
        period_from = form.cleaned_data["period_from"]
        period_to = form.cleaned_data["period_to"]
        try:
            preview = services.compute_allocation(request.user, period_from, period_to, account)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))

        if request.method == "POST" and preview and request.POST.get("action") == "save":
            services.save_allocation(request.user, period_from, period_to, preview)
            messages.success(request, "Аллокация сохранена.")
            return redirect(f"{request.path}?period_from={period_from}&period_to={period_to}")

    saved = (
        ProfitAllocation.objects.filter(investor__user=request.user)
        .select_related("investor")
        .order_by("-period_to", "-created_at", "investor__name")[:60]
    )

    return render(request, "investors/allocation.html", {
        "form": form,
        "preview": preview,
        "account": account,
        "period_from": period_from,
        "period_to": period_to,
        "saved": saved,
    })


@login_required
def allocation_settle(request, pk):
    allocation = get_object_or_404(ProfitAllocation, pk=pk, investor__user=request.user)
    account = get_active_account(request.user)
    status = request.POST.get("status")
    if status not in (ProfitAllocation.STATUS_PAID_OUT, ProfitAllocation.STATUS_REINVESTED):
        messages.error(request, "Неверный статус.")
    else:
        try:
            services.settle_allocation(allocation, status, account, request.user)
            messages.success(request, "Статус обновлён.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    return redirect(request.META.get("HTTP_REFERER", "investors:list"))

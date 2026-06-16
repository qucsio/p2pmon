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
from apps.investors.models import Investor, InvestorCapitalTransaction, InvestorProfitRule
from apps.ledger.models import LedgerAdjustment
from apps.ledger.tasks import rebuild_ledger


def _rebuild(account):
    if account:
        rebuild_ledger.delay(account.id)


class InvestorForm(forms.ModelForm):
    class Meta:
        model = Investor
        fields = (
            "name", "profit_share_mode", "profit_share_multiplier",
            "profit_share_fixed_pct", "source_investor", "split_percent",
            "residual_investor", "is_active", "comment",
        )
        labels = {
            "name": "Имя", "profit_share_mode": "Режим доли прибыли",
            "profit_share_multiplier": "Множитель прибыли",
            "profit_share_fixed_pct": "Фикс. % прибыли",
            "source_investor": "Источник (для «доли от прибыли»)",
            "split_percent": "% от прибыли источника",
            "residual_investor": "Получатель остатка (для множителя/none)",
            "is_active": "Активен", "comment": "Комментарий",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Investor.objects.filter(user=user) if user else Investor.objects.none()
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        for f in ("source_investor", "residual_investor"):
            self.fields[f].queryset = qs
            self.fields[f].required = False


class ProfitRuleForm(forms.ModelForm):
    class Meta:
        model = InvestorProfitRule
        fields = (
            "mode", "profit_share_multiplier", "profit_share_fixed_pct",
            "source_investor", "split_percent", "residual_investor",
            "effective_from", "effective_to", "comment",
        )
        widgets = {
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_to": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "mode": "Режим", "profit_share_multiplier": "Множитель",
            "profit_share_fixed_pct": "Фикс. %", "source_investor": "Источник",
            "split_percent": "% сплита", "residual_investor": "Получатель остатка",
            "effective_from": "Действует с", "effective_to": "Действует по",
            "comment": "Комментарий",
        }

    def __init__(self, *args, user=None, investor=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Investor.objects.filter(user=user) if user else Investor.objects.none()
        if investor is not None:
            qs = qs.exclude(pk=investor.pk)
        for f in ("source_investor", "residual_investor"):
            self.fields[f].queryset = qs
            self.fields[f].required = False


class CapitalTxnForm(forms.Form):
    amount_rub = forms.DecimalField(min_value=Decimal("0.01"), label="Сумма, ₽")
    effective_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"], label="Дата/время",
    )
    comment = forms.CharField(required=False, widget=forms.Textarea, label="Комментарий")


class ReportPeriodForm(forms.Form):
    period_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="С даты")
    period_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="По дату")


@login_required
def investor_list(request):
    account = get_active_account(request.user)
    rep = services.investor_report(request.user, account)
    rows = []
    for r in rep["rows"]:
        rows.append({
            "inv": r["investor"],
            "economic_capital": r["economic_capital"],
            "net_external": r["net_external_capital"],
            "economic_pnl": r["economic_pnl"],
            "gross": r["gross_by_participation"],
            "displayed": r["displayed_net"],
            "coeff": r["profit_coeff"],
            "residual_out": r["residual_out"],
            "rule_mode": r["rule_mode"],
            "residual_investor": r["residual_investor"],
        })
    return render(request, "investors/list.html", {
        "rows": rows, "equity": rep["equity"],
        "unassigned_residual": rep["unassigned_residual"], "warnings": rep["warnings"],
    })


@login_required
def investor_create(request):
    form = InvestorForm(request.POST or None, user=request.user)
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
    form = InvestorForm(request.POST or None, instance=investor, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Инвестор обновлён.")
        return redirect("investors:detail", pk=pk)
    return render(request, "investors/form.html", {"form": form, "title": "Изменить инвестора"})


@login_required
def investor_detail(request, pk):
    investor = get_object_or_404(Investor, pk=pk, user=request.user)
    account = get_active_account(request.user)

    rep = services.investor_report(request.user, account)
    row = next((r for r in rep["rows"] if r["investor"].id == investor.id), {})

    txns = list(
        investor.capital_transactions.filter(type__in=services.CAPITAL_EVENT_TYPES)
        .order_by("effective_at", "id")
    )
    return render(request, "investors/detail.html", {
        "investor": investor,
        "row": row,
        "txns": list(reversed(txns)),
        "rules": investor.profit_rules.all(),
        "rule_form": ProfitRuleForm(user=request.user, investor=investor),
        "cap_labels": json.dumps(rep["series_labels"]),
        "cap_values": json.dumps(rep["econ_series"].get(investor.id, [])),
        "earn_labels": json.dumps(rep["series_labels"]),
        "earn_values": json.dumps(rep["disp_series"].get(investor.id, [])),
    })


@login_required
def rule_create(request, pk):
    investor = get_object_or_404(Investor, pk=pk, user=request.user)
    form = ProfitRuleForm(request.POST or None, user=request.user, investor=investor)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.investor = investor
        rule.save()
        messages.success(request, "Правило прибыли добавлено.")
    elif request.method == "POST":
        messages.error(request, "; ".join(str(e) for e in form.errors.values()))
    return redirect("investors:detail", pk=pk)


@login_required
def rule_delete(request, pk):
    rule = get_object_or_404(InvestorProfitRule, pk=pk, investor__user=request.user)
    inv_pk = rule.investor_id
    if request.method == "POST":
        rule.delete()
        messages.success(request, "Правило удалено.")
    return redirect("investors:detail", pk=inv_pk)


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
            fn(investor, form.cleaned_data["amount_rub"], form.cleaned_data["effective_at"],
               account, request.user, comment=form.cleaned_data["comment"])
            _rebuild(account)
            messages.success(request, f"{'Депозит' if is_deposit else 'Вывод'} проведён. Леджер пересчитывается.")
            return redirect("investors:detail", pk=pk)
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "investors/form.html", {"form": form, "title": title})


@login_required
def contribution_history(request):
    """Link existing ledger adjustments (money already in the portfolio) to
    investors as dated capital contributions — no new cash is created."""
    account = get_active_account(request.user)
    investors = list(Investor.objects.filter(user=request.user).order_by("name"))

    if request.method == "POST" and account:
        adj = get_object_or_404(
            LedgerAdjustment, pk=request.POST.get("adjustment_id"),
            exchange_account=account, is_deleted=False)
        investor = get_object_or_404(Investor, pk=request.POST.get("investor_id"), user=request.user)
        try:
            services.link_contribution(investor, adj, request.user, account)
            messages.success(request, f"Привязано к {investor.name}.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("investors:history")

    unlinked, linked = [], []
    if account:
        for adj in (
            account.adjustments.filter(is_deleted=False, account=LedgerAdjustment.ACCOUNT_BANK)
            .prefetch_related("investor_transactions__investor").order_by("effective_at")
        ):
            if adj.signed_amount_rub() == 0:
                continue
            if adj.type not in services.LINKABLE_ADJUSTMENT_TYPES:
                continue
            (linked if adj.investor_transactions.all() else unlinked).append(adj)

    return render(request, "investors/history.html", {
        "account": account, "investors": investors,
        "unlinked": unlinked, "linked": linked,
    })


@login_required
def profit_report_view(request):
    """Reporting-only profit by capital ownership over automatic intervals."""
    account = get_active_account(request.user)
    form = ReportPeriodForm(request.GET or None)
    pf = pt = None
    if form.is_valid():
        pf = form.cleaned_data.get("period_from")
        pt = form.cleaned_data.get("period_to")

    report = services.investor_report(request.user, account, pf, pt) if account else {"rows": []}
    flows = services.unassigned_external_flows(account) if account else []

    earned_labels = [r["investor"].name for r in report.get("rows", []) if r["displayed_net"]]
    earned_data = [float(r["displayed_net"]) for r in report.get("rows", []) if r["displayed_net"]]
    cum_datasets = [
        {"label": r["investor"].name, "data": report.get("disp_series", {}).get(r["investor"].id, [])}
        for r in report.get("rows", [])
    ]

    return render(request, "investors/allocation.html", {
        "form": form, "report": report, "account": account, "flows": flows,
        "earned_labels": json.dumps(earned_labels),
        "earned_data": json.dumps(earned_data),
        "cum_labels": json.dumps(report.get("series_labels", [])),
        "cum_datasets": json.dumps(cum_datasets),
    })

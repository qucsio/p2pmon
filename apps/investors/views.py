from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render

from apps.common.helpers import get_active_account
from apps.investors.models import Investor, ProfitAllocation, active_share_total, shares_fully_allocated
from apps.ledger.models import DailySnapshot


class InvestorForm(forms.ModelForm):
    class Meta:
        model = Investor
        fields = ("name", "share_percent", "is_active", "comment")


class AllocationForm(forms.Form):
    period_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_to = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))


@login_required
def investor_list(request):
    investors = Investor.objects.filter(user=request.user).order_by("name")
    total_share = investors.filter(is_active=True).aggregate(
        total=Sum("share_percent")
    )["total"] or Decimal("0")
    return render(request, "investors/list.html", {
        "investors": investors,
        "total_share": total_share,
    })


@login_required
def investor_create(request):
    form = InvestorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inv = form.save(commit=False)
        inv.user = request.user
        inv.save()
        return redirect("investors:list")
    return render(request, "investors/form.html", {"form": form, "title": "Add Investor"})


@login_required
def investor_edit(request, pk):
    investor = Investor.objects.get(pk=pk, user=request.user)
    form = InvestorForm(request.POST or None, instance=investor)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("investors:list")
    return render(request, "investors/form.html", {"form": form, "title": "Edit Investor"})


@login_required
def calculate_allocation(request):
    account = get_active_account(request.user)
    form = AllocationForm(request.POST or None)
    allocations = []

    if request.method == "POST" and form.is_valid() and account:
        if not shares_fully_allocated(request.user):
            total = active_share_total(request.user)
            messages.error(
                request,
                f"Cannot calculate allocation: active shares must sum to 100% (currently {total}%)",
            )
        else:
            period_from = form.cleaned_data["period_from"]
            period_to = form.cleaned_data["period_to"]

            snaps = DailySnapshot.objects.filter(
                exchange_account=account,
                day__gte=period_from,
                day__lte=period_to,
            )
            gross = sum(s.gross_realized_pnl for s in snaps)
            fees = sum(s.fees for s in snaps)
            tax = sum(s.tax_accrual for s in snaps)
            net = sum(s.net_profit_after_tax for s in snaps)

            ProfitAllocation.objects.filter(
                investor__user=request.user,
                period_from=period_from,
                period_to=period_to,
            ).delete()

            for inv in Investor.objects.filter(user=request.user, is_active=True):
                share = inv.share_percent / Decimal("100")
                pa = ProfitAllocation.objects.create(
                    period_from=period_from,
                    period_to=period_to,
                    investor=inv,
                    share_percent=inv.share_percent,
                    gross_profit=gross * share,
                    fees_part=fees * share,
                    tax_part=tax * share,
                    net_profit=net * share,
                )
                allocations.append(pa)

    recent = ProfitAllocation.objects.filter(investor__user=request.user).order_by("-created_at")[:20]

    return render(request, "investors/allocation.html", {
        "form": form,
        "allocations": allocations or recent,
        "account": account,
    })

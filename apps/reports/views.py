import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.exchange.models import SyncLog
from apps.investors.models import TaxSetting
from apps.ledger.models import DailySnapshot
from apps.reconciliation.models import BalanceSnapshot


@login_required
def dashboard(request):
    account = get_active_account(request.user)
    period = request.GET.get("period", "30")
    today = timezone.localdate()

    if period == "today":
        date_from = today
    elif period == "7":
        date_from = today - timedelta(days=7)
    elif period == "month":
        date_from = today.replace(day=1)
    elif period == "custom":
        date_from = date.fromisoformat(request.GET.get("from", str(today - timedelta(days=30))))
        date_to = date.fromisoformat(request.GET.get("to", str(today)))
    else:
        date_from = today - timedelta(days=30)

    date_to = today if period != "custom" else date_to

    snapshots = []
    latest = None
    today_snap = None
    last_sync = None
    balance_warning = False

    if account:
        snapshots = list(
            DailySnapshot.objects.filter(
                exchange_account=account,
                day__gte=date_from,
                day__lte=date_to,
            ).order_by("day")
        )
        latest = DailySnapshot.objects.filter(exchange_account=account).order_by("-day").first()
        today_snap = DailySnapshot.objects.filter(exchange_account=account, day=today).first()
        last_sync = SyncLog.objects.filter(exchange_account=account).first()
        last_recon = BalanceSnapshot.objects.filter(exchange_account=account).first()
        if last_recon and (last_recon.bank_diff != 0 or last_recon.exchange_diff != 0):
            balance_warning = True

    chart_labels = [s.day.isoformat() for s in snapshots]
    chart_equity = [float(s.total_equity) for s in snapshots]
    chart_equity_pnl = [float(s.daily_total_equity_pnl) for s in snapshots]
    chart_wac_pnl = [float(s.daily_wac_realized_pnl) for s in snapshots]
    chart_net_profit = [float(s.net_profit_after_tax) for s in snapshots]
    chart_bank = [float(s.bank_balance) for s in snapshots]
    chart_exchange = [float(s.exchange_balance) for s in snapshots]
    chart_wac_price = [float(s.wac_price) for s in snapshots]
    chart_last_price = [float(s.last_price) for s in snapshots]

    context = {
        "account": account,
        "latest": latest,
        "today_snap": today_snap,
        "last_sync": last_sync,
        "balance_warning": balance_warning,
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
        "chart_labels": json.dumps(chart_labels),
        "chart_equity": json.dumps(chart_equity),
        "chart_equity_pnl": json.dumps(chart_equity_pnl),
        "chart_wac_pnl": json.dumps(chart_wac_pnl),
        "chart_net_profit": json.dumps(chart_net_profit),
        "chart_bank": json.dumps(chart_bank),
        "chart_exchange": json.dumps(chart_exchange),
        "chart_wac_price": json.dumps(chart_wac_price),
        "chart_last_price": json.dumps(chart_last_price),
    }
    return render(request, "reports/dashboard.html", context)


@login_required
def net_profit(request):
    account = get_active_account(request.user)
    period = request.GET.get("period", "month")
    today = timezone.localdate()

    if period == "today":
        date_from = today
    elif period == "week":
        date_from = today - timedelta(days=today.weekday())
    elif period == "month":
        date_from = today.replace(day=1)
    else:
        date_from = date.fromisoformat(request.GET.get("from", str(today.replace(day=1))))
        date_to = date.fromisoformat(request.GET.get("to", str(today)))

    date_to = today if period != "custom" else date_to

    daily_rows = []
    totals = {
        "gross": 0, "fees": 0, "other": 0,
        "before_tax": 0, "tax": 0, "after_tax": 0,
    }

    if account:
        snaps = DailySnapshot.objects.filter(
            exchange_account=account,
            day__gte=date_from,
            day__lte=date_to,
        ).order_by("day")
        for s in snaps:
            daily_rows.append(s)
            totals["gross"] += float(s.gross_realized_pnl)
            totals["fees"] += float(s.fees)
            totals["before_tax"] += float(s.net_profit_before_tax)
            totals["tax"] += float(s.tax_accrual)
            totals["after_tax"] += float(s.net_profit_after_tax)

    return render(request, "reports/net_profit.html", {
        "account": account,
        "period": period,
        "daily_rows": daily_rows,
        "totals": totals,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def daily_report(request):
    account = get_active_account(request.user)
    rows = []
    if account:
        rows = DailySnapshot.objects.filter(exchange_account=account).order_by("-day")[:90]
    return render(request, "reports/daily_report.html", {"account": account, "rows": rows})


@login_required
def weekly_report(request):
    account = get_active_account(request.user)
    rows = []
    if account:
        from apps.ledger.models import WeeklySnapshot
        rows = WeeklySnapshot.objects.filter(exchange_account=account).order_by("-week")[:52]
    return render(request, "reports/weekly_report.html", {"account": account, "rows": rows})

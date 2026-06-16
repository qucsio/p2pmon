import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.exchange.models import SyncLog
from apps.investors.models import TaxSetting
from apps.ledger.models import DailySnapshot, LedgerAdjustment
from apps.orders.models import P2POrder
from apps.reconciliation.models import BalanceSnapshot


def _resolve_range(request, default_period, today):
    """Resolve a flexible reporting range from query params.

    Supports: today, 7, 30, 90, week, month, year, all, custom.
    Returns (period, date_from, date_to). date_from is None for "all time".
    """
    period = request.GET.get("period", default_period)
    date_to = today

    if period == "custom":
        try:
            date_from = date.fromisoformat(request.GET.get("from"))
            date_to = date.fromisoformat(request.GET.get("to"))
        except (TypeError, ValueError):
            period, date_from = "30", today - timedelta(days=30)
    elif period == "today":
        date_from = today
    elif period == "week":
        date_from = today - timedelta(days=today.weekday())
    elif period == "month":
        date_from = today.replace(day=1)
    elif period == "year":
        date_from = today.replace(month=1, day=1)
    elif period == "all":
        date_from = None
    elif period in ("7", "30", "90"):
        date_from = today - timedelta(days=int(period))
    else:
        period, date_from = "30", today - timedelta(days=30)

    return period, date_from, date_to


@login_required
def dashboard(request):
    account = get_active_account(request.user)
    today = timezone.localdate()
    period, date_from, date_to = _resolve_range(request, "30", today)

    snapshots = []
    latest = None
    today_snap = None
    last_sync = None
    balance_warning = False

    adj_by_day = {}
    if account:
        snap_qs = DailySnapshot.objects.filter(
            exchange_account=account, day__lte=date_to
        )
        if date_from is not None:
            snap_qs = snap_qs.filter(day__gte=date_from)
        snapshots = list(snap_qs.order_by("day"))

        # Per-day signed adjustment value in RUB (used to strip deposit/withdrawal
        # spikes from the equity-delta PnL curve).
        adj_qs = LedgerAdjustment.objects.filter(
            exchange_account=account, is_deleted=False, include_in_ledger=True,
            effective_at__date__lte=date_to,
        )
        if date_from is not None:
            adj_qs = adj_qs.filter(effective_at__date__gte=date_from)
        price_by_day = {s.day: s.last_price for s in snapshots}
        for adj in adj_qs:
            d = timezone.localtime(adj.effective_at).date()
            rub = adj.signed_amount_rub()
            usdt = adj.signed_amount_usdt()
            if usdt:
                rub += usdt * price_by_day.get(d, Decimal("0"))
            adj_by_day[d] = adj_by_day.get(d, Decimal("0")) + rub

        latest = DailySnapshot.objects.filter(exchange_account=account).order_by("-day").first()
        today_snap = DailySnapshot.objects.filter(exchange_account=account, day=today).first()
        last_sync = SyncLog.objects.filter(exchange_account=account).first()
        last_recon = BalanceSnapshot.objects.filter(exchange_account=account).first()
        if last_recon and (last_recon.bank_diff != 0 or last_recon.exchange_diff != 0):
            balance_warning = True

    chart_labels = [s.day.isoformat() for s in snapshots]
    chart_equity = [float(s.total_equity) for s in snapshots]
    chart_equity_pnl = [float(s.daily_total_equity_pnl) for s in snapshots]
    # Same curve with deposit/withdrawal spikes removed (trading PnL only).
    chart_equity_pnl_clean = [
        float(s.daily_total_equity_pnl - adj_by_day.get(s.day, Decimal("0")))
        for s in snapshots
    ]
    chart_wac_pnl = [float(s.daily_wac_realized_pnl) for s in snapshots]
    chart_net_profit = [float(s.net_profit_after_tax) for s in snapshots]
    chart_bank = [float(s.bank_balance) for s in snapshots]
    chart_exchange = [float(s.exchange_balance) for s in snapshots]
    chart_wac_price = [float(s.wac_price) for s in snapshots]
    chart_last_price = [float(s.last_price) for s in snapshots]

    # Period totals for the KPI strip.
    sum_equity_pnl = sum((s.daily_total_equity_pnl for s in snapshots), Decimal("0"))
    sum_equity_pnl_clean = sum_equity_pnl - sum(adj_by_day.values(), Decimal("0"))
    sum_net_profit = sum((s.net_profit_after_tax for s in snapshots), Decimal("0"))
    sum_volume = sum((s.volume_rub for s in snapshots), Decimal("0"))

    context = {
        "sum_equity_pnl": sum_equity_pnl,
        "sum_equity_pnl_clean": sum_equity_pnl_clean,
        "sum_net_profit": sum_net_profit,
        "sum_volume": sum_volume,
        "chart_equity_pnl_clean": json.dumps(chart_equity_pnl_clean),
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
def volumes(request):
    account = get_active_account(request.user)
    today = timezone.localdate()
    period, date_from, date_to = _resolve_range(request, "year", today)

    # Daily buy / sell volume in RUB computed live from completed orders.
    buy = defaultdict(float)
    sell = defaultdict(float)
    if account:
        order_qs = P2POrder.objects.filter(
            exchange_account=account,
            include_in_ledger=True,
            created_at_moscow__date__lte=date_to,
        )
        if date_from is not None:
            order_qs = order_qs.filter(created_at_moscow__date__gte=date_from)
        rows = (
            order_qs.annotate(d=TruncDate("created_at_moscow"))
            .values("d", "side")
            .annotate(total=Sum("amount_rub"))
        )
        for r in rows:
            bucket = buy if r["side"] == P2POrder.SIDE_BUY else sell
            bucket[r["d"]] += float(r["total"] or 0)

    days = sorted(set(buy) | set(sell))
    cal = []
    total_buy = total_sell = 0.0
    for d in days:
        b, s = buy.get(d, 0.0), sell.get(d, 0.0)
        total_buy += b
        total_sell += s
        cal.append({"date": d.isoformat(), "buy": b, "sell": s, "total": b + s})

    total_all = total_buy + total_sell
    active_days = len([c for c in cal if c["total"] > 0])

    return render(request, "reports/volumes.html", {
        "account": account,
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
        "cal_json": json.dumps(cal),
        "total_buy": total_buy,
        "total_sell": total_sell,
        "total_all": total_all,
        "active_days": active_days,
        "avg_per_day": (total_all / active_days) if active_days else 0,
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

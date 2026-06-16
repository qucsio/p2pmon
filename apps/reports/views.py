import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncMonth
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
    # Total equity with capital-flow steps removed: subtract the running sum of
    # in-window adjustments so deposits/withdrawals don't show as vertical jumps.
    chart_equity_clean = []
    _cum_adj = Decimal("0")
    for s in snapshots:
        _cum_adj += adj_by_day.get(s.day, Decimal("0"))
        chart_equity_clean.append(float(s.total_equity - _cum_adj))
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

    order_count = 0
    if account:
        oc_qs = P2POrder.objects.filter(
            exchange_account=account, include_in_ledger=True,
            created_at_moscow__date__lte=date_to,
        )
        if date_from is not None:
            oc_qs = oc_qs.filter(created_at_moscow__date__gte=date_from)
        order_count = oc_qs.count()

    context = {
        "order_count": order_count,
        "sum_equity_pnl": sum_equity_pnl,
        "sum_equity_pnl_clean": sum_equity_pnl_clean,
        "sum_net_profit": sum_net_profit,
        "sum_volume": sum_volume,
        "chart_equity_pnl_clean": json.dumps(chart_equity_pnl_clean),
        "chart_equity_clean": json.dumps(chart_equity_clean),
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
    today = timezone.localdate()
    period, date_from, date_to = _resolve_range(request, "month", today)

    daily_rows = []
    totals = {
        "gross": 0, "fees": 0, "other": 0,
        "before_tax": 0, "tax": 0, "after_tax": 0,
    }

    if account:
        snaps = DailySnapshot.objects.filter(
            exchange_account=account, day__lte=date_to
        ).order_by("day")
        if date_from is not None:
            snaps = snaps.filter(day__gte=date_from)
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

    # Daily buy / sell volume in RUB + order counts, computed live from orders.
    buy = defaultdict(float)
    sell = defaultdict(float)
    n_buy = defaultdict(int)
    n_sell = defaultdict(int)
    total_orders = 0
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
            .annotate(total=Sum("amount_rub"), cnt=Count("id"))
        )
        for r in rows:
            if r["side"] == P2POrder.SIDE_BUY:
                buy[r["d"]] += float(r["total"] or 0)
                n_buy[r["d"]] += r["cnt"]
            else:
                sell[r["d"]] += float(r["total"] or 0)
                n_sell[r["d"]] += r["cnt"]
            total_orders += r["cnt"]

    days = sorted(set(buy) | set(sell))
    cal = []
    total_buy = total_sell = 0.0
    for d in days:
        b, s = buy.get(d, 0.0), sell.get(d, 0.0)
        nb, ns = n_buy.get(d, 0), n_sell.get(d, 0)
        total_buy += b
        total_sell += s
        cal.append({
            "date": d.isoformat(), "buy": b, "sell": s, "total": b + s,
            "n_buy": nb, "n_sell": ns, "n": nb + ns,
        })

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
        "total_orders": total_orders,
    })


@login_required
def daily_report(request):
    account = get_active_account(request.user)
    rows = []
    if account:
        rows = DailySnapshot.objects.filter(exchange_account=account).order_by("-day")[:90]
    return render(request, "reports/daily_report.html", {"account": account, "rows": rows})


def _week_sort_key(week_str):
    """Parse '2026-W05' → (2026, 5); tolerant of non-padded legacy values."""
    try:
        y, w = week_str.split("-W")
        return (int(y), int(w))
    except (ValueError, AttributeError):
        return (0, 0)


@login_required
def weekly_report(request):
    account = get_active_account(request.user)
    rows = []
    if account:
        from apps.ledger.models import WeeklySnapshot
        qs = list(WeeklySnapshot.objects.filter(exchange_account=account))
        qs.sort(key=lambda s: _week_sort_key(s.week), reverse=True)
        rows = qs[:52]
    return render(request, "reports/weekly_report.html", {"account": account, "rows": rows})


@login_required
def monthly_report(request):
    account = get_active_account(request.user)
    months = []
    if account:
        snaps = DailySnapshot.objects.filter(exchange_account=account).order_by("day")
        # Order counts per month.
        oc = {}
        for r in (
            P2POrder.objects.filter(exchange_account=account, include_in_ledger=True)
            .annotate(m=TruncMonth("created_at_moscow"))
            .values("m")
            .annotate(cnt=Count("id"))
        ):
            if r["m"]:
                oc[(r["m"].year, r["m"].month)] = r["cnt"]

        groups = {}
        for s in snaps:
            key = (s.day.year, s.day.month)
            groups.setdefault(key, []).append(s)

        ru_months = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
        ]
        for key in sorted(groups, reverse=True):
            g = groups[key]
            last = g[-1]
            months.append({
                "year": key[0],
                "month": key[1],
                "name": f"{ru_months[key[1] - 1]} {key[0]}",
                "label": date(key[0], key[1], 1),
                "equity_pnl": sum((x.daily_total_equity_pnl for x in g), Decimal("0")),
                "realized": sum((x.daily_wac_realized_pnl for x in g), Decimal("0")),
                "fees": sum((x.fees for x in g), Decimal("0")),
                "net": sum((x.net_profit_after_tax for x in g), Decimal("0")),
                "volume": sum((x.volume_rub for x in g), Decimal("0")),
                "equity_end": last.total_equity,
                "orders": oc.get(key, 0),
                "active_days": len(g),
            })

    return render(request, "reports/monthly_report.html", {"account": account, "months": months})

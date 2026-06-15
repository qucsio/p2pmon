from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.common.helpers import get_active_account
from apps.exchange.models import SyncLog
from apps.ledger.tasks import sync_bybit_orders


@login_required
def sync_log_list(request):
    account = get_active_account(request.user)
    logs = []
    next_sync = None
    if account:
        logs = SyncLog.objects.filter(exchange_account=account)[:50]
        from datetime import timedelta
        last = account.last_successful_sync_at
        if last:
            next_sync = last + timedelta(hours=1)

    return render(request, "exchange/sync_logs.html", {
        "account": account,
        "logs": logs,
        "next_sync": next_sync,
    })


@login_required
def sync_manual(request):
    account = get_active_account(request.user)
    if account and request.method == "POST":
        sync_bybit_orders.delay(account.id, mode=SyncLog.MODE_MANUAL, user_id=request.user.id)
    return redirect("exchange:sync_logs")

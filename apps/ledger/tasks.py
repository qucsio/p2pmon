from celery import shared_task

from apps.exchange.models import ExchangeAccount, SyncLog
from apps.exchange.services.sync import SyncService
from apps.ledger.services.engine import LedgerEngine


@shared_task
def sync_bybit_orders(account_id: int, mode: str = SyncLog.MODE_MANUAL, user_id: int | None = None):
    account = ExchangeAccount.objects.get(pk=account_id)
    user = None
    if user_id:
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.filter(pk=user_id).first()
    return SyncService(account, mode, user=user).run().id


@shared_task
def sync_all_active_accounts():
    for account in ExchangeAccount.objects.filter(is_active=True):
        sync_bybit_orders.delay(account.id, mode=SyncLog.MODE_HOURLY)


@shared_task
def rebuild_ledger(account_id: int):
    account = ExchangeAccount.objects.get(pk=account_id)
    LedgerEngine(account).rebuild()

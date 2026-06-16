from celery import shared_task

from apps.exchange.models import ExchangeAccount, SyncLog
from apps.exchange.services.sync import fetch_order_details_for_ids
from apps.orders.services.normalizer import normalize_account_orders


@shared_task
def fetch_sync_order_details(account_id: int, order_ids: list[str], sync_log_id: int | None = None):
    account = ExchangeAccount.objects.get(pk=account_id)
    log = SyncLog.objects.filter(pk=sync_log_id).first() if sync_log_id else None
    fetched, warnings = fetch_order_details_for_ids(account, order_ids, log=log)
    if fetched:
        normalize_account_orders(account, rebuild_ledger=False)
        from apps.ledger.tasks import rebuild_ledger
        rebuild_ledger.delay(account_id)
    return fetched

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone as dj_timezone

from apps.exchange.models import ExchangeAccount, SyncLog
from apps.exchange.services.bybit_client import (
    BybitClient,
    COMPLETED_STATUS,
    MAX_QUERY_WINDOW_DAYS,
    PAGE_SIZE,
    SAFE_HISTORY_DAYS,
    iter_query_windows,
)
from apps.exchange.services.lock import sync_lock
from apps.orders.models import RawP2POrder
from apps.orders.services.normalizer import normalize_account_orders

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, account: ExchangeAccount, mode: str, user=None):
        self.account = account
        self.mode = mode
        self.user = user
        self.client = BybitClient(
            api_key=account.get_api_key(),
            api_secret=account.get_api_secret(),
        )
        self._pending_detail_ids: set[str] = set()

    def run(self) -> SyncLog:
        with sync_lock(self.account.id) as acquired:
            if not acquired:
                return SyncLog.objects.create(
                    exchange_account=self.account,
                    status=SyncLog.STATUS_SKIPPED,
                    mode=self.mode,
                    message="Sync already running",
                    created_by=self.user,
                    finished_at=dj_timezone.now(),
                )
            return self._execute_sync()

    def _execute_sync(self) -> SyncLog:
        self._pending_detail_ids = set()
        period_from, period_to = self._resolve_period()
        log = SyncLog.objects.create(
            exchange_account=self.account,
            status=SyncLog.STATUS_RUNNING,
            mode=self.mode,
            period_from=period_from,
            period_to=period_to,
            created_by=self.user,
        )

        try:
            self._sync_period(log, period_from, period_to)
            normalize_account_orders(self.account)

            self.account.last_successful_sync_at = period_to
            self.account.save(update_fields=["last_successful_sync_at", "updated_at"])

            log.status = SyncLog.STATUS_SUCCESS if log.errors_count == 0 else SyncLog.STATUS_PARTIAL
            log.message = f"Sync completed: {log.orders_created} created, {log.orders_updated} updated"
        except Exception as exc:
            logger.exception("Sync failed for account %s", self.account.id)
            log.status = SyncLog.STATUS_FAILED
            log.raw_error = str(exc)
            log.message = str(exc)
            log.errors_count += 1
        finally:
            log.finished_at = dj_timezone.now()
            log.save()

        if log.status in (SyncLog.STATUS_SUCCESS, SyncLog.STATUS_PARTIAL):
            from apps.ledger.tasks import rebuild_ledger
            rebuild_ledger.delay(self.account.id)

        return log

    def _resolve_period(self) -> tuple[datetime, datetime]:
        now = dj_timezone.now()
        earliest = now - timedelta(days=SAFE_HISTORY_DAYS)

        if self.mode == SyncLog.MODE_BACKFILL:
            return earliest, now

        overlap = timedelta(days=settings.SYNC_OVERLAP_DAYS)
        if self.account.last_successful_sync_at:
            period_from = self.account.last_successful_sync_at - overlap
        else:
            period_from = earliest

        period_from = max(period_from, earliest)
        return period_from, now

    def _sync_period(self, log: SyncLog, period_from: datetime, period_to: datetime):
        windows = list(iter_query_windows(period_from, period_to))
        logger.info(
            "Sync account %s: %s -> %s in %s window(s) (max %s days each)",
            self.account.id,
            period_from,
            period_to,
            len(windows),
            MAX_QUERY_WINDOW_DAYS,
        )
        for window_start, window_end in windows:
            self._sync_single_window(log, window_start, window_end)
        self._fetch_details(log)

    def _sync_single_window(self, log: SyncLog, period_from: datetime, period_to: datetime):
        begin_ms = str(int(period_from.timestamp() * 1000))
        end_ms = str(int(period_to.timestamp() * 1000))
        page = 1

        while True:
            resp = self.client.get_orders(
                page=page,
                size=PAGE_SIZE,
                status=COMPLETED_STATUS,
                begin_time=begin_ms,
                end_time=end_ms,
            )
            items = resp.get("result", {}).get("items", [])
            if not items:
                break

            for raw_item in items:
                self._upsert_raw_order(raw_item, log)

            if len(items) < PAGE_SIZE:
                break
            page += 1
            self.client.page_sleep()

    def _upsert_raw_order(self, raw_item: dict, log: SyncLog):
        order_id = str(raw_item["id"])
        log.orders_fetched += 1

        existing = RawP2POrder.objects.filter(
            exchange_account=self.account,
            bybit_order_id=order_id,
        ).first()

        if existing:
            existing.raw_list_payload = raw_item
            existing.save(update_fields=["raw_list_payload", "updated_at"])
            log.orders_updated += 1
            raw = existing
        else:
            raw = RawP2POrder.objects.create(
                exchange_account=self.account,
                bybit_order_id=order_id,
                raw_list_payload=raw_item,
            )
            log.orders_created += 1

        if not raw.raw_detail_payload:
            self._pending_detail_ids.add(order_id)

    def _fetch_details(self, log: SyncLog):
        for order_id in sorted(self._pending_detail_ids):
            raw = RawP2POrder.objects.filter(
                exchange_account=self.account,
                bybit_order_id=order_id,
            ).first()
            if not raw or raw.raw_detail_payload:
                continue
            try:
                detail = self.client.get_order_details(order_id)
                raw.raw_detail_payload = detail
                raw.detail_fetched_at = dj_timezone.now()
                raw.save(update_fields=["raw_detail_payload", "detail_fetched_at", "updated_at"])
                log.details_fetched += 1
                self.client.page_sleep()
            except Exception as exc:
                logger.warning("Failed to fetch detail for %s: %s", order_id, exc)
                log.warnings_count += 1


def fetch_all_missing_details(exchange_account: ExchangeAccount) -> int:
    """
    Optionally fetch order details for all raw orders missing detail payload.
    Not run automatically during sync — use when backfilling details for old rows.
    """
    client = BybitClient(
        api_key=exchange_account.get_api_key(),
        api_secret=exchange_account.get_api_secret(),
    )
    from django.db.models import Q

    pending = RawP2POrder.objects.filter(
        exchange_account=exchange_account,
    ).filter(
        Q(detail_fetched_at__isnull=True) | Q(raw_detail_payload={})
    )
    fetched = 0
    for raw in pending.distinct():
        try:
            detail = client.get_order_details(raw.bybit_order_id)
            raw.raw_detail_payload = detail
            raw.detail_fetched_at = dj_timezone.now()
            raw.save(update_fields=["raw_detail_payload", "detail_fetched_at", "updated_at"])
            fetched += 1
            client.page_sleep()
        except Exception as exc:
            logger.warning(
                "Failed to fetch detail for %s: %s",
                raw.bybit_order_id,
                exc,
            )
    if fetched:
        normalize_account_orders(exchange_account)
    return fetched

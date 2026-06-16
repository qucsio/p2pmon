import logging
import math
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

LOG_PROGRESS_EVERY = 30


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
            normalize_account_orders(self.account, rebuild_ledger=False)

            self.account.last_successful_sync_at = period_to
            self.account.save(update_fields=["last_successful_sync_at", "updated_at"])

            log.status = SyncLog.STATUS_SUCCESS if log.errors_count == 0 else SyncLog.STATUS_PARTIAL
            pending_count = len(self._pending_detail_ids)
            log.message = (
                f"List sync completed: {log.orders_created} created, "
                f"{log.orders_updated} updated"
            )
            if pending_count:
                log.message += f"; {pending_count} details queued"
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

            pending = sorted(self._pending_detail_ids)
            if pending:
                from apps.exchange.tasks import fetch_sync_order_details
                fetch_sync_order_details.delay(self.account.id, pending, log.id)

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
        for index, (window_start, window_end) in enumerate(windows, start=1):
            logger.info(
                "Sync account %s: window %s/%s %s -> %s",
                self.account.id,
                index,
                len(windows),
                window_start,
                window_end,
            )
            self._sync_single_window(log, window_start, window_end)

    def _sync_single_window(self, log: SyncLog, period_from: datetime, period_to: datetime):
        begin_ms = str(int(period_from.timestamp() * 1000))
        end_ms = str(int(period_to.timestamp() * 1000))
        page = 1
        seen_ids: set[str] = set()
        total_count: int | None = None

        while True:
            resp = self.client.get_orders(
                page=page,
                size=PAGE_SIZE,
                status=COMPLETED_STATUS,
                begin_time=begin_ms,
                end_time=end_ms,
            )
            result = resp.get("result", {})
            items = result.get("items", [])
            if total_count is None:
                api_count = result.get("count")
                if api_count is not None:
                    total_count = int(api_count)

            if not items:
                break

            new_in_page = 0
            for raw_item in items:
                order_id = str(raw_item["id"])
                if order_id in seen_ids:
                    continue
                seen_ids.add(order_id)
                self._upsert_raw_order(raw_item, log)
                new_in_page += 1

            if new_in_page == 0:
                logger.warning(
                    "Sync account %s: duplicate page at page %s, stopping window",
                    self.account.id,
                    page,
                )
                break

            if log.orders_fetched % LOG_PROGRESS_EVERY == 0:
                self._save_progress(log)

            logger.info(
                "Sync account %s: page %s fetched %s new orders (%s total in window)",
                self.account.id,
                page,
                new_in_page,
                len(seen_ids),
            )

            if len(items) < PAGE_SIZE:
                break
            if total_count is not None and len(seen_ids) >= total_count:
                break
            if total_count is not None and page >= math.ceil(total_count / PAGE_SIZE):
                break

            page += 1
            self.client.page_sleep()

        self._save_progress(log)

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

    @staticmethod
    def _save_progress(log: SyncLog):
        log.save(
            update_fields=[
                "orders_fetched",
                "orders_created",
                "orders_updated",
                "details_fetched",
                "warnings_count",
                "errors_count",
            ]
        )


def fetch_order_details_for_ids(
    exchange_account: ExchangeAccount,
    order_ids: list[str] | set[str],
    log: SyncLog | None = None,
) -> tuple[int, int]:
    client = BybitClient(
        api_key=exchange_account.get_api_key(),
        api_secret=exchange_account.get_api_secret(),
    )
    fetched = 0
    warnings = 0
    for order_id in sorted(set(order_ids)):
        raw = RawP2POrder.objects.filter(
            exchange_account=exchange_account,
            bybit_order_id=order_id,
        ).first()
        if not raw or raw.raw_detail_payload:
            continue
        try:
            detail = client.get_order_details(order_id)
            raw.raw_detail_payload = detail
            raw.detail_fetched_at = dj_timezone.now()
            raw.save(update_fields=["raw_detail_payload", "detail_fetched_at", "updated_at"])
            fetched += 1
            if log:
                log.details_fetched += 1
            client.page_sleep()
        except Exception as exc:
            logger.warning("Failed to fetch detail for %s: %s", order_id, exc)
            warnings += 1
            if log:
                log.warnings_count += 1

    if log and (fetched or warnings):
        log.save(update_fields=["details_fetched", "warnings_count"])

    return fetched, warnings


def fetch_all_missing_details(exchange_account: ExchangeAccount) -> int:
    """
    Optionally fetch order details for all raw orders missing detail payload.
    Not run automatically during sync — use when backfilling details for old rows.
    """
    from django.db.models import Q

    pending = RawP2POrder.objects.filter(
        exchange_account=exchange_account,
    ).filter(
        Q(detail_fetched_at__isnull=True) | Q(raw_detail_payload={})
    )
    order_ids = list(pending.values_list("bybit_order_id", flat=True).distinct())
    fetched, _ = fetch_order_details_for_ids(exchange_account, order_ids)
    if fetched:
        normalize_account_orders(exchange_account, rebuild_ledger=False)
    return fetched

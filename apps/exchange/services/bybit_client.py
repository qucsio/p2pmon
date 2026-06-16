import logging
import time
from datetime import timedelta
from typing import Any, Iterator

from bybit_p2p import P2P

logger = logging.getLogger(__name__)

COMPLETED_STATUS = 50
PAGE_SIZE = 30

# Bybit P2P simplifyList: query window must not exceed 90 days (use 88 for margin).
MAX_QUERY_WINDOW_DAYS = 88
# API may expose ~180 days of history; stay slightly under for safety.
MAX_HISTORY_DAYS = 180
SAFE_HISTORY_DAYS = 175

# Bybit P2P read limit is 10 req/sec per UID; IP limit is 600 req/5 sec.
PAGE_SLEEP_SECONDS = 1


class BybitClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self._api = P2P(testnet=testnet, api_key=api_key, api_secret=api_secret)

    def get_orders(
        self,
        page: int = 1,
        size: int = PAGE_SIZE,
        status: int = COMPLETED_STATUS,
        begin_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        kwargs = {"page": page, "size": size, "status": status}
        if begin_time:
            kwargs["beginTime"] = begin_time
        if end_time:
            kwargs["endTime"] = end_time
        return self._call_with_retry(self._api.get_orders, **kwargs)

    def get_order_details(self, order_id: str) -> dict[str, Any]:
        return self._call_with_retry(self._api.get_order_details, orderId=order_id)

    def _call_with_retry(self, func, max_retries: int = 3, **kwargs):
        delay = 2
        last_exc = None
        for attempt in range(max_retries):
            try:
                result = func(**kwargs)
                code = result.get("retCode", result.get("ret_code", 0))
                try:
                    code = int(code)
                except (TypeError, ValueError):
                    code = 0
                if code != 0:
                    ret_msg = result.get("retMsg") or result.get("ret_msg", "Unknown error")
                    raise RuntimeError(f"Bybit API error ({code}): {ret_msg}")
                return result
            except Exception as exc:
                last_exc = exc
                logger.warning("Bybit API attempt %s failed: %s", attempt + 1, exc)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        raise last_exc

    @staticmethod
    def page_sleep():
        # Bybit P2P read limit is 10 req/sec per UID; IP limit is 600 req/5 sec.
        time.sleep(PAGE_SLEEP_SECONDS)


def iter_query_windows(period_from, period_to) -> Iterator[tuple]:
    """Split [period_from, period_to] into chunks of at most MAX_QUERY_WINDOW_DAYS."""
    max_delta = timedelta(days=MAX_QUERY_WINDOW_DAYS)
    cursor = period_from
    while cursor < period_to:
        window_end = min(cursor + max_delta, period_to)
        yield cursor, window_end
        cursor = window_end

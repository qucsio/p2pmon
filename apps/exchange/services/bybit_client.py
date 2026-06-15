import logging
import time
from typing import Any

from bybit_p2p import P2P

logger = logging.getLogger(__name__)

COMPLETED_STATUS = 50
PAGE_SIZE = 30
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
                if result.get("ret_code", 0) != 0 and result.get("retCode", 0) != 0:
                    ret_msg = result.get("ret_msg") or result.get("retMsg", "Unknown error")
                    raise RuntimeError(f"Bybit API error: {ret_msg}")
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
        time.sleep(PAGE_SLEEP_SECONDS)

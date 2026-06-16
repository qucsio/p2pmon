from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.exchange.models import ExchangeAccount, SyncLog
from apps.exchange.services.bybit_client import MAX_QUERY_WINDOW_DAYS, SAFE_HISTORY_DAYS, iter_query_windows
from apps.exchange.services.sync import SyncService

User = get_user_model()


class QueryWindowTests(TestCase):
    def test_single_window_under_limit(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=30)
        windows = list(iter_query_windows(start, end))
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0], (start, end))

    def test_chunks_long_period(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=180)
        windows = list(iter_query_windows(start, end))
        self.assertGreaterEqual(len(windows), 2)
        for w_start, w_end in windows:
            self.assertLessEqual(w_end - w_start, timedelta(days=MAX_QUERY_WINDOW_DAYS))

    def test_max_window_never_exceeds_limit(self):
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2025, 6, 1, tzinfo=timezone.utc)
        for w_start, w_end in iter_query_windows(start, end):
            delta_days = (w_end - w_start).total_seconds() / 86400
            self.assertLessEqual(delta_days, MAX_QUERY_WINDOW_DAYS)


class SyncPeriodTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sync", password="test")
        self.account = ExchangeAccount.objects.create(user=self.user, name="A")

    def test_initial_sync_uses_safe_history(self):
        service = SyncService(self.account, SyncLog.MODE_MANUAL)
        period_from, period_to = service._resolve_period()
        expected_earliest = period_to - timedelta(days=SAFE_HISTORY_DAYS)
        self.assertAlmostEqual(
            period_from.timestamp(),
            expected_earliest.timestamp(),
            delta=5,
        )

    def test_backfill_starts_from_safe_history(self):
        service = SyncService(self.account, SyncLog.MODE_BACKFILL)
        period_from, period_to = service._resolve_period()
        expected_earliest = period_to - timedelta(days=SAFE_HISTORY_DAYS)
        self.assertAlmostEqual(
            period_from.timestamp(),
            expected_earliest.timestamp(),
            delta=5,
        )


class PaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sync2", password="test")
        self.account = ExchangeAccount.objects.create(user=self.user, name="B")
        self.service = SyncService(self.account, SyncLog.MODE_MANUAL)
        self.log = SyncLog.objects.create(
            exchange_account=self.account,
            status=SyncLog.STATUS_RUNNING,
            mode=SyncLog.MODE_MANUAL,
        )

    def _make_item(self, order_id: str):
        return {"id": order_id, "side": 0, "amount": "100", "price": "90", "notifyTokenQuantity": "1"}

    @patch.object(SyncService, "_upsert_raw_order")
    def test_stops_on_duplicate_page(self, upsert_mock):
        same_page = [self._make_item(str(i)) for i in range(30)]
        self.service.client = MagicMock()
        self.service.client.get_orders.side_effect = [
            {"result": {"count": 60, "items": same_page}},
            {"result": {"count": 60, "items": same_page}},
        ]
        self.service.client.page_sleep = MagicMock()

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        self.service._sync_single_window(self.log, start, end)

        self.assertEqual(self.service.client.get_orders.call_count, 2)
        self.assertEqual(upsert_mock.call_count, 30)

    @patch.object(SyncService, "_upsert_raw_order")
    def test_stops_when_count_reached(self, upsert_mock):
        self.service.client = MagicMock()
        self.service.client.get_orders.return_value = {
            "result": {"count": 1, "items": [self._make_item("99")]},
        }
        self.service.client.page_sleep = MagicMock()

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        self.service._sync_single_window(self.log, start, end)

        self.assertEqual(self.service.client.get_orders.call_count, 1)
        upsert_mock.assert_called_once()

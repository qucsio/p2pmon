from datetime import datetime, timedelta, timezone

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

    def test_chunks_at_89_days(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=180)
        windows = list(iter_query_windows(start, end))
        self.assertEqual(len(windows), 3)
        for w_start, w_end in windows:
            self.assertLessEqual(w_end - w_start, timedelta(days=MAX_QUERY_WINDOW_DAYS))

    def test_max_window_never_exceeds_89_days(self):
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

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as dj_timezone

from apps.exchange.models import ExchangeAccount
from apps.exports.services.orders_display import export_orders_display
from apps.ledger.models import LedgerEvent
from apps.ledger.services.engine import LedgerEngine
from apps.ledger.services.events import build_events
from apps.orders.models import IgnoredOrderRule, P2POrder, RawP2POrder
from apps.orders.services.ignore_rules import LEDGER_START_REASON, apply_ignore_rules
from apps.orders.services.normalizer import normalize_account_orders, normalize_raw_order

User = get_user_model()
MOSCOW = ZoneInfo("Europe/Moscow")


class IgnoreRulesTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ignore", password="test")
        self.account = ExchangeAccount.objects.create(user=self.user, name="A")

    def _raw_payload(self, order_id: str, ts_ms: int, side: int = 0) -> dict:
        return {
            "id": order_id,
            "side": side,
            "amount": "1000.00",
            "price": "80.00",
            "notifyTokenQuantity": "12.5",
            "createDate": str(ts_ms),
            "status": 50,
            "tokenId": "USDT",
            "currencyId": "RUB",
            "fee": "0",
        }

    def _create_raw(self, order_id: str, ts_ms: int, side: int = 0) -> RawP2POrder:
        return RawP2POrder.objects.create(
            exchange_account=self.account,
            bybit_order_id=order_id,
            raw_list_payload=self._raw_payload(order_id, ts_ms, side),
        )

    def _ts(self, year, month, day, hour=12) -> int:
        dt = datetime(year, month, day, hour, 0, tzinfo=MOSCOW)
        return int(dt.timestamp() * 1000)

    def test_legacy_blacklist_applies_to_loaded_order(self):
        raw = self._create_raw("blacklisted-1", self._ts(2026, 3, 1))
        normalize_account_orders(self.account, rebuild_ledger=False)

        IgnoredOrderRule.objects.create(
            exchange_account=self.account,
            bybit_order_id="blacklisted-1",
            reason="Legacy orders from previous system",
        )
        apply_ignore_rules(self.account)

        order = P2POrder.objects.get(bybit_order_id="blacklisted-1")
        self.assertFalse(order.include_in_ledger)
        self.assertFalse(order.show_in_orders)
        self.assertFalse(order.show_in_export)
        self.assertEqual(order.ignore_reason, "Legacy orders from previous system")
        self.assertTrue(RawP2POrder.objects.filter(bybit_order_id="blacklisted-1").exists())

    def test_legacy_blacklist_pending_until_sync(self):
        IgnoredOrderRule.objects.create(
            exchange_account=self.account,
            bybit_order_id="pending-1",
            reason="Legacy orders from previous system",
        )
        apply_ignore_rules(self.account)
        rule = IgnoredOrderRule.objects.get(bybit_order_id="pending-1")
        self.assertIsNone(rule.applied_at)

        raw = self._create_raw("pending-1", self._ts(2026, 3, 2))
        normalize_account_orders(self.account, rebuild_ledger=False)

        order = P2POrder.objects.get(bybit_order_id="pending-1")
        rule.refresh_from_db()
        self.assertFalse(order.include_in_ledger)
        self.assertIsNotNone(rule.applied_at)

    def test_ledger_start_ignores_only_older_orders(self):
        old_raw = self._create_raw("old-1", self._ts(2026, 1, 10))
        new_raw = self._create_raw("new-1", self._ts(2026, 3, 10))
        normalize_account_orders(self.account, rebuild_ledger=False)

        boundary = P2POrder.objects.get(bybit_order_id="new-1").created_at_moscow
        self.account.ledger_start_at = boundary
        self.account.ledger_start_inclusive = False
        self.account.save()
        apply_ignore_rules(self.account)

        old = P2POrder.objects.get(bybit_order_id="old-1")
        new = P2POrder.objects.get(bybit_order_id="new-1")
        self.assertFalse(old.include_in_ledger)
        self.assertEqual(old.ignore_reason, LEDGER_START_REASON)
        self.assertTrue(new.include_in_ledger)

    def test_ledger_start_inclusive_ignores_boundary_order(self):
        raw = self._create_raw("boundary-1", self._ts(2026, 2, 15))
        normalize_account_orders(self.account, rebuild_ledger=False)
        order = P2POrder.objects.get(bybit_order_id="boundary-1")

        self.account.ledger_start_at = order.created_at_moscow
        self.account.ledger_start_inclusive = True
        self.account.save()
        apply_ignore_rules(self.account)

        order.refresh_from_db()
        self.assertFalse(order.include_in_ledger)

    def test_ignored_orders_excluded_from_ledger_and_export(self):
        raw = self._create_raw("ledger-excl-1", self._ts(2026, 4, 1))
        normalize_account_orders(self.account, rebuild_ledger=False)

        order = P2POrder.objects.get(bybit_order_id="ledger-excl-1")
        order.include_in_ledger = False
        order.show_in_orders = False
        order.show_in_export = False
        order.save()

        events = build_events(self.account)
        self.assertEqual(len(events), 0)

        output = export_orders_display(self.account)
        with __import__("zipfile").ZipFile(output) as zf:
            strings = zf.read("xl/sharedStrings.xml").decode("utf-8")
        self.assertNotIn("ledger-excl-1", strings)

    def test_raw_orders_never_skipped_during_normalize(self):
        raw_old = self._create_raw("keep-raw-1", self._ts(2025, 12, 1))
        raw_new = self._create_raw("keep-raw-2", self._ts(2026, 5, 1))
        self.account.ledger_start_at = datetime(2026, 1, 1, tzinfo=MOSCOW)
        self.account.save()

        normalize_account_orders(self.account, rebuild_ledger=False)

        self.assertEqual(RawP2POrder.objects.filter(exchange_account=self.account).count(), 2)
        self.assertEqual(P2POrder.objects.filter(exchange_account=self.account).count(), 2)
        self.assertTrue(P2POrder.objects.get(bybit_order_id="keep-raw-1").ignore_reason)

    def test_id_rule_overrides_ledger_start_reason(self):
        raw = self._create_raw("override-1", self._ts(2025, 11, 1))
        normalize_account_orders(self.account, rebuild_ledger=False)

        self.account.ledger_start_at = datetime(2026, 12, 31, tzinfo=MOSCOW)
        self.account.save()
        IgnoredOrderRule.objects.create(
            exchange_account=self.account,
            bybit_order_id="override-1",
            reason="Legacy blacklist",
        )
        apply_ignore_rules(self.account)

        order = P2POrder.objects.get(bybit_order_id="override-1")
        self.assertEqual(order.ignore_reason, "Legacy blacklist")

    def test_import_legacy_blacklist_command_summary(self):
        raw = self._create_raw("cmd-1", self._ts(2026, 1, 5))
        normalize_raw_order(raw)

        out = call_command(
            "import_legacy_blacklist",
            account_id=self.account.id,
            ids="cmd-1,cmd-pending",
            reason="Legacy orders from previous system",
        )
        self.assertIsNone(out)

        order = P2POrder.objects.get(bybit_order_id="cmd-1")
        self.assertFalse(order.include_in_ledger)
        self.assertEqual(
            IgnoredOrderRule.objects.filter(exchange_account=self.account).count(),
            2,
        )
        self.assertTrue(
            IgnoredOrderRule.objects.filter(bybit_order_id="cmd-pending", applied_at__isnull=True).exists()
        )

    def test_set_ledger_start_from_order_command(self):
        old_raw = self._create_raw("before-1", self._ts(2026, 1, 1))
        boundary_raw = self._create_raw("boundary-order", self._ts(2026, 2, 1))
        normalize_account_orders(self.account, rebuild_ledger=False)

        call_command(
            "set_ledger_start_from_order",
            account_id=self.account.id,
            order_id="boundary-order",
            include_boundary=True,
        )

        self.assertFalse(P2POrder.objects.get(bybit_order_id="before-1").include_in_ledger)
        self.assertTrue(P2POrder.objects.get(bybit_order_id="boundary-order").include_in_ledger)

    def test_set_ledger_start_date_command(self):
        raw = self._create_raw("date-old", self._ts(2026, 1, 20))
        normalize_account_orders(self.account, rebuild_ledger=False)

        call_command(
            "set_ledger_start_date",
            account_id=self.account.id,
            date="2026-02-01 00:00",
        )

        order = P2POrder.objects.get(bybit_order_id="date-old")
        self.assertFalse(order.include_in_ledger)
        self.assertEqual(LedgerEvent.objects.filter(exchange_account=self.account).count(), 0)

    def test_normalize_rebuilds_ledger_when_enabled(self):
        raw = self._create_raw("rebuild-1", self._ts(2026, 6, 1))
        normalize_account_orders(self.account, rebuild_ledger=False)
        order = P2POrder.objects.get(bybit_order_id="rebuild-1")
        order.include_in_ledger = False
        order.save()

        normalize_account_orders(self.account, rebuild_ledger=False)
        LedgerEngine(self.account).rebuild()
        self.assertEqual(
            LedgerEvent.objects.filter(exchange_account=self.account).count(),
            0,
        )

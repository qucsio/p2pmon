from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime
from zoneinfo import ZoneInfo

from apps.common.decimal_utils import q_rub, q_usdt
from apps.exchange.models import ExchangeAccount
from apps.investors.models import Investor
from apps.ledger.models import LedgerAdjustment, LedgerEvent
from apps.ledger.services.engine import LedgerEngine
from apps.orders.models import P2POrder, RawP2POrder


User = get_user_model()
MSK = ZoneInfo("Europe/Moscow")


class LedgerEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("test", password="test")
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            name="Test",
        )
        self.account.set_api_credentials("key", "secret")
        self.account.save()

    def _create_order(self, side, rub, qty, price, fee=0, fee_currency="USDT", day_offset=0, at=None):
        raw = RawP2POrder.objects.create(
            exchange_account=self.account,
            bybit_order_id=f"order-{RawP2POrder.objects.count() + 1}",
            raw_list_payload={"id": "x", "side": 0 if side == "BUY" else 1},
        )
        now = at or (timezone.now() + timezone.timedelta(days=day_offset))
        return P2POrder.objects.create(
            exchange_account=self.account,
            raw_order=raw,
            bybit_order_id=raw.bybit_order_id,
            side=side,
            bybit_side=0 if side == "BUY" else 1,
            status=50,
            price=Decimal(str(price)),
            quantity_gross=Decimal(str(qty)),
            quantity_net=Decimal(str(qty)),
            amount_gross=Decimal(str(rub)),
            amount_net=Decimal(str(rub)),
            amount_rub=Decimal(str(rub)),
            fee_amount=Decimal(str(fee)),
            fee_currency=fee_currency,
            created_at_bybit_raw_ms=int(now.timestamp() * 1000),
            created_at_utc=now,
            created_at_moscow=now,
        )

    def test_wac_buy_then_sell(self):
        self._create_order("BUY", rub=10000, qty=100, price=100)
        self._create_order("SELL", rub=11000, qty=100, price=110)
        LedgerEngine(self.account).rebuild()
        snap = self.account.daily_snapshots.order_by("-day").first()
        self.assertIsNotNone(snap)
        self.assertGreater(snap.daily_wac_realized_pnl, 0)

    def test_adjustment_deposit(self):
        LedgerAdjustment.objects.create(
            exchange_account=self.account,
            account=LedgerAdjustment.ACCOUNT_BANK,
            type=LedgerAdjustment.TYPE_DEPOSIT,
            currency="RUB",
            amount_rub=Decimal("50000"),
            amount_usdt=Decimal("0"),
            effective_at=timezone.now(),
        )
        LedgerEngine(self.account).rebuild()
        state = LedgerEngine(self.account).get_current_state()
        self.assertEqual(state.bank, Decimal("50000.00"))

    def test_exchange_adjustment_preserves_usdt_precision(self):
        LedgerAdjustment.objects.create(
            exchange_account=self.account,
            account=LedgerAdjustment.ACCOUNT_EXCHANGE,
            type=LedgerAdjustment.TYPE_CORRECTION,
            currency="USDT",
            amount_rub=Decimal("0"),
            amount_usdt=Decimal("23.543210"),
            effective_at=timezone.now(),
        )
        adj = LedgerAdjustment.objects.get(exchange_account=self.account)
        self.assertEqual(adj.amount_usdt, q_usdt("23.543210"))
        LedgerEngine(self.account).rebuild()
        state = LedgerEngine(self.account).get_current_state()
        self.assertEqual(state.exchange, q_usdt("23.543210"))

    def test_running_wac_pnl_uses_day_eod_not_final(self):
        self._create_order("BUY", rub=10000, qty=100, price=100, day_offset=0)
        self._create_order("SELL", rub=11000, qty=50, price=110, day_offset=1)
        LedgerEngine(self.account).rebuild()
        snaps = list(self.account.daily_snapshots.order_by("day"))
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0].running_wac_pnl, Decimal("0.00"))
        self.assertGreater(snaps[1].running_wac_pnl, snaps[0].running_wac_pnl)

    def test_deterministic_rebuild(self):
        self._create_order("BUY", rub=5000, qty=50, price=100)
        LedgerEngine(self.account).rebuild()
        e1 = list(LedgerEvent.objects.filter(exchange_account=self.account).values_list("amount_rub", flat=True))
        LedgerEngine(self.account).rebuild()
        e2 = list(LedgerEvent.objects.filter(exchange_account=self.account).values_list("amount_rub", flat=True))
        self.assertEqual(e1, e2)

    def test_last_price_carries_forward_on_adjustment_only_day(self):
        day1 = datetime(2026, 5, 30, 12, 0, tzinfo=MSK)
        day2 = datetime(2026, 5, 31, 12, 0, tzinfo=MSK)

        self._create_order("BUY", rub=7600, qty=100, price=76, at=day1)
        LedgerAdjustment.objects.create(
            exchange_account=self.account,
            account=LedgerAdjustment.ACCOUNT_BANK,
            type=LedgerAdjustment.TYPE_WITHDRAWAL,
            currency="RUB",
            amount_rub=Decimal("1000"),
            amount_usdt=Decimal("0"),
            effective_at=day2,
        )

        LedgerEngine(self.account).rebuild()
        snaps = list(self.account.daily_snapshots.order_by("day"))
        self.assertEqual(len(snaps), 2)

        day1_snap, day2_snap = snaps
        self.assertEqual(day1_snap.last_price, Decimal("76.000000"))
        self.assertEqual(day2_snap.last_price, Decimal("76.000000"))

        expected_equity = q_rub(
            day2_snap.bank_balance + q_rub(day2_snap.exchange_balance * Decimal("76"))
        )
        self.assertEqual(day2_snap.total_equity, expected_equity)
        self.assertNotEqual(day2_snap.total_equity, day2_snap.bank_balance)

        bogus_unrealized = q_rub(-day2_snap.exchange_balance * day2_snap.wac_price)
        self.assertNotEqual(day2_snap.daily_wac_unrealized_pnl, bogus_unrealized)
        self.assertEqual(day2_snap.daily_wac_unrealized_pnl, Decimal("0.00"))


class InvestorModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("inv", password="test")

    def test_can_create_investors_incrementally(self):
        Investor.objects.create(user=self.user, name="A", share_percent=Decimal("50"))
        Investor.objects.create(user=self.user, name="B", share_percent=Decimal("50"))

    def test_rejects_shares_over_100(self):
        Investor.objects.create(user=self.user, name="A", share_percent=Decimal("60"))
        with self.assertRaises(Exception):
            Investor.objects.create(user=self.user, name="B", share_percent=Decimal("50"))


class NormalizerTests(TestCase):
    def test_moscow_timezone_conversion(self):
        from apps.orders.services.normalizer import MOSCOW
        from datetime import datetime, timezone

        ts_ms = 1760450652000
        utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        msk = utc.astimezone(MOSCOW)
        self.assertEqual(msk.utcoffset().total_seconds(), 3 * 3600)

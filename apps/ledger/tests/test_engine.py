from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.ledger.models import LedgerAdjustment, LedgerEvent
from apps.ledger.services.engine import LedgerEngine, LedgerState
from apps.orders.models import P2POrder, RawP2POrder


User = get_user_model()


class LedgerEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("test", password="test")
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            name="Test",
        )
        self.account.set_api_credentials("key", "secret")
        self.account.save()

    def _create_order(self, side, rub, qty, price, fee=0, fee_currency="USDT"):
        raw = RawP2POrder.objects.create(
            exchange_account=self.account,
            bybit_order_id=f"order-{RawP2POrder.objects.count() + 1}",
            raw_list_payload={"id": "x", "side": 0 if side == "BUY" else 1},
        )
        now = timezone.now()
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
            amount=Decimal("50000"),
            effective_at=timezone.now(),
        )
        LedgerEngine(self.account).rebuild()
        state = LedgerEngine(self.account).get_current_state()
        self.assertEqual(state.bank, Decimal("50000.00"))

    def test_deterministic_rebuild(self):
        self._create_order("BUY", rub=5000, qty=50, price=100)
        LedgerEngine(self.account).rebuild()
        e1 = list(LedgerEvent.objects.filter(exchange_account=self.account).values_list("amount_rub", flat=True))
        LedgerEngine(self.account).rebuild()
        e2 = list(LedgerEvent.objects.filter(exchange_account=self.account).values_list("amount_rub", flat=True))
        self.assertEqual(e1, e2)


class NormalizerTests(TestCase):
    def test_moscow_timezone_conversion(self):
        from apps.orders.services.normalizer import MOSCOW
        from datetime import datetime, timezone

        ts_ms = 1760450652000
        utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        msk = utc.astimezone(MOSCOW)
        self.assertEqual(msk.utcoffset().total_seconds(), 3 * 3600)

from datetime import datetime, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.exchange.models import ExchangeAccount
from apps.orders.models import P2POrder, RawP2POrder
from apps.orders.services.normalizer import normalize_raw_order

User = get_user_model()


class NormalizerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("norm", password="test")
        self.account = ExchangeAccount.objects.create(user=self.user, name="A")

    def test_normalize_new_order_sets_quantity_net(self):
        raw = RawP2POrder.objects.create(
            exchange_account=self.account,
            bybit_order_id="1234567890123456789",
            raw_list_payload={
                "id": "1234567890123456789",
                "side": 0,
                "amount": "10000.00",
                "price": "90.00",
                "notifyTokenQuantity": "111.111111",
                "createDate": "1741769000000",
                "status": 50,
                "tokenId": "USDT",
                "currencyId": "RUB",
                "fee": "0",
                "sellerRealName": "Test Seller",
            },
        )

        order = normalize_raw_order(raw)

        self.assertIsNotNone(order.quantity_net)
        self.assertEqual(order.quantity_net, order.quantity_gross)
        self.assertEqual(P2POrder.objects.filter(exchange_account=self.account).count(), 1)

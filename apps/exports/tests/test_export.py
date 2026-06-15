from io import BytesIO
import zipfile
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.exchange.models import ExchangeAccount
from apps.exports.services.orders_display import export_orders_display
from apps.orders.models import P2POrder, RawP2POrder
from django.utils import timezone


User = get_user_model()


class ExportTests(TestCase):
    def test_respects_show_in_export_flag(self):
        user = User.objects.create_user("u", password="p")
        account = ExchangeAccount.objects.create(user=user, name="A")
        account.set_api_credentials("k", "s")
        account.save()
        now = timezone.now()

        for i, show in enumerate([True, False]):
            raw = RawP2POrder.objects.create(
                exchange_account=account,
                bybit_order_id=f"id-{i}",
                raw_list_payload={},
            )
            P2POrder.objects.create(
                exchange_account=account,
                raw_order=raw,
                bybit_order_id=raw.bybit_order_id,
                side="BUY",
                bybit_side=0,
                status=50,
                price=Decimal("80"),
                quantity_gross=Decimal("10"),
                quantity_net=Decimal("10"),
                amount_rub=Decimal("800"),
                created_at_bybit_raw_ms=1,
                created_at_utc=now,
                created_at_moscow=now,
                show_in_export=show,
            )

        output = export_orders_display(account)
        self.assertIsInstance(output, BytesIO)
        with zipfile.ZipFile(output) as zf:
            strings = zf.read("xl/sharedStrings.xml").decode("utf-8")
            sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Order ID", strings)
        self.assertEqual(sheet.count("<row "), 2)
        self.assertIn("id-0", strings)
        self.assertNotIn("id-1", strings)

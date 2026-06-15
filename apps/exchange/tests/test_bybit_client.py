import unittest
from unittest.mock import patch

from apps.exchange.services.bybit_client import BybitClient


class BybitClientErrorTests(unittest.TestCase):
    def test_api_error_with_ret_code_only(self):
        class FakeApi:
            def get_orders(self, **kwargs):
                return {"ret_code": 10001, "ret_msg": "Invalid API key"}

        client = BybitClient.__new__(BybitClient)
        client._api = FakeApi()
        with self.assertRaises(RuntimeError):
            client._call_with_retry(client._api.get_orders, page=1, size=1)

    @patch("apps.exchange.services.bybit_client.time.sleep")
    def test_success_when_ret_code_zero(self, _sleep):
        class FakeApi:
            def get_orders(self, **kwargs):
                return {"ret_code": 0, "result": {"items": []}}

        client = BybitClient.__new__(BybitClient)
        client._api = FakeApi()
        result = client._call_with_retry(client._api.get_orders, page=1, size=1)
        self.assertEqual(result["ret_code"], 0)

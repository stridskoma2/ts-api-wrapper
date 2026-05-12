from __future__ import annotations

import unittest
from decimal import Decimal

from tests.helpers import FakeTokenProvider, FakeTransport, json_response, sim_config
from tests.unit.test_models_and_validation import limit_order
from tradestation_api_wrapper.client import TradeStationClient
from tradestation_api_wrapper.transport import NetworkTimeout


class ClientFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_place_order_returns_trade_object(self) -> None:
        transport = FakeTransport([json_response(200, {"OrderID": "123"})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        trade = await client.place_order(limit_order())

        self.assertEqual(trade.order_id, "123")
        self.assertEqual(trade.ack.order_id, "123")  # type: ignore[union-attr]
        self.assertIn("AccountID", trade.payload)

    async def test_place_order_returns_ambiguous_trade_on_timeout(self) -> None:
        transport = FakeTransport([NetworkTimeout("timeout")])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        trade = await client.place_order(limit_order())

        self.assertTrue(trade.is_ambiguous)
        self.assertTrue(trade.reconcile_required)

    async def test_what_if_order_uses_confirmation_endpoint(self) -> None:
        transport = FakeTransport([json_response(200, {"OrderConfirmID": "confirm"})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        confirmation = await client.what_if_order(limit_order())

        self.assertEqual(confirmation.order_confirm_id, "confirm")
        self.assertTrue(transport.requests[0].url.endswith("/orderexecution/orderconfirm"))

    async def test_fetch_state_snapshot_collects_core_broker_state(self) -> None:
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {"Accounts": [{"AccountID": "123456789"}, {"AccountID": "987654321"}]},
                ),
                json_response(200, {"Balances": [{"AccountID": "123456789", "BuyingPower": "100"}]}),
                json_response(
                    200,
                    {"Positions": [{"AccountID": "123456789", "Symbol": "MSFT", "Quantity": "0"}]},
                ),
                json_response(200, {"Orders": [{"OrderID": "1", "Status": "OPN"}]}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        snapshot = await client.fetch_state_snapshot(("123456789",))

        self.assertEqual(len(snapshot.accounts), 1)
        self.assertEqual(snapshot.accounts[0].account_id, "123456789")
        self.assertEqual(len(snapshot.open_orders), 1)
        self.assertEqual(snapshot.nonzero_positions, ())

    async def test_market_data_helpers_parse_quotes_symbols_and_bars(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Quotes": [{"Symbol": "MSFT", "Bid": "10", "Ask": "11"}]}),
                json_response(200, {"Symbols": [{"Symbol": "MSFT", "AssetType": "STOCK"}]}),
                json_response(200, {"Bars": [{"Close": "10", "TotalVolume": "20"}]}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        quotes = await client.get_quotes(("MSFT",))
        symbols = await client.get_symbols(("MSFT",))
        bars = await client.get_bars("MSFT")

        self.assertEqual(quotes[0].midpoint, Decimal("10.5"))
        self.assertEqual(symbols[0].asset_type, "STOCK")
        self.assertEqual(bars[0].close, Decimal("10"))

    async def test_market_data_paths_url_encode_symbols(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Quotes": []}),
                json_response(200, {"Bars": []}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        await client.get_quotes(("A/B",))
        await client.get_bars("A/B")

        self.assertTrue(transport.requests[0].url.endswith("/marketdata/quotes/A%2FB"))
        self.assertTrue(transport.requests[1].url.endswith("/marketdata/barcharts/A%2FB"))


if __name__ == "__main__":
    unittest.main()

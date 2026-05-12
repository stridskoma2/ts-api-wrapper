from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from tests.helpers import FakeTokenProvider, FakeTransport, json_response, sim_config
from tests.unit.test_models_and_validation import limit_order
from tradestation_api_wrapper.capabilities import TradeStationCapabilities
from tradestation_api_wrapper.client import TradeStationClient
from tradestation_api_wrapper.errors import CapabilityError
from tradestation_api_wrapper.models import GroupOrderRequest, GroupType, OrderReplaceRequest
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
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "Confirmations": [
                            {"OrderConfirmID": "confirm", "EstimatedCost": "12.34"},
                        ],
                        "Errors": [],
                    },
                )
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        confirmation = await client.what_if_order(limit_order())

        self.assertEqual(confirmation.order_confirm_id, "confirm")
        self.assertEqual(confirmation.estimated_cost, Decimal("12.34"))
        self.assertEqual(len(confirmation.confirmations), 1)
        self.assertTrue(transport.requests[0].url.endswith("/orderexecution/orderconfirm"))

    async def test_flat_confirmation_response_stays_backward_compatible(self) -> None:
        transport = FakeTransport([json_response(200, {"OrderConfirmID": "confirm"})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        confirmation = await client.what_if_order(limit_order())

        self.assertEqual(confirmation.order_confirm_id, "confirm")

    async def test_fetch_state_snapshot_collects_core_broker_state(self) -> None:
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {"Accounts": [{"AccountID": "123456789"}, {"AccountID": "987654321"}]},
                ),
                json_response(
                    200,
                    {"Balances": [{"AccountID": "123456789", "BuyingPower": "100"}]},
                ),
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

    async def test_get_orders_follows_next_token_pages(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Orders": [{"OrderID": "1"}], "NextToken": "next page"}),
                json_response(200, {"Orders": [{"OrderID": "2"}]}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        orders = await client.get_orders(("123456789",), page_size=1)

        self.assertEqual([order.order_id for order in orders], ["1", "2"])
        self.assertIn("pageSize=1", transport.requests[0].url)
        self.assertIn("nextToken=next+page", transport.requests[1].url)

    async def test_get_historical_orders_uses_since_and_page_size(self) -> None:
        transport = FakeTransport([json_response(200, {"Orders": [{"OrderID": "1"}]})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        await client.get_historical_orders(
            ("123456789",),
            since=datetime(2026, 5, 9, tzinfo=UTC),
            page_size=10,
        )

        self.assertIn("since=2026-05-09T00%3A00%3A00%2B00%3A00", transport.requests[0].url)
        self.assertIn("pageSize=10", transport.requests[0].url)

    async def test_replace_order_sends_replace_payload_only(self) -> None:
        transport = FakeTransport([json_response(200, {"OrderID": "123"})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        trade = await client.replace_order("123", replacement)

        self.assertEqual(trade.order_id, "123")
        self.assertEqual(transport.requests[0].json_body, {"LimitPrice": "11", "Quantity": "1"})
        self.assertTrue(transport.requests[0].url.endswith("/orderexecution/orders/123"))

    async def test_replace_order_coerces_legacy_order_request_to_replace_payload(self) -> None:
        transport = FakeTransport([json_response(200, {"OrderID": "123"})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        await client.replace_order("123", limit_order(LimitPrice=Decimal("11")))

        self.assertEqual(transport.requests[0].json_body, {"LimitPrice": "11", "Quantity": "2"})

    async def test_capability_flags_fail_explicitly(self) -> None:
        group = GroupOrderRequest(Type=GroupType.OCO, Orders=(limit_order(), limit_order()))
        client = TradeStationClient(
            sim_config(),
            FakeTokenProvider(),
            transport=FakeTransport([]),
            capabilities=TradeStationCapabilities(supports_oco=False),
        )

        with self.assertRaises(CapabilityError):
            await client.place_order_group(group)

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

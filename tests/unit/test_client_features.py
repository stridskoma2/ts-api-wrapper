from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

import tradestation_api_wrapper.client as client_module
from tests.helpers import FakeTokenProvider, FakeTransport, json_response, sim_config
from tests.unit.test_models_and_validation import limit_order
from tradestation_api_wrapper.capabilities import TradeStationCapabilities
from tradestation_api_wrapper.client import TradeStationClient
from tradestation_api_wrapper.errors import (
    CapabilityError,
    ConfigurationError,
    NetworkTimeout,
    PaginationError,
    RequestValidationError,
)
from tradestation_api_wrapper.models import (
    BarChartParams,
    BarSessionTemplate,
    BarUnit,
    GroupOrderRequest,
    GroupType,
    OptionChainStreamParams,
    OptionSpreadTypeName,
    OptionType,
    OptionQuoteLeg,
    OptionRiskRewardLeg,
    OptionRiskRewardRequest,
    OrderReplaceRequest,
    StreamBarChartParams,
    StrikeRange,
    TradeAction,
)
from tradestation_api_wrapper.rest import BROKERAGE_STREAM_ACCEPT, MARKET_DATA_STREAM_ACCEPT


class CloseTrackingTransport(FakeTransport):
    closed = False

    async def aclose(self) -> None:
        self.closed = True


class ClientFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_context_manager_closes_transport(self) -> None:
        transport = CloseTrackingTransport([])

        async with TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport):
            pass

        self.assertTrue(transport.closed)

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

    async def test_get_orders_stops_nonterminating_pagination(self) -> None:
        original_limit = client_module.MAX_ORDER_PAGES
        client_module.MAX_ORDER_PAGES = 2
        try:
            transport = FakeTransport(
                [
                    json_response(200, {"Orders": [], "NextToken": "same"}),
                    json_response(200, {"Orders": [], "NextToken": "same"}),
                ]
            )
            client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

            with self.assertRaises(PaginationError):
                await client.get_orders(("123456789",))
        finally:
            client_module.MAX_ORDER_PAGES = original_limit

    async def test_brokerage_gap_endpoints_build_spec_paths(self) -> None:
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "BODBalances": [
                            {"AccountID": "123456789", "BalanceDetail": {"NetCash": "100"}}
                        ]
                    },
                ),
                json_response(
                    200,
                    {"Positions": [{"AccountID": "123456789", "Symbol": "MSFT", "Quantity": "1"}]},
                ),
                json_response(200, {"Orders": [{"OrderID": "1"}]}),
                json_response(200, {"Orders": [{"OrderID": "2"}]}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        bod_balances = await client.get_bod_balances(("123456789",))
        positions = await client.get_positions(("123456789",), symbols=("MSFT", "MSFT *"))
        orders = await client.get_orders_by_id(("123456789",), ("1",))
        historical = await client.get_historical_orders_by_id(
            ("123456789",),
            ("2",),
            since=datetime(2026, 5, 9, tzinfo=UTC),
        )

        bod_balance_detail = bod_balances[0].balance_detail
        assert bod_balance_detail is not None
        self.assertEqual(bod_balance_detail.net_cash, Decimal("100"))
        self.assertEqual(positions[0].symbol, "MSFT")
        self.assertEqual(orders[0].order_id, "1")
        self.assertEqual(historical[0].order_id, "2")
        self.assertTrue(
            transport.requests[0].url.endswith("/brokerage/accounts/123456789/bodbalances")
        )
        self.assertIn("symbol=MSFT%2CMSFT+%2A", transport.requests[1].url)
        self.assertTrue(
            transport.requests[2].url.endswith("/brokerage/accounts/123456789/orders/1")
        )
        self.assertIn(
            "/brokerage/accounts/123456789/historicalorders/2?",
            transport.requests[3].url,
        )

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
        transport = FakeTransport(
            [
                json_response(200, {"Orders": [{"AccountID": "123456789", "OrderID": "123"}]}),
                json_response(200, {"OrderID": "123"}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        trade = await client.replace_order("123456789", "123", replacement)

        self.assertEqual(trade.order_id, "123")
        self.assertTrue(
            transport.requests[0].url.endswith("/brokerage/accounts/123456789/orders/123")
        )
        self.assertEqual(transport.requests[1].json_body, {"LimitPrice": "11", "Quantity": "1"})
        self.assertTrue(transport.requests[1].url.endswith("/orderexecution/orders/123"))

    async def test_replace_order_coerces_legacy_order_request_to_replace_payload(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Orders": [{"AccountID": "123456789", "OrderID": "123"}]}),
                json_response(200, {"OrderID": "123"}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        await client.replace_order("123456789", "123", limit_order(LimitPrice=Decimal("11")))

        self.assertEqual(transport.requests[1].json_body, {"LimitPrice": "11", "Quantity": "2"})

    async def test_replace_order_rejects_account_mismatch_before_write(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Orders": [{"AccountID": "987654321", "OrderID": "123"}]}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        with self.assertRaises(RequestValidationError):
            await client.replace_order("123456789", "123", replacement)

        self.assertEqual(len(transport.requests), 1)

    async def test_replace_order_preflights_read_account_scope(self) -> None:
        client = TradeStationClient(
            sim_config(requested_scopes=("openid", "offline_access", "Trade")),
            FakeTokenProvider(),
            transport=FakeTransport([]),
        )
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        with self.assertRaises(ConfigurationError):
            await client.replace_order("123456789", "123", replacement)

    async def test_cancel_order_validates_account_allowlist(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"Orders": [{"AccountID": "123456789", "OrderID": "123"}]}),
                json_response(200, {"OrderID": "123"}),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        response = await client.cancel_order("123456789", "123")

        self.assertEqual(response["OrderID"], "123")
        self.assertTrue(
            transport.requests[0].url.endswith("/brokerage/accounts/123456789/orders/123")
        )
        self.assertTrue(transport.requests[1].url.endswith("/orderexecution/orders/123"))

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
        bars = await client.get_bars("MSFT", params=BarChartParams(unit=BarUnit.DAILY))

        self.assertEqual(quotes[0].midpoint, Decimal("10.5"))
        self.assertEqual(symbols[0].asset_type, "STOCK")
        self.assertEqual(bars[0].close, Decimal("10"))

    async def test_scope_preflight_blocks_unrequested_market_data(self) -> None:
        client = TradeStationClient(
            sim_config(requested_scopes=("openid", "offline_access", "ReadAccount", "Trade")),
            FakeTokenProvider(),
            transport=FakeTransport([]),
        )

        with self.assertRaises(ConfigurationError):
            await client.get_quotes(("MSFT",))

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

    async def test_bar_query_params_use_pinned_spec_aliases(self) -> None:
        transport = FakeTransport([json_response(200, {"Bars": []})])
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        await client.get_bars(
            "MSFT",
            params=BarChartParams(
                unit=BarUnit.MINUTE,
                interval=5,
                barsback=20,
                sessiontemplate=BarSessionTemplate.USEQ_PRE,
            ),
        )

        url = transport.requests[0].url
        self.assertIn("interval=5", url)
        self.assertIn("unit=Minute", url)
        self.assertIn("barsback=20", url)
        self.assertIn("sessiontemplate=USEQPre", url)
        self.assertNotIn("barsBack", url)
        self.assertNotIn("sessionTemplate", url)

    async def test_market_data_gap_endpoints_parse_typed_payloads(self) -> None:
        transport = FakeTransport(
            [
                json_response(200, {"SymbolNames": ["BTCUSD"]}),
                json_response(
                    200,
                    {"Expirations": [{"Date": "2026-06-19T00:00:00Z", "Root": "MSFT"}]},
                ),
                json_response(
                    200,
                    {"SpreadTypes": [{"Name": "Vertical", "StrikeInterval": True}]},
                ),
                json_response(
                    200,
                    {"SpreadType": "Vertical", "Strikes": [["100", "105"]]},
                ),
                json_response(
                    200,
                    {
                        "AdjustedMaxGain": "482",
                        "AdjustedMaxLoss": "-18",
                        "BreakevenPoints": ["150.09"],
                    },
                ),
            ]
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        crypto_symbols = await client.get_crypto_symbol_names()
        expirations = await client.get_option_expirations("MSFT", strike_price=Decimal("100"))
        spread_types = await client.get_option_spread_types()
        strikes = await client.get_option_strikes("MSFT", spread_type="Vertical")
        risk_reward = await client.get_option_risk_reward(
            OptionRiskRewardRequest(
                SpreadPrice=Decimal("0.24"),
                Legs=(
                    OptionRiskRewardLeg(
                        Symbol="MSFT 260619C100",
                        Quantity=Decimal("1"),
                        TradeAction=TradeAction.BUY,
                    ),
                ),
            )
        )

        self.assertEqual(crypto_symbols, ("BTCUSD",))
        self.assertEqual(expirations[0].root, "MSFT")
        self.assertEqual(spread_types[0].name, "Vertical")
        self.assertEqual(strikes.strikes[0][1], Decimal("105"))
        self.assertEqual(risk_reward.adjusted_max_gain, Decimal("482"))
        self.assertTrue(
            transport.requests[0].url.endswith("/marketdata/symbollists/cryptopairs/symbolnames")
        )
        self.assertIn("strikePrice=100", transport.requests[1].url)
        risk_reward_body = transport.requests[4].json_body
        assert risk_reward_body is not None
        self.assertEqual(risk_reward_body["SpreadPrice"], 0.24)

    async def test_additional_stream_helpers_build_paths(self) -> None:
        transport = FakeTransport(
            [],
            streams=[
                [b'{"OrderID":"1"}'],
                [b'{"Symbol":"MSFT"}'],
                [b'{"Close":"100"}'],
                [b'{"Bid":"100"}'],
                [b'{"Ask":"101"}'],
                [b'{"Symbol":"MSFT 260619C100"}'],
                [b'{"Symbol":"MSFT 260619C100"}'],
            ],
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        for stream in (
            client.stream_orders_by_id(("123456789",), ("1",)),
            client.stream_quotes(("MSFT",)),
            client.stream_bars(
                "MSFT",
                params=StreamBarChartParams(
                    unit=BarUnit.MINUTE,
                    barsback=10,
                    sessiontemplate=BarSessionTemplate.DEFAULT,
                ),
            ),
            client.stream_market_depth_aggregates("MSFT", max_levels=3),
            client.stream_market_depth_quotes("MSFT", max_levels=4),
            client.stream_option_chain(
                "MSFT",
                params=OptionChainStreamParams(
                    spreadType=OptionSpreadTypeName.SINGLE,
                    strikeRange=StrikeRange.ALL,
                    optionType=OptionType.CALL,
                    enableGreeks=True,
                ),
            ),
            client.stream_option_quotes(
                (OptionQuoteLeg(Symbol="MSFT 260619C100", Ratio=Decimal("1")),),
                enable_greeks=True,
            ),
        ):
            async for _event in stream:
                break

        self.assertTrue(transport.requests[0].url.endswith("/orders/1"))
        self.assertEqual(transport.requests[0].headers["Accept"], BROKERAGE_STREAM_ACCEPT)
        self.assertTrue(transport.requests[1].url.endswith("/stream/quotes/MSFT"))
        self.assertIn("unit=Minute", transport.requests[2].url)
        self.assertIn("barsback=10", transport.requests[2].url)
        self.assertIn("sessiontemplate=Default", transport.requests[2].url)
        self.assertIn("maxlevels=3", transport.requests[3].url)
        self.assertIn("maxlevels=4", transport.requests[4].url)
        self.assertIn("spreadType=Single", transport.requests[5].url)
        self.assertIn("strikeRange=All", transport.requests[5].url)
        self.assertIn("optionType=Call", transport.requests[5].url)
        self.assertIn("enableGreeks=true", transport.requests[5].url)
        self.assertIn("legs%5B0%5D.Symbol=MSFT+260619C100", transport.requests[6].url)
        self.assertTrue(
            all(
                request.headers["Accept"] == MARKET_DATA_STREAM_ACCEPT
                for request in transport.requests[1:]
            )
        )

    async def test_stream_quotes_can_yield_nonfatal_error_events(self) -> None:
        transport = FakeTransport(
            [],
            streams=[
                [
                    b'{"Error":"BadSymbol","Message":"bad symbol"}',
                    b'{"Symbol":"MSFT","Bid":"100"}',
                ]
            ],
        )
        client = TradeStationClient(sim_config(), FakeTokenProvider(), transport=transport)

        events = []
        async for event in client.stream_quotes(("MSFT", "BAD"), raise_on_error=False):
            events.append(event)
            if len(events) == 2:
                break

        self.assertEqual(events[0].payload["Error"], "BadSymbol")
        self.assertEqual(events[1].payload["Symbol"], "MSFT")


if __name__ == "__main__":
    unittest.main()

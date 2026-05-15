from __future__ import annotations

import os
import unittest
from decimal import Decimal

from tests.helpers import sim_config
from tradestation_api_wrapper.client import TradeStationClient
from tradestation_api_wrapper.models import (
    AssetClass,
    Duration,
    OrderRequest,
    OrderType,
    TimeInForce,
    TradeAction,
)
from tradestation_api_wrapper.rest import StaticTokenProvider


def sim_trade_enabled() -> bool:
    required = (
        "TRADESTATION_SIM_ACCESS_TOKEN",
        "TRADESTATION_SIM_ACCOUNT_ID",
        "TRADESTATION_SIM_TEST_SYMBOL",
        "TRADESTATION_SIM_TEST_LIMIT_PRICE",
    )
    return os.getenv("TRADESTATION_SIM_TRADE_TESTS") == "1" and all(
        os.getenv(name) for name in required
    )


@unittest.skipUnless(
    sim_trade_enabled(),
    "SIM trade test disabled; set TRADESTATION_SIM_TRADE_TESTS=1 and test order env vars",
)
class SimTradeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_place_and_cancel_tiny_limit_order(self) -> None:
        account_id = os.environ["TRADESTATION_SIM_ACCOUNT_ID"]
        order = OrderRequest(
            AccountID=account_id,
            Symbol=os.environ["TRADESTATION_SIM_TEST_SYMBOL"],
            Quantity=Decimal(os.getenv("TRADESTATION_SIM_TEST_QUANTITY", "1")),
            OrderType=OrderType.LIMIT,
            TradeAction=TradeAction.BUY,
            TimeInForce=TimeInForce(Duration=Duration.DAY),
            LimitPrice=Decimal(os.environ["TRADESTATION_SIM_TEST_LIMIT_PRICE"]),
            asset_class=AssetClass.EQUITY,
        )
        client = TradeStationClient(
            sim_config(
                account_allowlist=(account_id,),
                max_order_notional=Decimal(os.getenv("TRADESTATION_SIM_TEST_MAX_NOTIONAL", "1000")),
            ),
            StaticTokenProvider(os.environ["TRADESTATION_SIM_ACCESS_TOKEN"]),
        )

        confirmation = await client.confirm_order(order)
        self.assertFalse(confirmation.errors)

        trade = await client.place_order(order)
        self.assertFalse(trade.reconcile_required)
        order_id = trade.order_id
        self.assertIsNotNone(order_id)
        if order_id is not None:
            await client.cancel_order(account_id, order_id)


if __name__ == "__main__":
    unittest.main()

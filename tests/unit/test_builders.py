from __future__ import annotations

import unittest
from decimal import Decimal

from tradestation_api_wrapper.builders import bracket_order_group, oco_exit_group, protective_exit_action
from tradestation_api_wrapper.errors import RequestValidationError
from tradestation_api_wrapper.models import (
    AssetClass,
    Duration,
    GroupType,
    OrderType,
    TradeAction,
)
from tradestation_api_wrapper.validation import group_order_payload


class BuilderTests(unittest.TestCase):
    def test_bracket_order_group_builds_parent_target_and_stop(self) -> None:
        group = bracket_order_group(
            account_id="123456789",
            symbol="MSFT",
            quantity=Decimal("2"),
            entry_action=TradeAction.BUY,
            entry_limit_price=Decimal("10"),
            target_price=Decimal("12"),
            stop_price=Decimal("9"),
        )

        payload = group_order_payload(group)

        self.assertEqual(group.type_, GroupType.BRACKET)
        self.assertEqual(payload["Orders"][0]["TradeAction"], "BUY")
        self.assertEqual(payload["Orders"][0]["TimeInForce"]["Duration"], "DAY")
        self.assertEqual(payload["Orders"][1]["TradeAction"], "SELL")
        self.assertEqual(payload["Orders"][1]["TimeInForce"]["Duration"], "GTC")
        self.assertEqual(payload["Orders"][2]["OrderType"], "StopMarket")

    def test_bracket_order_group_allows_distinct_entry_and_exit_duration(self) -> None:
        group = bracket_order_group(
            account_id="123456789",
            symbol="ESM26",
            quantity=Decimal("1"),
            entry_action=TradeAction.BUY,
            entry_limit_price=Decimal("5000"),
            target_price=Decimal("5010"),
            stop_price=Decimal("4990"),
            entry_duration=Duration.GTC,
            exit_duration=Duration.DAY,
            asset_class=AssetClass.FUTURE,
        )

        self.assertEqual(group.orders[0].time_in_force.duration, Duration.GTC)
        self.assertEqual(group.orders[1].time_in_force.duration, Duration.DAY)
        self.assertEqual(group.orders[0].asset_class, AssetClass.FUTURE)

    def test_oco_exit_group_builds_two_exit_orders(self) -> None:
        group = oco_exit_group(
            account_id="123456789",
            symbol="MSFT",
            quantity=Decimal("2"),
            exit_action=TradeAction.SELL,
            target_price=Decimal("12"),
            stop_price=Decimal("9"),
        )

        self.assertEqual(group.type_, GroupType.OCO)
        self.assertEqual(group.orders[0].order_type, OrderType.LIMIT)
        self.assertEqual(group.orders[1].order_type, OrderType.STOP_MARKET)

    def test_protective_exit_action_rejects_non_opening_action(self) -> None:
        with self.assertRaises(RequestValidationError):
            protective_exit_action(TradeAction.SELL)


if __name__ == "__main__":
    unittest.main()

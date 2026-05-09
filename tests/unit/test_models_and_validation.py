from __future__ import annotations

import unittest
from decimal import Decimal

from pydantic import ValidationError

from tests.helpers import sim_config
from tradestation_api_wrapper.errors import RequestValidationError
from tradestation_api_wrapper.models import (
    Duration,
    GroupOrderRequest,
    GroupType,
    OrderRequest,
    OrderType,
    TimeInForce,
    TradeAction,
)
from tradestation_api_wrapper.validation import (
    canonical_payload_hash,
    group_order_payload,
    order_payload,
    validate_order_for_config,
)


def limit_order(**overrides: object) -> OrderRequest:
    values = {
        "AccountID": "123456789",
        "Symbol": "MSFT",
        "Quantity": Decimal("2"),
        "OrderType": OrderType.LIMIT,
        "TradeAction": TradeAction.BUY,
        "TimeInForce": TimeInForce(Duration=Duration.DAY),
        "LimitPrice": Decimal("10.25"),
    }
    values.update(overrides)
    return OrderRequest.model_validate(values)


class ModelAndValidationTests(unittest.TestCase):
    def test_order_payload_uses_tradestation_field_names_and_decimal_strings(self) -> None:
        payload = order_payload(limit_order())

        self.assertEqual(payload["AccountID"], "123456789")
        self.assertEqual(payload["OrderType"], "Limit")
        self.assertEqual(payload["TradeAction"], "BUY")
        self.assertEqual(payload["Quantity"], "2")
        self.assertEqual(payload["LimitPrice"], "10.25")

    def test_payload_hash_is_stable(self) -> None:
        left = canonical_payload_hash({"b": "2", "a": "1"})
        right = canonical_payload_hash({"a": "1", "b": "2"})
        self.assertEqual(left, right)

    def test_limit_order_requires_limit_price(self) -> None:
        with self.assertRaises(ValidationError):
            limit_order(LimitPrice=None)

    def test_market_order_requires_config_permission_and_estimated_price(self) -> None:
        order = limit_order(OrderType=OrderType.MARKET, LimitPrice=None)
        with self.assertRaises(RequestValidationError):
            validate_order_for_config(order, sim_config())
        with self.assertRaises(RequestValidationError):
            validate_order_for_config(order, sim_config(allow_market_orders=True))

    def test_notional_limit_is_enforced(self) -> None:
        with self.assertRaises(RequestValidationError):
            validate_order_for_config(limit_order(LimitPrice=Decimal("999")), sim_config())

    def test_group_payload_shape(self) -> None:
        parent = limit_order()
        target = limit_order(TradeAction=TradeAction.SELL, LimitPrice=Decimal("12"))
        stop = limit_order(
            TradeAction=TradeAction.SELL,
            OrderType=OrderType.STOP_MARKET,
            LimitPrice=None,
            StopPrice=Decimal("9"),
        )
        group = GroupOrderRequest(Type=GroupType.BRACKET, Orders=(parent, target, stop))

        payload = group_order_payload(group)

        self.assertEqual(payload["Type"], "BRK")
        self.assertEqual(len(payload["Orders"]), 3)


if __name__ == "__main__":
    unittest.main()


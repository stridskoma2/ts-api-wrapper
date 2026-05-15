from __future__ import annotations

import unittest
from decimal import Decimal

from pydantic import ValidationError

from tests.helpers import sim_config
from tradestation_api_wrapper.errors import RequestValidationError
from tradestation_api_wrapper.models import (
    AdvancedOptions,
    AssetClass,
    BODBalanceSnapshot,
    BalanceSnapshot,
    Duration,
    GroupOrderRequest,
    GroupType,
    OptionRiskRewardLeg,
    OptionRiskRewardRequest,
    OrderReplaceRequest,
    OrderRequest,
    OrderType,
    TimeInForce,
    TradeAction,
    TrailingStop,
)
from tradestation_api_wrapper.validation import (
    canonical_payload_hash,
    group_order_payload,
    option_risk_reward_payload,
    order_payload,
    replace_order_payload,
    validate_order_for_config,
    validate_replace_for_config,
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
        "asset_class": AssetClass.EQUITY,
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

    def test_order_payload_supports_structured_advanced_options(self) -> None:
        order = limit_order(
            AdvancedOptions=AdvancedOptions(
                TrailingStop=TrailingStop(Percent=Decimal("5")),
                ShowOnlyQuantity=Decimal("100"),
            )
        )

        payload = order_payload(order)

        self.assertEqual(
            payload["AdvancedOptions"],
            {"ShowOnlyQuantity": "100", "TrailingStop": {"Percent": "5"}},
        )

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

    def test_order_validation_requires_explicit_asset_class(self) -> None:
        order = limit_order(asset_class=AssetClass.UNKNOWN)

        with self.assertRaises(RequestValidationError):
            validate_order_for_config(order, sim_config())

    def test_extended_hours_requires_config_permission(self) -> None:
        order = limit_order(TimeInForce=TimeInForce(Duration=Duration.DAY_PLUS))

        with self.assertRaises(RequestValidationError):
            validate_order_for_config(order, sim_config())

        validate_order_for_config(order, sim_config(allow_extended_hours=True))

    def test_gtd_duration_requires_expiration(self) -> None:
        with self.assertRaises(ValidationError):
            TimeInForce(Duration=Duration.GTD)

    def test_oso_children_are_risk_validated(self) -> None:
        parent = limit_order(OSOs=(limit_order(LimitPrice=Decimal("999")),))

        with self.assertRaises(RequestValidationError):
            validate_order_for_config(parent, sim_config())

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

    def test_replace_payload_allows_only_replaceable_fields(self) -> None:
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        payload = replace_order_payload(replacement)

        self.assertEqual(payload, {"LimitPrice": "11", "Quantity": "1"})

    def test_replace_request_rejects_non_market_order_type_updates(self) -> None:
        with self.assertRaises(ValidationError):
            OrderReplaceRequest(OrderType=OrderType.LIMIT)

    def test_replace_notional_limit_is_enforced_when_estimable(self) -> None:
        replacement = OrderReplaceRequest(Quantity=Decimal("2"), LimitPrice=Decimal("999"))

        with self.assertRaises(RequestValidationError):
            validate_replace_for_config(replacement, sim_config())

    def test_balance_detail_models_preserve_nested_account_fields(self) -> None:
        balance = BalanceSnapshot.model_validate(
            {
                "AccountID": "123456789",
                "BalanceDetail": {"OptionBuyingPower": "12.34"},
                "CurrencyDetails": [{"Currency": "USD", "CashBalance": "100"}],
            }
        )
        bod_balance = BODBalanceSnapshot.model_validate(
            {
                "AccountID": "123456789",
                "BalanceDetail": {"NetCash": "99"},
                "CurrencyDetails": [{"Currency": "USD", "OpenTradeEquity": "2"}],
            }
        )

        balance_detail = balance.balance_detail
        bod_balance_detail = bod_balance.balance_detail
        assert balance_detail is not None
        assert bod_balance_detail is not None

        self.assertEqual(balance_detail.option_buying_power, Decimal("12.34"))
        self.assertEqual(balance.currency_details[0].cash_balance, Decimal("100"))
        self.assertEqual(bod_balance_detail.net_cash, Decimal("99"))

    def test_option_risk_reward_payload_preserves_decimal_precision(self) -> None:
        request = OptionRiskRewardRequest(
            SpreadPrice=Decimal("0.24"),
            Legs=(
                OptionRiskRewardLeg(
                    Symbol="AAPL 211217C150",
                    Quantity=Decimal("1"),
                    TradeAction=TradeAction.BUY,
                ),
            ),
        )

        payload = option_risk_reward_payload(request)

        self.assertEqual(payload["SpreadPrice"], "0.24")
        self.assertEqual(payload["Legs"][0]["Quantity"], "1")


if __name__ == "__main__":
    unittest.main()

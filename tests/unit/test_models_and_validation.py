from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from tests.helpers import sim_config
from tradestation_api_wrapper.errors import RequestValidationError
from tradestation_api_wrapper.models import (
    ActivationRulesReplace,
    AdvancedOptions,
    AdvancedOptionsReplace,
    AssetClass,
    BarChartParams,
    BarSessionTemplate,
    BarUnit,
    BODBalanceSnapshot,
    BalanceSnapshot,
    Duration,
    GroupOrderRequest,
    GroupType,
    OptionChainStreamParams,
    OptionRiskRewardLeg,
    OptionRiskRewardRequest,
    OptionSpreadTypeName,
    OptionType,
    OrderReplaceRequest,
    OrderRequest,
    OrderType,
    StreamBarChartParams,
    StrikeRange,
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

    def test_order_request_defaults_to_equity_for_direct_callers(self) -> None:
        order = OrderRequest(
            AccountID="123456789",
            Symbol="MSFT",
            Quantity=Decimal("1"),
            OrderType=OrderType.LIMIT,
            TradeAction=TradeAction.BUY,
            TimeInForce=TimeInForce(Duration=Duration.DAY),
            LimitPrice=Decimal("10"),
        )

        self.assertEqual(order.asset_class, AssetClass.EQUITY)

    def test_order_validation_rejects_unknown_asset_class(self) -> None:
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

    def test_group_model_rejects_multiple_symbols(self) -> None:
        with self.assertRaises(ValidationError):
            GroupOrderRequest(
                Type=GroupType.OCO,
                Orders=(limit_order(Symbol="MSFT"), limit_order(Symbol="AAPL")),
            )

    def test_bar_chart_params_are_typed(self) -> None:
        params = BarChartParams(
            unit=BarUnit.MINUTE,
            interval=5,
            startdate=date(2026, 1, 2),
            sessiontemplate=BarSessionTemplate.USEQ_PRE,
        )

        self.assertEqual(params.unit, BarUnit.MINUTE)
        self.assertEqual(params.start_date, date(2026, 1, 2))
        with self.assertRaises(ValidationError):
            BarChartParams(interval=0)

    def test_bar_chart_params_reject_conflicting_ranges(self) -> None:
        with self.assertRaises(ValidationError):
            BarChartParams(firstdate=date(2026, 1, 1), barsback=10)
        with self.assertRaises(ValidationError):
            BarChartParams(lastdate=date(2026, 1, 2), startdate=date(2026, 1, 1))

    def test_stream_bar_chart_params_reject_rest_only_dates(self) -> None:
        params = StreamBarChartParams(unit=BarUnit.MINUTE, barsback=10)

        self.assertEqual(params.bars_back, 10)
        with self.assertRaises(ValidationError):
            StreamBarChartParams.model_validate({"firstdate": date(2026, 1, 1)})

    def test_option_chain_stream_params_are_typed(self) -> None:
        params = OptionChainStreamParams(
            expiration=date(2026, 6, 19),
            strikeProximity=4,
            spreadType=OptionSpreadTypeName.SINGLE,
            riskFreeRate=Decimal("0"),
            strikeRange=StrikeRange.ITM,
            optionType=OptionType.CALL,
            enableGreeks=True,
        )

        self.assertEqual(params.strike_proximity, 4)
        self.assertEqual(
            params.model_dump(by_alias=True, exclude_none=True, mode="json"),
            {
                "expiration": "2026-06-19",
                "strikeProximity": 4,
                "spreadType": "Single",
                "riskFreeRate": "0",
                "enableGreeks": True,
                "strikeRange": "ITM",
                "optionType": "Call",
            },
        )
        with self.assertRaises(ValidationError):
            OptionChainStreamParams(strikeInterval=0)
        with self.assertRaises(ValidationError):
            OptionChainStreamParams.model_validate({"optionType": "Calls"})
        with self.assertRaises(ValidationError):
            OptionChainStreamParams.model_validate({"strikeRange": "Near"})
        with self.assertRaises(ValidationError):
            OptionChainStreamParams.model_validate({"spreadType": "Butterflies"})

    def test_option_chain_stream_params_allow_economic_risk_free_rates(self) -> None:
        self.assertEqual(
            OptionChainStreamParams(riskFreeRate=Decimal("-0.01")).risk_free_rate,
            Decimal("-0.01"),
        )
        with self.assertRaises(ValidationError):
            OptionChainStreamParams(priceCenter=Decimal("0"))

    def test_replace_payload_allows_only_replaceable_fields(self) -> None:
        replacement = OrderReplaceRequest(Quantity=Decimal("1"), LimitPrice=Decimal("11"))

        payload = replace_order_payload(replacement)

        self.assertEqual(payload, {"LimitPrice": "11", "Quantity": "1"})

    def test_replace_payload_uses_replace_advanced_options_shape(self) -> None:
        replacement = OrderReplaceRequest(
            AdvancedOptions=AdvancedOptionsReplace(
                MarketActivationRules=ActivationRulesReplace(ClearAll=True),
                ShowOnlyQuantity=Decimal("100"),
                TrailingStop=TrailingStop(Percent=Decimal("5")),
            )
        )

        payload = replace_order_payload(replacement)

        self.assertEqual(
            payload,
            {
                "AdvancedOptions": {
                    "MarketActivationRules": {"ClearAll": True},
                    "ShowOnlyQuantity": "100",
                    "TrailingStop": {"Percent": "5"},
                }
            },
        )

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

    def test_option_risk_reward_payload_uses_api_numeric_types(self) -> None:
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

        self.assertEqual(payload["SpreadPrice"], 0.24)
        self.assertEqual(payload["Legs"][0]["Quantity"], 1)

    def test_option_risk_reward_leg_quantity_must_be_integral(self) -> None:
        with self.assertRaises(ValidationError):
            OptionRiskRewardLeg(
                Symbol="AAPL 211217C150",
                Quantity=Decimal("1.5"),
                TradeAction=TradeAction.BUY,
            )


if __name__ == "__main__":
    unittest.main()

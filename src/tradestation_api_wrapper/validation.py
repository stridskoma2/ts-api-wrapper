from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from tradestation_api_wrapper.config import TradeStationConfig
from tradestation_api_wrapper.errors import RequestValidationError
from tradestation_api_wrapper.models import (
    AssetClass,
    GroupOrderRequest,
    OrderReplaceRequest,
    OrderRequest,
    OrderType,
)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def order_payload(order: OrderRequest) -> dict[str, Any]:
    return _stringify_decimals(
        order.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True, mode="json")
    )


def group_order_payload(group: GroupOrderRequest) -> dict[str, Any]:
    return _stringify_decimals(
        group.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True, mode="json")
    )


def replace_order_payload(replacement: OrderReplaceRequest) -> dict[str, Any]:
    return _stringify_decimals(
        replacement.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True, mode="json")
    )


def validate_order_for_config(order: OrderRequest, config: TradeStationConfig) -> None:
    config.assert_can_submit_orders(order.account_id)
    if order.order_type is OrderType.MARKET and not config.allow_market_orders:
        raise RequestValidationError("market orders are disabled by configuration")
    if order.asset_class is AssetClass.OPTION and not config.allow_options:
        raise RequestValidationError("option orders are disabled by configuration")
    if order.asset_class is AssetClass.FUTURE and not config.allow_futures:
        raise RequestValidationError("futures orders are disabled by configuration")
    notional = _estimated_notional(order)
    if notional is not None and notional > config.max_order_notional:
        raise RequestValidationError(
            f"order notional {notional} exceeds max_order_notional {config.max_order_notional}"
        )
    if order.order_type is OrderType.MARKET and order.estimated_price is None:
        raise RequestValidationError("market orders require estimated_price for risk validation")


def validate_group_for_config(group: GroupOrderRequest, config: TradeStationConfig) -> None:
    account_ids = {order.account_id for order in group.orders}
    if len(account_ids) != 1:
        raise RequestValidationError("TradeStation order groups must use one account")
    symbols = {order.symbol for order in group.orders}
    if len(symbols) != 1:
        raise RequestValidationError("protective order groups must use one symbol")
    for order in group.orders:
        validate_order_for_config(order, config)


def validate_replace_for_config(
    replacement: OrderReplaceRequest,
    config: TradeStationConfig,
) -> None:
    notional = _estimated_replace_notional(replacement)
    if notional is not None and notional > config.max_order_notional:
        raise RequestValidationError(
            f"replacement notional {notional} exceeds max_order_notional "
            f"{config.max_order_notional}"
        )


def _estimated_notional(order: OrderRequest) -> Decimal | None:
    price = order.estimated_price or order.limit_price or order.stop_price
    if price is None:
        return None
    return order.quantity * price


def _estimated_replace_notional(replacement: OrderReplaceRequest) -> Decimal | None:
    price = replacement.limit_price or replacement.stop_price
    if replacement.quantity is None or price is None:
        return None
    return replacement.quantity * price


def _stringify_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _stringify_decimals(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stringify_decimals(item) for item in value]
    if isinstance(value, tuple):
        return [_stringify_decimals(item) for item in value]
    return value

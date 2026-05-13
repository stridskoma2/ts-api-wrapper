from __future__ import annotations

from decimal import Decimal

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


def limit_order(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    action: TradeAction,
    limit_price: Decimal,
    duration: Duration = Duration.DAY,
    route: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        AccountID=account_id,
        Symbol=symbol,
        Quantity=quantity,
        OrderType=OrderType.LIMIT,
        TradeAction=action,
        TimeInForce=TimeInForce(Duration=duration),
        LimitPrice=limit_price,
        Route=route,
    )


def market_order(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    action: TradeAction,
    estimated_price: Decimal,
    duration: Duration = Duration.DAY,
    route: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        AccountID=account_id,
        Symbol=symbol,
        Quantity=quantity,
        OrderType=OrderType.MARKET,
        TradeAction=action,
        TimeInForce=TimeInForce(Duration=duration),
        Route=route,
        estimated_price=estimated_price,
    )


def stop_market_order(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    action: TradeAction,
    stop_price: Decimal,
    duration: Duration = Duration.GTC,
    route: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        AccountID=account_id,
        Symbol=symbol,
        Quantity=quantity,
        OrderType=OrderType.STOP_MARKET,
        TradeAction=action,
        TimeInForce=TimeInForce(Duration=duration),
        StopPrice=stop_price,
        Route=route,
    )


def stop_limit_order(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    action: TradeAction,
    stop_price: Decimal,
    limit_price: Decimal,
    duration: Duration = Duration.GTC,
    route: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        AccountID=account_id,
        Symbol=symbol,
        Quantity=quantity,
        OrderType=OrderType.STOP_LIMIT,
        TradeAction=action,
        TimeInForce=TimeInForce(Duration=duration),
        StopPrice=stop_price,
        LimitPrice=limit_price,
        Route=route,
    )


def one_cancels_all(orders: tuple[OrderRequest, ...]) -> GroupOrderRequest:
    return GroupOrderRequest(Type=GroupType.OCO, Orders=orders)


def oco_exit_group(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    exit_action: TradeAction,
    target_price: Decimal,
    stop_price: Decimal,
    duration: Duration = Duration.GTC,
    route: str | None = None,
) -> GroupOrderRequest:
    target = limit_order(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        action=exit_action,
        limit_price=target_price,
        duration=duration,
        route=route,
    )
    stop = stop_market_order(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        action=exit_action,
        stop_price=stop_price,
        duration=duration,
        route=route,
    )
    return GroupOrderRequest(Type=GroupType.OCO, Orders=(target, stop))


def bracket_order_group(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    entry_action: TradeAction,
    entry_limit_price: Decimal,
    target_price: Decimal,
    stop_price: Decimal,
    duration: Duration = Duration.GTC,
    route: str | None = None,
) -> GroupOrderRequest:
    exit_action = protective_exit_action(entry_action)
    parent = limit_order(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        action=entry_action,
        limit_price=entry_limit_price,
        duration=Duration.DAY,
        route=route,
    )
    target = limit_order(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        action=exit_action,
        limit_price=target_price,
        duration=duration,
        route=route,
    )
    stop = stop_market_order(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        action=exit_action,
        stop_price=stop_price,
        duration=duration,
        route=route,
    )
    return GroupOrderRequest(Type=GroupType.BRACKET, Orders=(parent, target, stop))


def protective_exit_action(entry_action: TradeAction) -> TradeAction:
    if entry_action is TradeAction.BUY:
        return TradeAction.SELL
    if entry_action is TradeAction.SELL_SHORT:
        return TradeAction.BUY_TO_COVER
    if entry_action is TradeAction.BUY_TO_OPEN:
        return TradeAction.SELL_TO_CLOSE
    if entry_action is TradeAction.SELL_TO_OPEN:
        return TradeAction.BUY_TO_CLOSE
    raise RequestValidationError(f"{entry_action.value} is not an opening action")

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import quote, urlencode

from pydantic import BaseModel

from tradestation_api_wrapper.capabilities import (
    TRADESTATION_V3_CAPABILITIES,
    TradeStationCapabilities,
)
from tradestation_api_wrapper.config import (
    MARKET_DATA_SCOPE,
    MATRIX_SCOPE,
    OPTION_SPREADS_SCOPE,
    READ_ACCOUNT_SCOPE,
    TRADE_SCOPE,
    TradeStationConfig,
)
from tradestation_api_wrapper.errors import (
    AmbiguousOrderState,
    CapabilityError,
    PaginationError,
    RequestValidationError,
)
from tradestation_api_wrapper.models import (
    AccountSnapshot,
    AccountStateSnapshot,
    ActivationRulesReplace,
    AdvancedOptions,
    AdvancedOptionsReplace,
    BarChartParams,
    BarSnapshot,
    BODBalanceSnapshot,
    BalanceSnapshot,
    GroupOrderRequest,
    GroupType,
    OptionExpiration,
    OptionChainStreamParams,
    OptionQuoteLeg,
    OptionRiskReward,
    OptionRiskRewardRequest,
    OptionSpreadType,
    OptionStrikes,
    OrderAck,
    OrderConfirmation,
    OrderReplaceRequest,
    OrderRequest,
    OrderSnapshot,
    OrderType,
    PositionSnapshot,
    QuoteSnapshot,
    StreamBarChartParams,
    SymbolDetail,
)
from tradestation_api_wrapper.rest import (
    AccessTokenProvider,
    MARKET_DATA_STREAM_ACCEPT,
    TradeStationRestClient,
)
from tradestation_api_wrapper.stream import StreamEvent
from tradestation_api_wrapper.trade import TradeStationTrade
from tradestation_api_wrapper.transport import AsyncTransport, UrllibAsyncTransport
from tradestation_api_wrapper.validation import (
    canonical_payload_hash,
    group_order_payload,
    option_risk_reward_payload,
    order_payload,
    replace_order_payload,
    validate_group_for_config,
    validate_order_for_config,
    validate_replace_for_config,
)

MAX_ORDER_PAGES = 1000


class TradeStationClient:
    def __init__(
        self,
        config: TradeStationConfig,
        token_provider: AccessTokenProvider,
        *,
        transport: AsyncTransport | None = None,
        capabilities: TradeStationCapabilities | None = None,
    ) -> None:
        self.config = config
        self.capabilities = capabilities or TRADESTATION_V3_CAPABILITIES
        self._transport = transport or UrllibAsyncTransport()
        self._rest = TradeStationRestClient(
            config=config,
            token_provider=token_provider,
            transport=self._transport,
        )

    async def __aenter__(self) -> "TradeStationClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def get_accounts(self) -> tuple[AccountSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        payload = await self._rest.get("/brokerage/accounts")
        return tuple(AccountSnapshot.model_validate(item) for item in payload.get("Accounts", ()))

    async def get_balances(self, account_ids: tuple[str, ...]) -> tuple[BalanceSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/balances")
        return tuple(BalanceSnapshot.model_validate(item) for item in payload.get("Balances", ()))

    async def get_bod_balances(
        self,
        account_ids: tuple[str, ...],
    ) -> tuple[BODBalanceSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/bodbalances")
        return tuple(
            BODBalanceSnapshot.model_validate(item) for item in payload.get("BODBalances", ())
        )

    async def get_positions(
        self,
        account_ids: tuple[str, ...],
        *,
        symbols: tuple[str, ...] = (),
    ) -> tuple[PositionSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        query = self._query_string({"symbol": self._joined_symbols(symbols) if symbols else None})
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/positions{query}")
        return tuple(PositionSnapshot.model_validate(item) for item in payload.get("Positions", ()))

    async def get_orders(
        self,
        account_ids: tuple[str, ...],
        *,
        page_size: int | None = None,
    ) -> tuple[OrderSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        return await self._get_order_pages(
            f"/brokerage/accounts/{accounts}/orders",
            {"pageSize": page_size} if page_size is not None else {},
        )

    async def get_orders_by_id(
        self,
        account_ids: tuple[str, ...],
        order_ids: tuple[str, ...],
    ) -> tuple[OrderSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        orders = self._order_ids_path(order_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/orders/{orders}")
        return tuple(OrderSnapshot.model_validate(item) for item in payload.get("Orders", ()))

    async def get_historical_orders(
        self,
        account_ids: tuple[str, ...],
        *,
        since: datetime,
        page_size: int | None = None,
    ) -> tuple[OrderSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        params: dict[str, str | int] = {"since": since.isoformat()}
        if page_size is not None:
            params["pageSize"] = page_size
        return await self._get_order_pages(
            f"/brokerage/accounts/{accounts}/historicalorders",
            params,
        )

    async def get_historical_orders_by_id(
        self,
        account_ids: tuple[str, ...],
        order_ids: tuple[str, ...],
        *,
        since: datetime,
    ) -> tuple[OrderSnapshot, ...]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        accounts = self._account_path(account_ids)
        orders = self._order_ids_path(order_ids)
        query = self._query_string({"since": since})
        payload = await self._rest.get(
            f"/brokerage/accounts/{accounts}/historicalorders/{orders}{query}"
        )
        return tuple(OrderSnapshot.model_validate(item) for item in payload.get("Orders", ()))

    async def fetch_state_snapshot(self, account_ids: tuple[str, ...]) -> AccountStateSnapshot:
        self._account_path(account_ids)
        requested_account_ids = set(account_ids)
        accounts_payload, balances, positions, orders = await asyncio.gather(
            self.get_accounts(),
            self.get_balances(account_ids),
            self.get_positions(account_ids),
            self.get_orders(account_ids),
        )
        accounts = tuple(
            account
            for account in accounts_payload
            if account.account_id in requested_account_ids
        )
        return AccountStateSnapshot(
            accounts=accounts,
            balances=balances,
            positions=positions,
            orders=orders,
        )

    async def confirm_order(self, order: OrderRequest) -> OrderConfirmation:
        self._require_scope(TRADE_SCOPE)
        self._require_capability("supports_order_confirm")
        validate_order_for_config(order, self.config)
        payload = order_payload(order)
        response = await self._rest.post_confirm("/orderexecution/orderconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def what_if_order(self, order: OrderRequest) -> OrderConfirmation:
        return await self.confirm_order(order)

    async def place_order(self, order: OrderRequest) -> TradeStationTrade:
        self._require_scope(TRADE_SCOPE)
        self._require_capability("supports_single_orders")
        validate_order_for_config(order, self.config)
        payload = order_payload(order)
        payload_hash = canonical_payload_hash(payload)
        try:
            response = await self._rest.post_order_write(
                "/orderexecution/orders",
                payload,
                local_request_id=str(order.request_id),
            )
        except AmbiguousOrderState as exc:
            return TradeStationTrade(
                request=order,
                payload=payload,
                payload_hash=payload_hash,
                ambiguous_error=exc,
            )
        return TradeStationTrade(
            request=order,
            payload=payload,
            payload_hash=payload_hash,
            ack=OrderAck.model_validate(response),
        )

    async def confirm_order_group(self, group: GroupOrderRequest) -> OrderConfirmation:
        self._require_scope(TRADE_SCOPE)
        self._require_group_capabilities(group)
        self._require_capability("supports_group_confirm")
        validate_group_for_config(group, self.config)
        payload = group_order_payload(group)
        response = await self._rest.post_confirm("/orderexecution/ordergroupconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def what_if_order_group(self, group: GroupOrderRequest) -> OrderConfirmation:
        return await self.confirm_order_group(group)

    async def place_order_group(self, group: GroupOrderRequest) -> TradeStationTrade:
        self._require_scope(TRADE_SCOPE)
        self._require_group_capabilities(group)
        validate_group_for_config(group, self.config)
        payload = group_order_payload(group)
        payload_hash = canonical_payload_hash(payload)
        try:
            response = await self._rest.post_order_write(
                "/orderexecution/ordergroups",
                payload,
                local_request_id=str(group.request_id),
            )
        except AmbiguousOrderState as exc:
            return TradeStationTrade(
                request=group,
                payload=payload,
                payload_hash=payload_hash,
                ambiguous_error=exc,
            )
        return TradeStationTrade(
            request=group,
            payload=payload,
            payload_hash=payload_hash,
            ack=OrderAck.model_validate(response),
        )

    async def replace_order(
        self,
        account_id: str,
        order_id: str,
        replacement: OrderReplaceRequest | OrderRequest,
    ) -> TradeStationTrade:
        self._require_scope(TRADE_SCOPE)
        self._require_scope(READ_ACCOUNT_SCOPE)
        self._require_capability("supports_replace")
        self.config.assert_can_replace_orders(account_id)
        cleaned_order_id = self._order_id_value(order_id)
        await self._assert_order_belongs_to_account(account_id, cleaned_order_id)
        replacement_request = _coerce_replace_request(replacement)
        validate_replace_for_config(replacement_request, self.config)
        local_request_id = (
            str(replacement.request_id)
            if isinstance(replacement, OrderRequest)
            else cleaned_order_id
        )
        payload = replace_order_payload(replacement_request)
        payload_hash = canonical_payload_hash(payload)
        try:
            response = await self._rest.put_order_write(
                f"/orderexecution/orders/{quote(cleaned_order_id, safe='')}",
                payload,
                local_request_id=local_request_id,
            )
        except AmbiguousOrderState as exc:
            return TradeStationTrade(
                request=replacement_request,
                payload=payload,
                payload_hash=payload_hash,
                ambiguous_error=exc,
            )
        return TradeStationTrade(
            request=replacement_request,
            payload=payload,
            payload_hash=payload_hash,
            ack=OrderAck.model_validate(response),
        )

    async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
        self._require_scope(TRADE_SCOPE)
        self._require_scope(READ_ACCOUNT_SCOPE)
        self.config.assert_can_cancel_orders(account_id)
        cleaned_order_id = self._order_id_value(order_id)
        await self._assert_order_belongs_to_account(account_id, cleaned_order_id)
        return await self._rest.delete_order_write(
            f"/orderexecution/orders/{quote(cleaned_order_id, safe='')}",
            local_request_id=cleaned_order_id,
        )

    async def get_routes(self) -> dict[str, Any]:
        self._require_scope(TRADE_SCOPE)
        return await self._rest.get("/orderexecution/routes")

    async def get_activation_triggers(self) -> dict[str, Any]:
        self._require_scope(TRADE_SCOPE)
        return await self._rest.get("/orderexecution/activationtriggers")

    async def get_crypto_symbol_names(self) -> tuple[str, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        payload = await self._rest.get("/marketdata/symbollists/cryptopairs/symbolnames")
        return tuple(str(symbol) for symbol in payload.get("SymbolNames", ()))

    async def get_quotes(self, symbols: tuple[str, ...]) -> tuple[QuoteSnapshot, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        payload = await self._rest.get(f"/marketdata/quotes/{self._symbol_path(symbols)}")
        return tuple(QuoteSnapshot.model_validate(item) for item in payload.get("Quotes", ()))

    async def get_symbols(self, symbols: tuple[str, ...]) -> tuple[SymbolDetail, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        payload = await self._rest.get(f"/marketdata/symbols/{self._symbol_path(symbols)}")
        return tuple(SymbolDetail.model_validate(item) for item in payload.get("Symbols", ()))

    async def get_option_expirations(
        self,
        underlying: str,
        *,
        strike_price: Decimal | None = None,
    ) -> tuple[OptionExpiration, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        query = self._query_string({"strikePrice": strike_price})
        payload = await self._rest.get(
            f"/marketdata/options/expirations/{self._single_symbol_path(underlying)}{query}"
        )
        return tuple(
            OptionExpiration.model_validate(item) for item in payload.get("Expirations", ())
        )

    async def get_option_spread_types(self) -> tuple[OptionSpreadType, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        payload = await self._rest.get("/marketdata/options/spreadtypes")
        return tuple(
            OptionSpreadType.model_validate(item) for item in payload.get("SpreadTypes", ())
        )

    async def get_option_strikes(
        self,
        underlying: str,
        *,
        spread_type: str | None = None,
        strike_interval: int | None = None,
        expiration: str | date | datetime | None = None,
        expiration2: str | date | datetime | None = None,
    ) -> OptionStrikes:
        self._require_scope(MARKET_DATA_SCOPE)
        query = self._query_string(
            {
                "spreadType": spread_type,
                "strikeInterval": strike_interval,
                "expiration": expiration,
                "expiration2": expiration2,
            }
        )
        payload = await self._rest.get(
            f"/marketdata/options/strikes/{self._single_symbol_path(underlying)}{query}"
        )
        return OptionStrikes.model_validate(payload)

    async def get_option_risk_reward(
        self,
        request: OptionRiskRewardRequest,
    ) -> OptionRiskReward:
        self._require_scope(OPTION_SPREADS_SCOPE)
        payload = await self._rest.post_read(
            "/marketdata/options/riskreward",
            option_risk_reward_payload(request),
        )
        return OptionRiskReward.model_validate(payload)

    async def get_bars(
        self,
        symbol: str,
        *,
        params: BarChartParams | None = None,
    ) -> tuple[BarSnapshot, ...]:
        self._require_scope(MARKET_DATA_SCOPE)
        query = self._query_string(_bar_query_params(params))
        payload = await self._rest.get(
            f"/marketdata/barcharts/{self._single_symbol_path(symbol)}{query}"
        )
        return tuple(BarSnapshot.model_validate(item) for item in payload.get("Bars", ()))

    def stream_orders(
        self,
        account_ids: tuple[str, ...],
        *,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        self._require_capability("supports_stream_orders")
        return self._rest.stream_events(
            f"/brokerage/stream/accounts/{self._account_path(account_ids)}/orders",
            raise_on_error=raise_on_error,
        )

    def stream_orders_by_id(
        self,
        account_ids: tuple[str, ...],
        order_ids: tuple[str, ...],
        *,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        self._require_capability("supports_stream_orders")
        return self._rest.stream_events(
            f"/brokerage/stream/accounts/{self._account_path(account_ids)}/orders/"
            f"{self._order_ids_path(order_ids)}",
            raise_on_error=raise_on_error,
        )

    def stream_positions(
        self,
        account_ids: tuple[str, ...],
        *,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(READ_ACCOUNT_SCOPE)
        self._require_capability("supports_stream_positions")
        return self._rest.stream_events(
            f"/brokerage/stream/accounts/{self._account_path(account_ids)}/positions",
            raise_on_error=raise_on_error,
        )

    def stream_quotes(
        self,
        symbols: tuple[str, ...],
        *,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MARKET_DATA_SCOPE)
        self._require_capability("supports_quote_stream")
        return self._rest.stream_events(
            f"/marketdata/stream/quotes/{self._symbol_path(symbols)}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def stream_bars(
        self,
        symbol: str,
        *,
        params: StreamBarChartParams | None = None,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MARKET_DATA_SCOPE)
        self._require_capability("supports_bar_stream")
        query = self._query_string(_bar_query_params(params))
        return self._rest.stream_events(
            f"/marketdata/stream/barcharts/{self._single_symbol_path(symbol)}{query}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def stream_market_depth_aggregates(
        self,
        symbol: str,
        *,
        max_levels: int | None = None,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MATRIX_SCOPE)
        self._require_capability("supports_market_depth_stream")
        query = self._query_string({"maxlevels": _positive_int(max_levels, "max_levels")})
        return self._rest.stream_events(
            f"/marketdata/stream/marketdepth/aggregates/{self._single_symbol_path(symbol)}{query}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def stream_market_depth_quotes(
        self,
        symbol: str,
        *,
        max_levels: int | None = None,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MATRIX_SCOPE)
        self._require_capability("supports_market_depth_stream")
        query = self._query_string({"maxlevels": _positive_int(max_levels, "max_levels")})
        return self._rest.stream_events(
            f"/marketdata/stream/marketdepth/quotes/{self._single_symbol_path(symbol)}{query}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def stream_option_chain(
        self,
        underlying: str,
        *,
        params: OptionChainStreamParams | None = None,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MARKET_DATA_SCOPE)
        self._require_capability("supports_option_streams")
        query = self._query_string(_model_query_params(params))
        return self._rest.stream_events(
            f"/marketdata/stream/options/chains/{self._single_symbol_path(underlying)}{query}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def stream_option_quotes(
        self,
        legs: tuple[OptionQuoteLeg, ...],
        *,
        risk_free_rate: Decimal | None = None,
        enable_greeks: bool | None = None,
        raise_on_error: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        self._require_scope(MARKET_DATA_SCOPE)
        self._require_capability("supports_option_streams")
        if not legs:
            raise ValueError("at least one option quote leg is required")
        params: dict[str, object | None] = {
            "riskFreeRate": risk_free_rate,
            "enableGreeks": enable_greeks,
        }
        for index, leg in enumerate(legs):
            params[f"legs[{index}].Symbol"] = leg.symbol
            params[f"legs[{index}].Ratio"] = leg.ratio
        return self._rest.stream_events(
            f"/marketdata/stream/options/quotes{self._query_string(params)}",
            accept=MARKET_DATA_STREAM_ACCEPT,
            raise_on_error=raise_on_error,
        )

    def order_payload_hash(self, order: OrderRequest) -> str:
        return canonical_payload_hash(order_payload(order))

    async def _get_order_pages(
        self,
        base_path: str,
        params: Mapping[str, object | None],
    ) -> tuple[OrderSnapshot, ...]:
        orders: list[OrderSnapshot] = []
        next_token: str | None = None
        for _page_number in range(MAX_ORDER_PAGES):
            query_params = {key: value for key, value in params.items() if value is not None}
            if next_token is not None:
                query_params["nextToken"] = next_token
            query = self._query_string(query_params)
            payload = await self._rest.get(f"{base_path}{query}")
            orders.extend(OrderSnapshot.model_validate(item) for item in payload.get("Orders", ()))
            next_token_value = payload.get("NextToken")
            if not isinstance(next_token_value, str) or not next_token_value.strip():
                return tuple(orders)
            next_token = next_token_value
        raise PaginationError("TradeStation order pagination exceeded the page safety limit")

    def _account_path(self, account_ids: tuple[str, ...]) -> str:
        if not account_ids:
            raise ValueError("at least one account ID is required")
        for account_id in account_ids:
            self.config.assert_account_allowed(account_id)
        return ",".join(account_ids)

    async def _assert_order_belongs_to_account(self, account_id: str, order_id: str) -> None:
        matches = await self.get_orders_by_id((account_id,), (order_id,))
        for match in matches:
            if match.order_id == order_id and match.account_id in {None, account_id}:
                return
        raise RequestValidationError("order was not found for the requested account")

    def _require_scope(self, scope: str) -> None:
        self.config.assert_scope_requested(scope)

    def _order_ids_path(self, order_ids: tuple[str, ...]) -> str:
        cleaned = tuple(order_id.strip() for order_id in order_ids if order_id.strip())
        if not cleaned:
            raise ValueError("at least one order ID is required")
        if len(cleaned) > 50:
            raise ValueError("at most 50 order IDs are allowed")
        return ",".join(quote(order_id, safe="") for order_id in cleaned)

    def _order_id_value(self, order_id: str) -> str:
        cleaned = order_id.strip()
        if not cleaned:
            raise ValueError("order ID must not be blank")
        return cleaned

    def _symbol_path(self, symbols: tuple[str, ...]) -> str:
        return ",".join(quote(symbol, safe="") for symbol in self._clean_symbols(symbols))

    def _single_symbol_path(self, symbol: str) -> str:
        return quote(self._path_segment(symbol, "symbol"), safe="")

    def _joined_symbols(self, symbols: tuple[str, ...]) -> str:
        return ",".join(self._clean_symbols(symbols))

    def _clean_symbols(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(symbol.strip() for symbol in symbols if symbol.strip())
        if not cleaned:
            raise ValueError("at least one symbol is required")
        return cleaned

    def _path_segment(self, value: str, name: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{name} must not be blank")
        return cleaned

    def _query_string(self, params: Mapping[str, object | None]) -> str:
        query_params = {
            key: _query_value(value) for key, value in params.items() if value is not None
        }
        return f"?{urlencode(query_params)}" if query_params else ""

    def _require_group_capabilities(self, group: GroupOrderRequest) -> None:
        self._require_capability("supports_group_orders")
        if group.type_ is GroupType.OCO:
            self._require_capability("supports_oco")
        if group.type_ is GroupType.BRACKET:
            self._require_capability("supports_bracket")

    def _require_capability(self, capability_name: str) -> None:
        if not bool(getattr(self.capabilities, capability_name)):
            raise CapabilityError(f"TradeStation capability unavailable: {capability_name}")


def _coerce_replace_request(replacement: OrderReplaceRequest | OrderRequest) -> OrderReplaceRequest:
    if isinstance(replacement, OrderReplaceRequest):
        return replacement
    return OrderReplaceRequest(
        AdvancedOptions=_coerce_replace_advanced_options(replacement.advanced_options),
        LimitPrice=replacement.limit_price,
        OrderType=replacement.order_type if replacement.order_type is OrderType.MARKET else None,
        Quantity=replacement.quantity,
        StopPrice=replacement.stop_price,
    )


def _coerce_replace_advanced_options(
    advanced_options: AdvancedOptions | None,
) -> AdvancedOptionsReplace | None:
    if advanced_options is None:
        return None
    market_rules = None
    if advanced_options.market_activation_rules:
        market_rules = ActivationRulesReplace(Rules=advanced_options.market_activation_rules)
    time_rules = None
    if advanced_options.time_activation_rules:
        time_rules = ActivationRulesReplace(Rules=advanced_options.time_activation_rules)
    return AdvancedOptionsReplace(
        MarketActivationRules=market_rules,
        ShowOnlyQuantity=advanced_options.show_only_quantity,
        TimeActivationRules=time_rules,
        TrailingStop=advanced_options.trailing_stop,
    )


def _query_value(value: object) -> str | int:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return value
    return str(value)


def _bar_query_params(
    params: BarChartParams | StreamBarChartParams | None,
) -> Mapping[str, object | None]:
    return _model_query_params(params)


def _model_query_params(params: BaseModel | None) -> Mapping[str, object | None]:
    if params is None:
        return {}
    return params.model_dump(
        by_alias=True,
        exclude_defaults=True,
        exclude_none=True,
        mode="json",
    )


def _positive_int(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from tradestation_api_wrapper.config import TradeStationConfig
from tradestation_api_wrapper.errors import AmbiguousOrderState
from tradestation_api_wrapper.models import (
    AccountSnapshot,
    AccountStateSnapshot,
    BarSnapshot,
    BalanceSnapshot,
    GroupOrderRequest,
    OrderAck,
    OrderConfirmation,
    OrderRequest,
    OrderSnapshot,
    PositionSnapshot,
    QuoteSnapshot,
    SymbolDetail,
)
from tradestation_api_wrapper.rest import AccessTokenProvider, TradeStationRestClient
from tradestation_api_wrapper.stream import StreamEvent
from tradestation_api_wrapper.trade import TradeStationTrade
from tradestation_api_wrapper.transport import AsyncTransport, UrllibAsyncTransport
from tradestation_api_wrapper.validation import (
    canonical_payload_hash,
    group_order_payload,
    order_payload,
    validate_group_for_config,
    validate_order_for_config,
)


class TradeStationClient:
    def __init__(
        self,
        config: TradeStationConfig,
        token_provider: AccessTokenProvider,
        *,
        transport: AsyncTransport | None = None,
    ) -> None:
        self.config = config
        self._rest = TradeStationRestClient(
            config=config,
            token_provider=token_provider,
            transport=transport or UrllibAsyncTransport(),
        )

    async def get_accounts(self) -> tuple[AccountSnapshot, ...]:
        payload = await self._rest.get("/brokerage/accounts")
        return tuple(AccountSnapshot.model_validate(item) for item in payload.get("Accounts", ()))

    async def get_balances(self, account_ids: tuple[str, ...]) -> tuple[BalanceSnapshot, ...]:
        accounts = self._account_path(account_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/balances")
        return tuple(BalanceSnapshot.model_validate(item) for item in payload.get("Balances", ()))

    async def get_positions(self, account_ids: tuple[str, ...]) -> tuple[PositionSnapshot, ...]:
        accounts = self._account_path(account_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/positions")
        return tuple(PositionSnapshot.model_validate(item) for item in payload.get("Positions", ()))

    async def get_orders(self, account_ids: tuple[str, ...]) -> tuple[OrderSnapshot, ...]:
        accounts = self._account_path(account_ids)
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/orders")
        return tuple(OrderSnapshot.model_validate(item) for item in payload.get("Orders", ()))

    async def get_historical_orders(
        self,
        account_ids: tuple[str, ...],
        *,
        since: datetime,
    ) -> tuple[OrderSnapshot, ...]:
        accounts = self._account_path(account_ids)
        query = urlencode({"since": since.isoformat()})
        payload = await self._rest.get(f"/brokerage/accounts/{accounts}/historicalorders?{query}")
        return tuple(OrderSnapshot.model_validate(item) for item in payload.get("Orders", ()))

    async def fetch_state_snapshot(self, account_ids: tuple[str, ...]) -> AccountStateSnapshot:
        self._account_path(account_ids)
        requested_account_ids = set(account_ids)
        accounts = tuple(
            account for account in await self.get_accounts() if account.account_id in requested_account_ids
        )
        balances = await self.get_balances(account_ids)
        positions = await self.get_positions(account_ids)
        orders = await self.get_orders(account_ids)
        return AccountStateSnapshot(
            accounts=accounts,
            balances=balances,
            positions=positions,
            orders=orders,
        )

    async def confirm_order(self, order: OrderRequest) -> OrderConfirmation:
        validate_order_for_config(order, self.config)
        payload = order_payload(order)
        response = await self._rest.post_confirm("/orderexecution/orderconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def what_if_order(self, order: OrderRequest) -> OrderConfirmation:
        return await self.confirm_order(order)

    async def place_order(self, order: OrderRequest) -> TradeStationTrade:
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
        validate_group_for_config(group, self.config)
        payload = group_order_payload(group)
        response = await self._rest.post_confirm("/orderexecution/ordergroupconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def what_if_order_group(self, group: GroupOrderRequest) -> OrderConfirmation:
        return await self.confirm_order_group(group)

    async def place_order_group(self, group: GroupOrderRequest) -> TradeStationTrade:
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

    async def replace_order(self, order_id: str, replacement: OrderRequest) -> TradeStationTrade:
        validate_order_for_config(replacement, self.config)
        payload = order_payload(replacement)
        payload_hash = canonical_payload_hash(payload)
        try:
            response = await self._rest.put_order_write(
                f"/orderexecution/orders/{order_id}",
                payload,
                local_request_id=str(replacement.request_id),
            )
        except AmbiguousOrderState as exc:
            return TradeStationTrade(
                request=replacement,
                payload=payload,
                payload_hash=payload_hash,
                ambiguous_error=exc,
            )
        return TradeStationTrade(
            request=replacement,
            payload=payload,
            payload_hash=payload_hash,
            ack=OrderAck.model_validate(response),
        )

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._rest.delete_order_write(
            f"/orderexecution/orders/{order_id}",
            local_request_id=order_id,
        )

    async def get_routes(self) -> dict[str, Any]:
        return await self._rest.get("/orderexecution/routes")

    async def get_activation_triggers(self) -> dict[str, Any]:
        return await self._rest.get("/orderexecution/activationtriggers")

    async def get_quotes(self, symbols: tuple[str, ...]) -> tuple[QuoteSnapshot, ...]:
        payload = await self._rest.get(f"/marketdata/quotes/{self._symbol_path(symbols)}")
        return tuple(QuoteSnapshot.model_validate(item) for item in payload.get("Quotes", ()))

    async def get_symbols(self, symbols: tuple[str, ...]) -> tuple[SymbolDetail, ...]:
        payload = await self._rest.get(f"/marketdata/symbols/{self._symbol_path(symbols)}")
        return tuple(SymbolDetail.model_validate(item) for item in payload.get("Symbols", ()))

    async def get_bars(
        self,
        symbol: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[BarSnapshot, ...]:
        query = f"?{urlencode(params)}" if params else ""
        payload = await self._rest.get(f"/marketdata/barcharts/{self._single_symbol_path(symbol)}{query}")
        return tuple(BarSnapshot.model_validate(item) for item in payload.get("Bars", ()))

    def stream_orders(self, account_ids: tuple[str, ...]) -> AsyncIterator[StreamEvent]:
        return self._rest.stream_events(f"/brokerage/stream/accounts/{self._account_path(account_ids)}/orders")

    def stream_positions(self, account_ids: tuple[str, ...]) -> AsyncIterator[StreamEvent]:
        return self._rest.stream_events(
            f"/brokerage/stream/accounts/{self._account_path(account_ids)}/positions"
        )

    def stream_quotes(self, symbols: tuple[str, ...]) -> AsyncIterator[StreamEvent]:
        return self._rest.stream_events(
            f"/marketdata/stream/quotes/{self._symbol_path(symbols)}",
            accept="application/vnd.tradestation.streams.v2+json",
        )

    def stream_bars(self, symbol: str, *, params: dict[str, Any] | None = None) -> AsyncIterator[StreamEvent]:
        query = f"?{urlencode(params)}" if params else ""
        return self._rest.stream_events(
            f"/marketdata/stream/barcharts/{self._single_symbol_path(symbol)}{query}",
            accept="application/vnd.tradestation.streams.v2+json",
        )

    def order_payload_hash(self, order: OrderRequest) -> str:
        return canonical_payload_hash(order_payload(order))

    def _account_path(self, account_ids: tuple[str, ...]) -> str:
        if not account_ids:
            raise ValueError("at least one account ID is required")
        for account_id in account_ids:
            self.config.assert_account_allowed(account_id)
        return ",".join(account_ids)

    def _symbol_path(self, symbols: tuple[str, ...]) -> str:
        cleaned = tuple(symbol.strip() for symbol in symbols if symbol.strip())
        if not cleaned:
            raise ValueError("at least one symbol is required")
        return ",".join(quote(symbol, safe="") for symbol in cleaned)

    def _single_symbol_path(self, symbol: str) -> str:
        cleaned = symbol.strip()
        if not cleaned:
            raise ValueError("symbol must not be blank")
        return quote(cleaned, safe="")

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from tradestation_api_wrapper.config import TradeStationConfig
from tradestation_api_wrapper.models import (
    AccountSnapshot,
    BalanceSnapshot,
    GroupOrderRequest,
    OrderAck,
    OrderConfirmation,
    OrderRequest,
    OrderSnapshot,
    PositionSnapshot,
)
from tradestation_api_wrapper.rest import AccessTokenProvider, TradeStationRestClient
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

    async def confirm_order(self, order: OrderRequest) -> OrderConfirmation:
        validate_order_for_config(order, self.config)
        payload = order_payload(order)
        response = await self._rest.post_confirm("/orderexecution/orderconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def place_order(self, order: OrderRequest) -> OrderAck:
        validate_order_for_config(order, self.config)
        payload = order_payload(order)
        response = await self._rest.post_order_write(
            "/orderexecution/orders",
            payload,
            local_request_id=str(order.request_id),
        )
        return OrderAck.model_validate(response)

    async def confirm_order_group(self, group: GroupOrderRequest) -> OrderConfirmation:
        validate_group_for_config(group, self.config)
        payload = group_order_payload(group)
        response = await self._rest.post_confirm("/orderexecution/ordergroupconfirm", payload)
        return OrderConfirmation.model_validate(response)

    async def place_order_group(self, group: GroupOrderRequest) -> OrderAck:
        validate_group_for_config(group, self.config)
        payload = group_order_payload(group)
        response = await self._rest.post_order_write(
            "/orderexecution/ordergroups",
            payload,
            local_request_id=str(group.request_id),
        )
        return OrderAck.model_validate(response)

    async def replace_order(self, order_id: str, replacement: OrderRequest) -> OrderAck:
        validate_order_for_config(replacement, self.config)
        payload = order_payload(replacement)
        response = await self._rest.put_order_write(
            f"/orderexecution/orders/{order_id}",
            payload,
            local_request_id=str(replacement.request_id),
        )
        return OrderAck.model_validate(response)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._rest.delete_order_write(
            f"/orderexecution/orders/{order_id}",
            local_request_id=order_id,
        )

    async def get_routes(self) -> dict[str, Any]:
        return await self._rest.get("/orderexecution/routes")

    async def get_activation_triggers(self) -> dict[str, Any]:
        return await self._rest.get("/orderexecution/activationtriggers")

    def order_payload_hash(self, order: OrderRequest) -> str:
        return canonical_payload_hash(order_payload(order))

    def _account_path(self, account_ids: tuple[str, ...]) -> str:
        if not account_ids:
            raise ValueError("at least one account ID is required")
        for account_id in account_ids:
            self.config.assert_account_allowed(account_id)
        return ",".join(account_ids)


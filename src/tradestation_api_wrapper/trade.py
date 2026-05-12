from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tradestation_api_wrapper.errors import AmbiguousOrderState
from tradestation_api_wrapper.models import (
    GroupOrderRequest,
    OrderAck,
    OrderReplaceRequest,
    OrderRequest,
    OrderSnapshot,
)
from tradestation_api_wrapper.order_status import TradeStationOrderStatus


OrderWriteRequest = OrderRequest | GroupOrderRequest | OrderReplaceRequest


@dataclass(frozen=True, slots=True)
class TradeStationTrade:
    request: OrderWriteRequest
    payload: dict[str, Any]
    payload_hash: str
    ack: OrderAck | None = None
    latest_order: OrderSnapshot | None = None
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    ambiguous_error: AmbiguousOrderState | None = None

    @property
    def order_id(self) -> str | None:
        if self.latest_order is not None:
            return self.latest_order.order_id
        if self.ack is not None:
            return self.ack.first_order_id()
        return None

    @property
    def status(self) -> TradeStationOrderStatus | None:
        if self.latest_order is None:
            return None
        return self.latest_order.status_value

    @property
    def is_ambiguous(self) -> bool:
        return self.ambiguous_error is not None

    @property
    def reconcile_required(self) -> bool:
        return self.is_ambiguous or (self.ack is not None and self.order_id is None)

    @property
    def is_done(self) -> bool:
        return bool(self.latest_order and self.latest_order.is_done)

    @property
    def is_active(self) -> bool:
        return bool(self.latest_order and self.latest_order.is_active)

    def with_order_snapshot(self, order: OrderSnapshot) -> "TradeStationTrade":
        return TradeStationTrade(
            request=self.request,
            payload=self.payload,
            payload_hash=self.payload_hash,
            ack=self.ack,
            latest_order=order,
            events=self.events,
            ambiguous_error=self.ambiguous_error,
        )

    def with_event(self, event: dict[str, Any]) -> "TradeStationTrade":
        return TradeStationTrade(
            request=self.request,
            payload=self.payload,
            payload_hash=self.payload_hash,
            ack=self.ack,
            latest_order=self.latest_order,
            events=(*self.events, event),
            ambiguous_error=self.ambiguous_error,
        )

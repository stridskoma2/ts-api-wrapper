from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TradeStationCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    supports_single_orders: bool = True
    supports_order_confirm: bool = True
    supports_group_orders: bool = True
    supports_group_confirm: bool = True
    supports_oco: bool = True
    supports_bracket: bool = True
    supports_oso_children: bool = True
    supports_replace: bool = True
    replace_preserves_order_id: bool | None = None
    supports_stream_orders: bool = True
    supports_stream_positions: bool = True
    supports_quote_stream: bool = True
    supports_bar_stream: bool = True
    supports_native_partial_fill_sibling_reduction: bool | None = None


TRADESTATION_V3_CAPABILITIES = TradeStationCapabilities()


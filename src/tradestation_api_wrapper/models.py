from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    BUY_TO_COVER = "BUYTOCOVER"
    SELL_SHORT = "SELLSHORT"
    BUY_TO_OPEN = "BUYTOOPEN"
    BUY_TO_CLOSE = "BUYTOCLOSE"
    SELL_TO_OPEN = "SELLTOOPEN"
    SELL_TO_CLOSE = "SELLTOCLOSE"


class OrderType(str, Enum):
    LIMIT = "Limit"
    STOP_MARKET = "StopMarket"
    MARKET = "Market"
    STOP_LIMIT = "StopLimit"


class Duration(str, Enum):
    DAY = "DAY"
    DAY_PLUS = "DYP"
    GTC = "GTC"
    GTC_PLUS = "GCP"
    GTD = "GTD"
    GTD_PLUS = "GDP"
    OPEN = "OPG"
    CLOSE = "CLO"
    IOC = "IOC"
    FOK = "FOK"
    MINUTE_1 = "1"
    MINUTE_3 = "3"
    MINUTE_5 = "5"


class GroupType(str, Enum):
    NORMAL = "NORMAL"
    OCO = "OCO"
    BRACKET = "BRK"


class TimeInForce(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    duration: Duration = Field(alias="Duration")
    expiration: datetime | None = Field(default=None, alias="Expiration")


class OrderRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    account_id: str = Field(alias="AccountID")
    symbol: str = Field(alias="Symbol")
    quantity: Decimal = Field(alias="Quantity")
    order_type: OrderType = Field(alias="OrderType")
    trade_action: TradeAction = Field(alias="TradeAction")
    time_in_force: TimeInForce = Field(alias="TimeInForce")
    limit_price: Decimal | None = Field(default=None, alias="LimitPrice")
    stop_price: Decimal | None = Field(default=None, alias="StopPrice")
    route: str | None = Field(default=None, alias="Route")
    advanced_options: str | None = Field(default=None, alias="AdvancedOptions")
    order_confirm_id: str | None = Field(default=None, alias="OrderConfirmID")
    osos: tuple["OrderRequest", ...] = Field(default=(), alias="OSOs")
    request_id: UUID = Field(default_factory=uuid4, exclude=True)
    asset_class: AssetClass = Field(default=AssetClass.EQUITY, exclude=True)
    client_order_id: str | None = Field(default=None, exclude=True)
    estimated_price: Decimal | None = Field(default=None, exclude=True)

    @field_validator("account_id", "symbol")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("quantity")
    @classmethod
    def require_positive_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @field_validator("limit_price", "stop_price", "estimated_price")
    @classmethod
    def require_positive_price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("price must be positive")
        return value

    @model_validator(mode="after")
    def validate_price_requirements(self) -> "OrderRequest":
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require LimitPrice")
        if self.order_type is OrderType.STOP_MARKET and self.stop_price is None:
            raise ValueError("stop-market orders require StopPrice")
        if self.order_type is OrderType.STOP_LIMIT:
            if self.limit_price is None or self.stop_price is None:
                raise ValueError("stop-limit orders require both LimitPrice and StopPrice")
        return self


class GroupOrderRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type_: GroupType = Field(alias="Type")
    orders: tuple[OrderRequest, ...] = Field(alias="Orders")
    request_id: UUID = Field(default_factory=uuid4, exclude=True)

    @model_validator(mode="after")
    def validate_group_shape(self) -> "GroupOrderRequest":
        if not self.orders:
            raise ValueError("order group must contain at least one order")
        if self.type_ is GroupType.OCO and len(self.orders) < 2:
            raise ValueError("OCO groups require at least two orders")
        if self.type_ is GroupType.BRACKET and len(self.orders) < 3:
            raise ValueError("bracket groups require parent, target, and stop orders")
        return self


class TradeStationEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AccountSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    status: str | None = Field(default=None, alias="Status")
    account_type: str | None = Field(default=None, alias="AccountType")
    currency: str | None = Field(default=None, alias="Currency")


class BalanceSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    account_type: str | None = Field(default=None, alias="AccountType")
    buying_power: Decimal | None = Field(default=None, alias="BuyingPower")
    equity: Decimal | None = Field(default=None, alias="Equity")
    todays_profit_loss: Decimal | None = Field(default=None, alias="TodaysProfitLoss")


class PositionSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    symbol: str = Field(alias="Symbol")
    quantity: Decimal = Field(alias="Quantity")
    asset_type: str | None = Field(default=None, alias="AssetType")
    average_price: Decimal | None = Field(default=None, alias="AveragePrice")
    long_short: str | None = Field(default=None, alias="LongShort")


class OrderLegSnapshot(TradeStationEnvelope):
    symbol: str | None = Field(default=None, alias="Symbol")
    buy_or_sell: str | None = Field(default=None, alias="BuyOrSell")
    quantity_ordered: Decimal | None = Field(default=None, alias="QuantityOrdered")
    quantity_remaining: Decimal | None = Field(default=None, alias="QuantityRemaining")
    exec_quantity: Decimal | None = Field(default=None, alias="ExecQuantity")


class OrderSnapshot(TradeStationEnvelope):
    account_id: str | None = Field(default=None, alias="AccountID")
    order_id: str = Field(alias="OrderID")
    status: str | None = Field(default=None, alias="Status")
    symbol: str | None = Field(default=None, alias="Symbol")
    order_type: OrderType | None = Field(default=None, alias="OrderType")
    limit_price: Decimal | None = Field(default=None, alias="LimitPrice")
    stop_price: Decimal | None = Field(default=None, alias="StopPrice")
    opened_at: datetime | None = Field(default=None, alias="OpenedDateTime")
    legs: tuple[OrderLegSnapshot, ...] = Field(default=(), alias="Legs")

    def primary_symbol(self) -> str | None:
        if self.symbol:
            return self.symbol
        if self.legs:
            return self.legs[0].symbol
        return None

    def primary_quantity(self) -> Decimal | None:
        if self.legs:
            return self.legs[0].quantity_ordered
        return None

    def primary_action(self) -> str | None:
        if self.legs:
            return self.legs[0].buy_or_sell
        return None


class OrderAck(TradeStationEnvelope):
    order_id: str | None = Field(default=None, alias="OrderID")
    orders: tuple[dict[str, Any], ...] = Field(default=(), alias="Orders")
    errors: tuple[dict[str, Any], ...] = Field(default=(), alias="Errors")


class OrderConfirmation(TradeStationEnvelope):
    order_confirm_id: str | None = Field(default=None, alias="OrderConfirmID")
    estimated_cost: Decimal | None = Field(default=None, alias="EstimatedCost")
    buying_power_effect: Decimal | None = Field(default=None, alias="BuyingPowerEffect")
    warnings: tuple[dict[str, Any], ...] = Field(default=(), alias="Warnings")
    errors: tuple[dict[str, Any], ...] = Field(default=(), alias="Errors")


class UnknownOrderFingerprint(BaseModel):
    account_id: str
    symbol: str
    trade_action: TradeAction
    order_type: OrderType
    quantity: Decimal
    submitted_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    payload_hash: str


from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradestation_api_wrapper.order_status import (
    TradeStationOrderStatus,
    normalize_order_status,
    order_status_can_cancel,
    order_status_can_replace,
    order_status_is_active,
    order_status_is_done,
    order_status_is_working,
)


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


class BarUnit(str, Enum):
    MINUTE = "Minute"
    DAILY = "Daily"
    WEEKLY = "Weekly"
    MONTHLY = "Monthly"


class BarSessionTemplate(str, Enum):
    USEQ_PRE = "USEQPre"
    USEQ_POST = "USEQPost"
    USEQ_PRE_AND_POST = "USEQPreAndPost"
    USEQ_24_HOUR = "USEQ24Hour"
    DEFAULT = "Default"


class _BaseBarChartParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    interval: int = Field(default=1, alias="interval")
    unit: BarUnit = Field(default=BarUnit.DAILY, alias="unit")
    bars_back: int | None = Field(default=None, alias="barsback")
    session_template: BarSessionTemplate | None = Field(
        default=None,
        alias="sessiontemplate",
    )

    @field_validator("interval")
    @classmethod
    def require_positive_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("bar interval must be positive")
        return value

    @field_validator("bars_back")
    @classmethod
    def require_positive_bars_back(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("bars_back must be positive")
        return value


class BarChartParams(_BaseBarChartParams):
    first_date: date | datetime | None = Field(default=None, alias="firstdate")
    last_date: date | datetime | None = Field(default=None, alias="lastdate")
    start_date: date | datetime | None = Field(default=None, alias="startdate")

    @model_validator(mode="after")
    def reject_conflicting_date_ranges(self) -> "BarChartParams":
        if self.first_date is not None and self.bars_back is not None:
            raise ValueError("first_date and bars_back are mutually exclusive")
        if self.last_date is not None and self.start_date is not None:
            raise ValueError("last_date and start_date are mutually exclusive")
        return self


class StreamBarChartParams(_BaseBarChartParams):
    pass


class OptionChainStreamParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expiration: date | datetime | str | None = Field(default=None, alias="expiration")
    expiration2: date | datetime | str | None = Field(default=None, alias="expiration2")
    strike_proximity: int | None = Field(default=None, alias="strikeProximity")
    spread_type: str | None = Field(default=None, alias="spreadType")
    risk_free_rate: Decimal | None = Field(default=None, alias="riskFreeRate")
    price_center: Decimal | None = Field(default=None, alias="priceCenter")
    strike_interval: int | None = Field(default=None, alias="strikeInterval")
    enable_greeks: bool | None = Field(default=None, alias="enableGreeks")
    strike_range: str | None = Field(default=None, alias="strikeRange")
    option_type: str | None = Field(default=None, alias="optionType")

    @field_validator("strike_proximity", "strike_interval")
    @classmethod
    def require_positive_integer(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("option-chain integer parameters must be positive")
        return value

    @field_validator("risk_free_rate", "price_center")
    @classmethod
    def require_positive_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("option-chain decimal parameters must be positive")
        return value


class GroupType(str, Enum):
    NORMAL = "NORMAL"
    OCO = "OCO"
    BRACKET = "BRK"


class TimeInForce(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    duration: Duration = Field(alias="Duration")
    expiration: datetime | None = Field(default=None, alias="Expiration")

    @model_validator(mode="after")
    def require_expiration_for_dated_duration(self) -> "TimeInForce":
        if self.duration in {Duration.GTD, Duration.GTD_PLUS} and self.expiration is None:
            raise ValueError("GTD durations require Expiration")
        return self


class TrailingStop(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    amount: Decimal | None = Field(default=None, alias="Amount")
    percent: Decimal | None = Field(default=None, alias="Percent")

    @model_validator(mode="after")
    def require_one_offset(self) -> "TrailingStop":
        if self.amount is None and self.percent is None:
            raise ValueError("trailing stop requires Amount or Percent")
        if self.amount is not None and self.percent is not None:
            raise ValueError("trailing stop Amount and Percent are mutually exclusive")
        if self.amount is not None and self.amount <= 0:
            raise ValueError("trailing stop Amount must be positive")
        if self.percent is not None and self.percent <= 0:
            raise ValueError("trailing stop Percent must be positive")
        return self


class AdvancedOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    add_liquidity: bool | None = Field(default=None, alias="AddLiquidity")
    all_or_none: bool | None = Field(default=None, alias="AllOrNone")
    book_only: bool | None = Field(default=None, alias="BookOnly")
    discretionary_price: Decimal | None = Field(default=None, alias="DiscretionaryPrice")
    market_activation_rules: tuple[dict[str, Any], ...] = Field(
        default=(),
        alias="MarketActivationRules",
    )
    non_display: bool | None = Field(default=None, alias="NonDisplay")
    peg_value: str | None = Field(default=None, alias="PegValue")
    show_only_quantity: Decimal | None = Field(default=None, alias="ShowOnlyQuantity")
    time_activation_rules: tuple[dict[str, Any], ...] = Field(
        default=(),
        alias="TimeActivationRules",
    )
    trailing_stop: TrailingStop | None = Field(default=None, alias="TrailingStop")

    @field_validator("discretionary_price", "show_only_quantity")
    @classmethod
    def require_positive_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("advanced option decimal values must be positive")
        return value


class ActivationRulesReplace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    clear_all: bool | None = Field(default=None, alias="ClearAll")
    rules: tuple[dict[str, Any], ...] = Field(default=(), alias="Rules")

    @model_validator(mode="after")
    def require_clear_all_or_rules(self) -> "ActivationRulesReplace":
        if not self.clear_all and not self.rules:
            raise ValueError("replace activation rules require ClearAll or Rules")
        return self


class AdvancedOptionsReplace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    market_activation_rules: ActivationRulesReplace | None = Field(
        default=None,
        alias="MarketActivationRules",
    )
    show_only_quantity: Decimal | None = Field(default=None, alias="ShowOnlyQuantity")
    time_activation_rules: ActivationRulesReplace | None = Field(
        default=None,
        alias="TimeActivationRules",
    )
    trailing_stop: TrailingStop | None = Field(default=None, alias="TrailingStop")

    @field_validator("show_only_quantity")
    @classmethod
    def require_positive_show_only_quantity(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("ShowOnlyQuantity must be positive")
        return value

    @model_validator(mode="after")
    def require_one_replace_option(self) -> "AdvancedOptionsReplace":
        if (
            self.market_activation_rules is None
            and self.show_only_quantity is None
            and self.time_activation_rules is None
            and self.trailing_stop is None
        ):
            raise ValueError("replace advanced options require at least one option")
        return self


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
    advanced_options: AdvancedOptions | None = Field(default=None, alias="AdvancedOptions")
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
        if len({order.account_id for order in self.orders}) != 1:
            raise ValueError("TradeStation order groups must use one account")
        if len({order.symbol for order in self.orders}) != 1:
            raise ValueError("TradeStation order groups must use one symbol")
        return self


class OrderReplaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    advanced_options: AdvancedOptionsReplace | None = Field(default=None, alias="AdvancedOptions")
    limit_price: Decimal | None = Field(default=None, alias="LimitPrice")
    order_type: OrderType | None = Field(default=None, alias="OrderType")
    quantity: Decimal | None = Field(default=None, alias="Quantity")
    stop_price: Decimal | None = Field(default=None, alias="StopPrice")

    @field_validator("quantity")
    @classmethod
    def require_positive_quantity(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @field_validator("limit_price", "stop_price")
    @classmethod
    def require_positive_price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("price must be positive")
        return value

    @model_validator(mode="after")
    def validate_replace_shape(self) -> "OrderReplaceRequest":
        if (
            self.advanced_options is None
            and self.limit_price is None
            and self.order_type is None
            and self.quantity is None
            and self.stop_price is None
        ):
            raise ValueError("replace request requires at least one updated property")
        if self.order_type is not None and self.order_type is not OrderType.MARKET:
            raise ValueError("replace request OrderType can only convert to Market")
        return self


class TradeStationEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AccountDetail(TradeStationEnvelope):
    day_trading_qualified: bool | None = Field(default=None, alias="DayTradingQualified")
    enrolled_in_reg_t_program: bool | None = Field(default=None, alias="EnrolledInRegTProgram")
    is_stock_locate_eligible: bool | None = Field(default=None, alias="IsStockLocateEligible")
    option_approval_level: int | None = Field(default=None, alias="OptionApprovalLevel")
    pattern_day_trader: bool | None = Field(default=None, alias="PatternDayTrader")
    requires_buying_power_warning: bool | None = Field(
        default=None,
        alias="RequiresBuyingPowerWarning",
    )


class AccountSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    status: str | None = Field(default=None, alias="Status")
    account_type: str | None = Field(default=None, alias="AccountType")
    account_detail: AccountDetail | None = Field(default=None, alias="AccountDetail")
    alias: str | None = Field(default=None, alias="Alias")
    alt_id: str | None = Field(default=None, alias="AltID")
    currency: str | None = Field(default=None, alias="Currency")


class BalanceDetail(TradeStationEnvelope):
    cost_of_positions: Decimal | None = Field(default=None, alias="CostOfPositions")
    day_trade_excess: Decimal | None = Field(default=None, alias="DayTradeExcess")
    day_trade_margin: Decimal | None = Field(default=None, alias="DayTradeMargin")
    day_trade_open_order_margin: Decimal | None = Field(
        default=None,
        alias="DayTradeOpenOrderMargin",
    )
    day_trades: Decimal | None = Field(default=None, alias="DayTrades")
    initial_margin: Decimal | None = Field(default=None, alias="InitialMargin")
    maintenance_margin: Decimal | None = Field(default=None, alias="MaintenanceMargin")
    maintenance_rate: Decimal | None = Field(default=None, alias="MaintenanceRate")
    margin_requirement: Decimal | None = Field(default=None, alias="MarginRequirement")
    open_order_margin: Decimal | None = Field(default=None, alias="OpenOrderMargin")
    option_buying_power: Decimal | None = Field(default=None, alias="OptionBuyingPower")
    options_market_value: Decimal | None = Field(default=None, alias="OptionsMarketValue")
    overnight_buying_power: Decimal | None = Field(default=None, alias="OvernightBuyingPower")
    realized_profit_loss: Decimal | None = Field(default=None, alias="RealizedProfitLoss")
    required_margin: Decimal | None = Field(default=None, alias="RequiredMargin")
    security_on_deposit: Decimal | None = Field(default=None, alias="SecurityOnDeposit")
    today_real_time_trade_equity: Decimal | None = Field(
        default=None,
        alias="TodayRealTimeTradeEquity",
    )
    trade_equity: Decimal | None = Field(default=None, alias="TradeEquity")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="UnrealizedProfitLoss")
    unsettled_funds: Decimal | None = Field(default=None, alias="UnsettledFunds")


class CurrencyDetail(TradeStationEnvelope):
    account_conversion_rate: Decimal | None = Field(default=None, alias="AccountConversionRate")
    account_margin_requirement: Decimal | None = Field(
        default=None,
        alias="AccountMarginRequirement",
    )
    cash_balance: Decimal | None = Field(default=None, alias="CashBalance")
    commission: Decimal | None = Field(default=None, alias="Commission")
    currency: str | None = Field(default=None, alias="Currency")
    initial_margin: Decimal | None = Field(default=None, alias="InitialMargin")
    maintenance_margin: Decimal | None = Field(default=None, alias="MaintenanceMargin")
    realized_profit_loss: Decimal | None = Field(default=None, alias="RealizedProfitLoss")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="UnrealizedProfitLoss")


class BalanceSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    account_type: str | None = Field(default=None, alias="AccountType")
    balance_detail: BalanceDetail | None = Field(default=None, alias="BalanceDetail")
    buying_power: Decimal | None = Field(default=None, alias="BuyingPower")
    equity: Decimal | None = Field(default=None, alias="Equity")
    todays_profit_loss: Decimal | None = Field(default=None, alias="TodaysProfitLoss")
    cash_balance: Decimal | None = Field(default=None, alias="CashBalance")
    commission: Decimal | None = Field(default=None, alias="Commission")
    currency_details: tuple[CurrencyDetail, ...] = Field(default=(), alias="CurrencyDetails")
    market_value: Decimal | None = Field(default=None, alias="MarketValue")
    uncleared_deposit: Decimal | None = Field(default=None, alias="UnclearedDeposit")


class BODBalanceDetail(TradeStationEnvelope):
    account_balance: Decimal | None = Field(default=None, alias="AccountBalance")
    cash_available_to_withdraw: Decimal | None = Field(
        default=None,
        alias="CashAvailableToWithdraw",
    )
    day_trades: Decimal | None = Field(default=None, alias="DayTrades")
    day_trading_marginable_buying_power: Decimal | None = Field(
        default=None,
        alias="DayTradingMarginableBuyingPower",
    )
    equity: Decimal | None = Field(default=None, alias="Equity")
    net_cash: Decimal | None = Field(default=None, alias="NetCash")
    open_trade_equity: Decimal | None = Field(default=None, alias="OpenTradeEquity")
    option_buying_power: Decimal | None = Field(default=None, alias="OptionBuyingPower")
    option_value: Decimal | None = Field(default=None, alias="OptionValue")
    overnight_buying_power: Decimal | None = Field(default=None, alias="OvernightBuyingPower")
    security_on_deposit: Decimal | None = Field(default=None, alias="SecurityOnDeposit")


class BODCurrencyDetail(TradeStationEnvelope):
    account_margin_requirement: Decimal | None = Field(
        default=None,
        alias="AccountMarginRequirement",
    )
    account_open_trade_equity: Decimal | None = Field(default=None, alias="AccountOpenTradeEquity")
    account_securities: Decimal | None = Field(default=None, alias="AccountSecurities")
    cash_balance: Decimal | None = Field(default=None, alias="CashBalance")
    currency: str | None = Field(default=None, alias="Currency")
    margin_requirement: Decimal | None = Field(default=None, alias="MarginRequirement")
    open_trade_equity: Decimal | None = Field(default=None, alias="OpenTradeEquity")
    securities: Decimal | None = Field(default=None, alias="Securities")


class BODBalanceSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    account_type: str | None = Field(default=None, alias="AccountType")
    balance_detail: BODBalanceDetail | None = Field(default=None, alias="BalanceDetail")
    currency_details: tuple[BODCurrencyDetail, ...] = Field(default=(), alias="CurrencyDetails")


class PositionSnapshot(TradeStationEnvelope):
    account_id: str = Field(alias="AccountID")
    symbol: str = Field(alias="Symbol")
    quantity: Decimal = Field(alias="Quantity")
    asset_type: str | None = Field(default=None, alias="AssetType")
    average_price: Decimal | None = Field(default=None, alias="AveragePrice")
    long_short: str | None = Field(default=None, alias="LongShort")
    bid: Decimal | None = Field(default=None, alias="Bid")
    ask: Decimal | None = Field(default=None, alias="Ask")
    last: Decimal | None = Field(default=None, alias="Last")
    market_value: Decimal | None = Field(default=None, alias="MarketValue")
    position_id: str | None = Field(default=None, alias="PositionID")
    timestamp: datetime | None = Field(default=None, alias="Timestamp")
    todays_profit_loss: Decimal | None = Field(default=None, alias="TodaysProfitLoss")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="UnrealizedProfitLoss")

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class OrderLegSnapshot(TradeStationEnvelope):
    symbol: str | None = Field(default=None, alias="Symbol")
    buy_or_sell: str | None = Field(default=None, alias="BuyOrSell")
    quantity_ordered: Decimal | None = Field(default=None, alias="QuantityOrdered")
    quantity_remaining: Decimal | None = Field(default=None, alias="QuantityRemaining")
    exec_quantity: Decimal | None = Field(default=None, alias="ExecQuantity")
    execution_price: Decimal | None = Field(default=None, alias="ExecutionPrice")

    @property
    def filled_quantity(self) -> Decimal:
        return self.exec_quantity or Decimal("0")

    @property
    def remaining_quantity(self) -> Decimal | None:
        return self.quantity_remaining

    @property
    def ordered_quantity(self) -> Decimal | None:
        return self.quantity_ordered


class OrderSnapshot(TradeStationEnvelope):
    account_id: str | None = Field(default=None, alias="AccountID")
    order_id: str = Field(alias="OrderID")
    status: str | None = Field(default=None, alias="Status")
    symbol: str | None = Field(default=None, alias="Symbol")
    order_type: OrderType | None = Field(default=None, alias="OrderType")
    limit_price: Decimal | None = Field(default=None, alias="LimitPrice")
    stop_price: Decimal | None = Field(default=None, alias="StopPrice")
    opened_at: datetime | None = Field(default=None, alias="OpenedDateTime")
    closed_at: datetime | None = Field(default=None, alias="ClosedDateTime")
    filled_price: Decimal | None = Field(default=None, alias="FilledPrice")
    status_description: str | None = Field(default=None, alias="StatusDescription")
    reject_reason: str | None = Field(default=None, alias="RejectReason")
    group_name: str | None = Field(default=None, alias="GroupName")
    legs: tuple[OrderLegSnapshot, ...] = Field(default=(), alias="Legs")

    @property
    def status_value(self) -> TradeStationOrderStatus | None:
        return normalize_order_status(self.status)

    @property
    def is_active(self) -> bool:
        return order_status_is_active(self.status_value)

    @property
    def is_done(self) -> bool:
        return order_status_is_done(self.status_value)

    @property
    def is_working(self) -> bool:
        return order_status_is_working(self.status_value)

    @property
    def can_cancel(self) -> bool:
        return order_status_can_cancel(self.status_value)

    @property
    def can_replace(self) -> bool:
        return order_status_can_replace(self.status_value)

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

    @property
    def ordered_quantity(self) -> Decimal | None:
        return self.primary_quantity()

    @property
    def filled_quantity(self) -> Decimal:
        return sum((leg.filled_quantity for leg in self.legs), Decimal("0"))

    @property
    def remaining_quantity(self) -> Decimal | None:
        if not self.legs:
            return None
        quantities = [leg.remaining_quantity for leg in self.legs]
        if any(quantity is None for quantity in quantities):
            return None
        return sum((quantity for quantity in quantities if quantity is not None), Decimal("0"))


class OrderAck(TradeStationEnvelope):
    order_id: str | None = Field(default=None, alias="OrderID")
    orders: tuple[dict[str, Any], ...] = Field(default=(), alias="Orders")
    errors: tuple[dict[str, Any], ...] = Field(default=(), alias="Errors")

    def first_order_id(self) -> str | None:
        if self.order_id:
            return self.order_id
        for order in self.orders:
            value = order.get("OrderID") or order.get("OrderId") or order.get("order_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


class OrderConfirmationDetail(TradeStationEnvelope):
    order_confirm_id: str | None = Field(default=None, alias="OrderConfirmID")
    estimated_cost: Decimal | None = Field(default=None, alias="EstimatedCost")
    buying_power_effect: Decimal | None = Field(default=None, alias="BuyingPowerEffect")
    warnings: tuple[dict[str, Any], ...] = Field(default=(), alias="Warnings")


class OrderConfirmation(TradeStationEnvelope):
    confirmations: tuple[OrderConfirmationDetail, ...] = Field(default=(), alias="Confirmations")
    errors: tuple[dict[str, Any], ...] = Field(default=(), alias="Errors")

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_flat_confirmation(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "Confirmations" in value:
            return value
        if "OrderConfirmID" not in value and "EstimatedCost" not in value:
            return value
        errors = value.get("Errors", ())
        confirmation = {key: item for key, item in value.items() if key != "Errors"}
        return {"Confirmations": (confirmation,), "Errors": errors}

    @property
    def first_confirmation(self) -> OrderConfirmationDetail | None:
        if not self.confirmations:
            return None
        return self.confirmations[0]

    @property
    def order_confirm_id(self) -> str | None:
        confirmation = self.first_confirmation
        if confirmation is None:
            return None
        return confirmation.order_confirm_id

    @property
    def estimated_cost(self) -> Decimal | None:
        confirmation = self.first_confirmation
        if confirmation is None:
            return None
        return confirmation.estimated_cost

    @property
    def buying_power_effect(self) -> Decimal | None:
        confirmation = self.first_confirmation
        if confirmation is None:
            return None
        return confirmation.buying_power_effect

    @property
    def warnings(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            warning
            for confirmation in self.confirmations
            for warning in confirmation.warnings
        )


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

    @field_validator("submitted_at")
    @classmethod
    def require_timezone_aware_submission_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware")
        return value


class QuoteSnapshot(TradeStationEnvelope):
    symbol: str = Field(alias="Symbol")
    bid: Decimal | None = Field(default=None, alias="Bid")
    bid_size: Decimal | None = Field(default=None, alias="BidSize")
    ask: Decimal | None = Field(default=None, alias="Ask")
    ask_size: Decimal | None = Field(default=None, alias="AskSize")
    last: Decimal | None = Field(default=None, alias="Last")
    last_size: Decimal | None = Field(default=None, alias="LastSize")
    open_: Decimal | None = Field(default=None, alias="Open")
    high: Decimal | None = Field(default=None, alias="High")
    low: Decimal | None = Field(default=None, alias="Low")
    close: Decimal | None = Field(default=None, alias="Close")
    previous_close: Decimal | None = Field(default=None, alias="PreviousClose")
    volume: Decimal | None = Field(default=None, alias="Volume")
    trade_time: datetime | None = Field(default=None, alias="TradeTime")

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")


class SymbolDetail(TradeStationEnvelope):
    symbol: str = Field(alias="Symbol")
    asset_type: str | None = Field(default=None, alias="AssetType")
    country: str | None = Field(default=None, alias="Country")
    currency: str | None = Field(default=None, alias="Currency")
    description: str | None = Field(default=None, alias="Description")
    exchange: str | None = Field(default=None, alias="Exchange")
    root: str | None = Field(default=None, alias="Root")
    underlying: str | None = Field(default=None, alias="Underlying")
    expiration_date: datetime | None = Field(default=None, alias="ExpirationDate")
    option_type: str | None = Field(default=None, alias="OptionType")
    strike_price: Decimal | None = Field(default=None, alias="StrikePrice")


class OptionExpiration(TradeStationEnvelope):
    date: datetime = Field(alias="Date")
    root: str | None = Field(default=None, alias="Root")
    type_: str | None = Field(default=None, alias="Type")


class OptionSpreadType(TradeStationEnvelope):
    name: str = Field(alias="Name")
    expiration_interval: bool | None = Field(default=None, alias="ExpirationInterval")
    strike_interval: bool | None = Field(default=None, alias="StrikeInterval")


class OptionStrikes(TradeStationEnvelope):
    spread_type: str | None = Field(default=None, alias="SpreadType")
    strikes: tuple[tuple[Decimal, ...], ...] = Field(default=(), alias="Strikes")


class OptionRiskRewardLeg(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    symbol: str = Field(alias="Symbol")
    quantity: Decimal = Field(alias="Quantity")
    trade_action: TradeAction = Field(alias="TradeAction")

    @field_validator("symbol")
    @classmethod
    def require_non_blank_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("option leg symbol must not be blank")
        return stripped

    @field_validator("quantity")
    @classmethod
    def require_positive_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("option leg quantity must be positive")
        if value != value.to_integral_value():
            raise ValueError("option leg quantity must be a whole number")
        return value


class OptionRiskRewardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    legs: tuple[OptionRiskRewardLeg, ...] = Field(alias="Legs")
    spread_price: Decimal = Field(alias="SpreadPrice")

    @model_validator(mode="after")
    def require_at_least_one_leg(self) -> "OptionRiskRewardRequest":
        if not self.legs:
            raise ValueError("option risk/reward request requires at least one leg")
        return self


class OptionRiskReward(TradeStationEnvelope):
    adjusted_max_gain: Decimal | None = Field(default=None, alias="AdjustedMaxGain")
    adjusted_max_loss: Decimal | None = Field(default=None, alias="AdjustedMaxLoss")
    breakeven_points: tuple[Decimal, ...] = Field(default=(), alias="BreakevenPoints")
    max_gain_is_infinite: bool | None = Field(default=None, alias="MaxGainIsInfinite")
    max_loss_is_infinite: bool | None = Field(default=None, alias="MaxLossIsInfinite")


class OptionQuoteLeg(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    symbol: str = Field(alias="Symbol")
    ratio: Decimal = Field(alias="Ratio")

    @field_validator("symbol")
    @classmethod
    def require_non_blank_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("option quote leg symbol must not be blank")
        return stripped

    @field_validator("ratio")
    @classmethod
    def require_non_zero_ratio(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("option quote leg ratio must not be zero")
        return value


class BarSnapshot(TradeStationEnvelope):
    timestamp: datetime | None = Field(default=None, alias="TimeStamp")
    open_: Decimal | None = Field(default=None, alias="Open")
    high: Decimal | None = Field(default=None, alias="High")
    low: Decimal | None = Field(default=None, alias="Low")
    close: Decimal | None = Field(default=None, alias="Close")
    total_volume: Decimal | None = Field(default=None, alias="TotalVolume")
    bar_status: str | None = Field(default=None, alias="BarStatus")
    is_realtime: bool | None = Field(default=None, alias="IsRealtime")
    is_end_of_history: bool | None = Field(default=None, alias="IsEndOfHistory")


class AccountStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    accounts: tuple[AccountSnapshot, ...]
    balances: tuple[BalanceSnapshot, ...]
    positions: tuple[PositionSnapshot, ...]
    orders: tuple[OrderSnapshot, ...]

    @property
    def open_orders(self) -> tuple[OrderSnapshot, ...]:
        return tuple(order for order in self.orders if order.is_active)

    @property
    def nonzero_positions(self) -> tuple[PositionSnapshot, ...]:
        return tuple(position for position in self.positions if not position.is_flat)

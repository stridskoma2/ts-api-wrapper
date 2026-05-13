from __future__ import annotations

from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from tradestation_api_wrapper.errors import ConfigurationError

SIM_BASE_URL = "https://sim-api.tradestation.com/v3"
LIVE_BASE_URL = "https://api.tradestation.com/v3"
LIVE_ACKNOWLEDGEMENT = "I_ACKNOWLEDGE_LIVE_TRADESTATION_TRADING"
REQUIRED_SCOPES = frozenset({"openid", "offline_access"})
TRADE_SCOPE = "Trade"
MARKET_DATA_SCOPE = "MarketData"
READ_ACCOUNT_SCOPE = "ReadAccount"
OPTION_SPREADS_SCOPE = "OptionSpreads"
MATRIX_SCOPE = "Matrix"


class Environment(str, Enum):
    SIM = "SIM"
    LIVE = "LIVE"


class TradeStationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: Environment
    base_url: str
    client_id: str
    client_secret: SecretStr | None = None
    redirect_uri: str = "http://localhost:31022/callback"
    requested_scopes: tuple[str, ...] = ("openid", "offline_access", "MarketData", "ReadAccount")
    account_allowlist: tuple[str, ...]
    live_trading_enabled: bool = False
    live_acknowledgement: str | None = None
    max_order_notional: Decimal = Decimal("1000")
    max_symbol_position_notional: Decimal = Decimal("5000")
    max_daily_loss: Decimal = Decimal("500")
    max_daily_order_count: int = 20
    allow_market_orders: bool = False
    allow_options: bool = False
    allow_futures: bool = False
    allow_extended_hours: bool = False
    kill_switch_file: Path | None = None

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("client_id", "redirect_uri")
    @classmethod
    def reject_unresolved_placeholders(cls, value: str) -> str:
        if "${" in value or "}" in value:
            raise ValueError("configuration contains unresolved placeholder")
        if not value.strip():
            raise ValueError("configuration value must not be blank")
        return value.strip()

    @field_validator("client_secret")
    @classmethod
    def reject_unresolved_secret_placeholder(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value()
        if "${" in secret or "}" in secret:
            raise ValueError("configuration contains unresolved placeholder")
        return value

    @field_validator("account_allowlist")
    @classmethod
    def require_unique_account_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(account.strip() for account in value if account.strip())
        if not cleaned:
            raise ValueError("account_allowlist must contain at least one account")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("account_allowlist contains duplicate account IDs")
        return cleaned

    @field_validator("requested_scopes")
    @classmethod
    def require_refresh_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        scopes = {scope.strip() for scope in value if scope.strip()}
        missing = REQUIRED_SCOPES.difference(scopes)
        if missing:
            raise ValueError(f"requested_scopes missing required scopes: {sorted(missing)}")
        return tuple(sorted(scopes))

    @model_validator(mode="after")
    def validate_environment_contract(self) -> "TradeStationConfig":
        if self.environment is Environment.SIM and self.base_url != SIM_BASE_URL:
            raise ValueError(f"SIM must use {SIM_BASE_URL}")
        if self.environment is Environment.LIVE:
            if self.base_url != LIVE_BASE_URL:
                raise ValueError(f"LIVE must use {LIVE_BASE_URL}")
            if not self.live_trading_enabled:
                raise ValueError("LIVE requires live_trading_enabled=true")
            if self.live_acknowledgement != LIVE_ACKNOWLEDGEMENT:
                raise ValueError("LIVE requires explicit live_acknowledgement")
        return self

    def assert_account_allowed(self, account_id: str) -> None:
        if account_id not in self.account_allowlist:
            raise ConfigurationError(f"account {account_id!r} is not allowlisted")

    def assert_can_submit_orders(self, account_id: str) -> None:
        self.assert_account_allowed(account_id)
        if self.kill_switch_file is not None and self.kill_switch_file.exists():
            raise ConfigurationError(f"kill switch is active: {self.kill_switch_file}")

    def assert_can_replace_orders(self, account_id: str) -> None:
        self.assert_can_submit_orders(account_id)

    def assert_can_cancel_orders(self, account_id: str) -> None:
        self.assert_account_allowed(account_id)

    def assert_scope_requested(self, scope: str) -> None:
        if scope not in self.requested_scopes:
            raise ConfigurationError(f"requested_scopes missing required scope: {scope}")

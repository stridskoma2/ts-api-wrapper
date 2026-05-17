from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TradeStationWrapperError(Exception):
    """Base class for wrapper errors."""


class ConfigurationError(TradeStationWrapperError):
    """Raised when SIM/LIVE or credential configuration is unsafe."""


class RequestValidationError(TradeStationWrapperError):
    """Raised before a request reaches TradeStation."""


class TransportError(TradeStationWrapperError):
    """Raised when the HTTP transport fails before a response is available."""


class NetworkTimeout(TransportError):
    """Raised when a transport timeout leaves request outcome unknown."""


class RetryExhausted(TradeStationWrapperError):
    """Raised when a safe request exhausts its retry budget."""


class PaginationError(TradeStationWrapperError):
    """Raised when paginated API responses do not terminate safely."""


@dataclass(slots=True)
class TradeStationAPIError(TradeStationWrapperError):
    status_code: int
    error: str
    message: str
    payload: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"TradeStation API {self.status_code} {self.error}: {self.message}"


@dataclass(slots=True)
class RateLimitError(TradeStationAPIError):
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class AuthenticationError(TradeStationAPIError):
    pass


@dataclass(slots=True)
class AmbiguousOrderState(TradeStationWrapperError):
    operation: str
    local_request_id: str | None
    cause: BaseException

    def __str__(self) -> str:
        request_id = self.local_request_id or "unknown-request"
        return (
            f"{self.operation} for {request_id} ended without a definitive broker "
            f"response; reconcile before retrying"
        )


class StreamParseError(TradeStationWrapperError):
    """Raised when a stream chunk contains malformed JSON."""


@dataclass(slots=True)
class StreamError(TradeStationWrapperError):
    message: str
    payload: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class CapabilityError(TradeStationWrapperError):
    """Raised when TradeStation cannot safely support the requested behavior."""

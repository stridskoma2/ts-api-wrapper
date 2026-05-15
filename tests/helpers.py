from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tradestation_api_wrapper.config import SIM_BASE_URL, Environment, TradeStationConfig
from tradestation_api_wrapper.transport import HTTPRequest, HTTPResponse


@dataclass
class FakeTransport:
    responses: list[HTTPResponse | BaseException]
    streams: list[list[bytes | str] | BaseException] = field(default_factory=list)
    requests: list[HTTPRequest] = field(default_factory=list)

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        self.requests.append(request)
        stream = self.streams.pop(0)
        if isinstance(stream, BaseException):
            raise stream
        for chunk in stream:
            if isinstance(chunk, str):
                yield chunk.encode("utf-8")
            else:
                yield chunk


class FakeTokenProvider:
    def __init__(self, token: str = "first-token", refreshed: str = "refreshed-token") -> None:
        self.token = token
        self.refreshed = refreshed
        self.refresh_count = 0

    async def get_access_token(self) -> str:
        return self.token

    async def force_refresh_access_token(self) -> str:
        self.refresh_count += 1
        self.token = self.refreshed
        return self.token


def json_response(
    status_code: int,
    payload: Any,
    headers: dict[str, str] | None = None,
) -> HTTPResponse:
    return HTTPResponse(
        status_code=status_code,
        headers=headers or {},
        body=json.dumps(payload).encode("utf-8"),
    )


def sim_config(**overrides: Any) -> TradeStationConfig:
    values: dict[str, Any] = {
        "environment": Environment.SIM,
        "base_url": SIM_BASE_URL,
        "client_id": "client",
        "client_secret": "secret",
        "requested_scopes": (
            "openid",
            "offline_access",
            "MarketData",
            "ReadAccount",
            "Trade",
            "OptionSpreads",
            "Matrix",
        ),
        "account_allowlist": ("123456789",),
        "max_order_notional": Decimal("1000"),
    }
    values.update(overrides)
    return TradeStationConfig(**values)

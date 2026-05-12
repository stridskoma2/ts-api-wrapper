from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from tradestation_api_wrapper.config import TradeStationConfig
from tradestation_api_wrapper.errors import (
    AmbiguousOrderState,
    AuthenticationError,
    NetworkTimeout,
    RateLimitError,
    RetryExhausted,
    TradeStationAPIError,
    TransportError,
)
from tradestation_api_wrapper.rate_limit import RetryPolicy, Sleeper, sleep_with_policy
from tradestation_api_wrapper.redaction import redact
from tradestation_api_wrapper.transport import AsyncTransport, HTTPRequest, HTTPResponse
from tradestation_api_wrapper.stream import StreamEvent, TradeStationStream


class AccessTokenProvider(Protocol):
    async def get_access_token(self) -> str:
        ...

    async def force_refresh_access_token(self) -> str:
        ...


class StaticTokenProvider:
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    async def get_access_token(self) -> str:
        return self._access_token

    async def force_refresh_access_token(self) -> str:
        return self._access_token


class TradeStationRestClient:
    def __init__(
        self,
        *,
        config: TradeStationConfig,
        token_provider: AccessTokenProvider,
        transport: AsyncTransport,
        retry_policy: RetryPolicy | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._config = config
        self._token_provider = token_provider
        self._transport = transport
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleeper = sleeper

    async def get(self, path: str) -> dict[str, Any]:
        return await self.request_json("GET", path, retry_safe=True)

    async def post_confirm(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request_json("POST", path, payload=payload, retry_safe=True)

    async def post_order_write(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        local_request_id: str | None,
    ) -> dict[str, Any]:
        return await self.request_json(
            "POST",
            path,
            payload=payload,
            retry_safe=False,
            ambiguous_operation="submit order",
            local_request_id=local_request_id,
        )

    async def put_order_write(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        local_request_id: str | None,
    ) -> dict[str, Any]:
        return await self.request_json(
            "PUT",
            path,
            payload=payload,
            retry_safe=False,
            ambiguous_operation="replace order",
            local_request_id=local_request_id,
        )

    async def delete_order_write(self, path: str, *, local_request_id: str | None) -> dict[str, Any]:
        return await self.request_json(
            "DELETE",
            path,
            retry_safe=False,
            ambiguous_operation="cancel order",
            local_request_id=local_request_id,
        )

    def stream_events(
        self,
        path: str,
        *,
        accept: str = "application/vnd.tradestation.streams.v3+json",
    ) -> AsyncIterator[StreamEvent]:
        stream = TradeStationStream(lambda: self._stream_chunks(path, accept=accept))
        return stream.events()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        retry_safe: bool,
        ambiguous_operation: str | None = None,
        local_request_id: str | None = None,
    ) -> dict[str, Any]:
        attempt = 1
        refreshed_after_401 = False
        while True:
            token = await self._token_provider.get_access_token()
            request = HTTPRequest(
                method=method,
                url=self._url(path),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                json_body=payload,
            )
            try:
                response = await self._transport.send(request)
            except NetworkTimeout as exc:
                if not retry_safe:
                    raise AmbiguousOrderState(ambiguous_operation or method, local_request_id, exc) from exc
                if attempt >= self._retry_policy.max_attempts:
                    raise RetryExhausted(str(redact(exc))) from exc
                await self._sleep(attempt)
                attempt += 1
                continue
            except TransportError as exc:
                if not retry_safe:
                    raise AmbiguousOrderState(ambiguous_operation or method, local_request_id, exc) from exc
                if attempt >= self._retry_policy.max_attempts:
                    raise RetryExhausted(str(redact(exc))) from exc
                await self._sleep(attempt)
                attempt += 1
                continue

            if response.status_code == 401 and not refreshed_after_401:
                refreshed_after_401 = True
                await self._token_provider.force_refresh_access_token()
                continue
            if _is_success(response):
                decoded = response.json()
                if not isinstance(decoded, dict):
                    raise TradeStationAPIError(
                        response.status_code,
                        "InvalidResponse",
                        "expected JSON object response",
                        {"response": decoded},
                    )
                return decoded
            if retry_safe and _is_retryable(response) and attempt < self._retry_policy.max_attempts:
                await self._sleep(attempt, response.headers.get("Retry-After"))
                attempt += 1
                continue
            raise _api_error(response)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self._config.base_url + path

    async def _stream_chunks(self, path: str, *, accept: str) -> AsyncIterator[bytes]:
        token = await self._token_provider.get_access_token()
        request = HTTPRequest(
            method="GET",
            url=self._url(path),
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
            },
        )
        async for chunk in self._transport.stream(request):
            yield chunk

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        sleeper = self._sleeper
        if sleeper is None:
            await sleep_with_policy(self._retry_policy, attempt, retry_after)
        else:
            await sleep_with_policy(self._retry_policy, attempt, retry_after, sleeper=sleeper)


def _is_success(response: HTTPResponse) -> bool:
    return 200 <= response.status_code < 300


def _is_retryable(response: HTTPResponse) -> bool:
    return response.status_code in {408, 429, 500, 502, 503, 504}


def _api_error(response: HTTPResponse) -> TradeStationAPIError:
    payload = _response_payload(response)
    error = str(payload.get("Error", payload.get("error", "TradeStationAPIError")))
    message = str(payload.get("Message", payload.get("message", response.text())))
    if response.status_code == 401:
        return AuthenticationError(response.status_code, error, message, payload)
    if response.status_code == 429:
        return RateLimitError(
            response.status_code,
            error,
            message,
            payload,
            _retry_after(response),
        )
    return TradeStationAPIError(response.status_code, error, message, payload)


def _response_payload(response: HTTPResponse) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"Message": response.text()}
    if isinstance(payload, dict):
        return payload
    return {"Message": str(payload)}


def _retry_after(response: HTTPResponse) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

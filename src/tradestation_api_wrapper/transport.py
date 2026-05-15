from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from tradestation_api_wrapper.errors import ConfigurationError, NetworkTimeout, TransportError


@dataclass(frozen=True, slots=True)
class HTTPRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    form_body: dict[str, str] | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.text())


@dataclass(slots=True)
class HTTPStreamOpenError(TransportError):
    status_code: int
    headers: dict[str, str]
    body: bytes

    def __str__(self) -> str:
        return f"stream open failed with HTTP {self.status_code}: {self.body!r}"


class AsyncTransport(Protocol):
    async def send(self, request: HTTPRequest) -> HTTPResponse:
        ...

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        ...


class UrllibAsyncTransport:
    async def send(self, request: HTTPRequest) -> HTTPResponse:
        return await asyncio.to_thread(self._send_sync, request)

    def _send_sync(self, request: HTTPRequest) -> HTTPResponse:
        data: bytes | None = None
        headers = dict(request.headers)
        if request.json_body is not None:
            data = json.dumps(request.json_body, separators=(",", ":"), default=str).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif request.form_body is not None:
            data = urllib.parse.urlencode(request.form_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        urllib_request = urllib.request.Request(
            request.url,
            data=data,
            headers=headers,
            method=request.method.upper(),
        )
        try:
            with urllib.request.urlopen(urllib_request, timeout=request.timeout_seconds) as response:
                return HTTPResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HTTPResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )
        except (TimeoutError, socket.timeout) as exc:
            raise NetworkTimeout(str(exc)) from exc
        except OSError as exc:
            raise TransportError(str(exc)) from exc

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        stream = await asyncio.to_thread(self._open_stream_sync, request)
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 8192)
                if not chunk:
                    break
                yield chunk
        except (TimeoutError, socket.timeout) as exc:
            raise NetworkTimeout(str(exc)) from exc
        except OSError as exc:
            raise TransportError(str(exc)) from exc
        finally:
            await asyncio.to_thread(stream.close)

    def _open_stream_sync(self, request: HTTPRequest) -> Any:
        headers = dict(request.headers)
        urllib_request = urllib.request.Request(
            request.url,
            headers=headers,
            method=request.method.upper(),
        )
        try:
            return urllib.request.urlopen(urllib_request, timeout=request.timeout_seconds)
        except urllib.error.HTTPError as exc:
            raise HTTPStreamOpenError(
                exc.code,
                dict(exc.headers.items()) if exc.headers else {},
                exc.read(),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise NetworkTimeout(str(exc)) from exc
        except OSError as exc:
            raise TransportError(str(exc)) from exc


class HttpxAsyncTransport:
    def __init__(self, client: Any | None = None) -> None:
        self._httpx = _load_httpx()
        self._client = client or self._httpx.AsyncClient()
        self._owns_client = client is None
        self._timeout_exception: type[BaseException] = self._httpx.TimeoutException
        self._http_exception: type[BaseException] = self._httpx.HTTPError

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        try:
            response = await self._client.request(
                request.method.upper(),
                request.url,
                headers=request.headers,
                json=request.json_body,
                data=request.form_body,
                timeout=request.timeout_seconds,
            )
        except self._timeout_exception as exc:
            raise NetworkTimeout(str(exc)) from exc
        except self._http_exception as exc:
            raise TransportError(str(exc)) from exc

        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            body=response.content,
        )

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        try:
            async with self._client.stream(
                request.method.upper(),
                request.url,
                headers=request.headers,
                timeout=request.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise HTTPStreamOpenError(
                        response.status_code,
                        dict(response.headers.items()),
                        body,
                    )
                async for chunk in response.aiter_bytes():
                    yield chunk
        except self._timeout_exception as exc:
            raise NetworkTimeout(str(exc)) from exc
        except self._http_exception as exc:
            raise TransportError(str(exc)) from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpxAsyncTransport":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def _load_httpx() -> Any:
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise ConfigurationError("install tradestation-api-wrapper[httpx] to use httpx") from exc
    return httpx

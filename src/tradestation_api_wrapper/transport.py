from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from tradestation_api_wrapper.errors import NetworkTimeout, TransportError


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


class AsyncTransport(Protocol):
    async def send(self, request: HTTPRequest) -> HTTPResponse:
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


from __future__ import annotations

import asyncio
import codecs
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from tradestation_api_wrapper.errors import (
    AuthenticationError,
    ConfigurationError,
    RateLimitError,
    TradeStationAPIError,
    StreamError,
    StreamParseError,
)


class StreamEventKind(str, Enum):
    DATA = "DATA"
    END_SNAPSHOT = "END_SNAPSHOT"
    GO_AWAY = "GO_AWAY"
    HEARTBEAT = "HEARTBEAT"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: StreamEventKind
    payload: dict[str, Any]


class JsonStreamParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")()
        self._decoder = json.JSONDecoder()

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        if isinstance(chunk, bytes):
            try:
                self._buffer += self._utf8_decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise StreamParseError("stream chunk was not valid UTF-8") from exc
        else:
            self._buffer += chunk

        messages: list[dict[str, Any]] = []
        while True:
            self._buffer = self._buffer.lstrip()
            if not self._buffer:
                return messages
            try:
                decoded, index = self._decoder.raw_decode(self._buffer)
            except json.JSONDecodeError as exc:
                if _looks_incomplete(self._buffer):
                    return messages
                message = f"malformed stream JSON at byte {exc.pos}: {exc.msg}"
                raise StreamParseError(message) from exc
            if not isinstance(decoded, dict):
                raise StreamParseError("TradeStation stream message must be a JSON object")
            messages.append(decoded)
            self._buffer = self._buffer[index:]


StreamChunkSource = Callable[[], AsyncIterator[bytes | str]]
StreamSleeper = Callable[[float], Awaitable[None]]


async def _default_stream_sleep(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)


@dataclass(frozen=True, slots=True)
class StreamReconnectPolicy:
    max_reconnects: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0
    sleeper: StreamSleeper = _default_stream_sleep

    def delay_for_reconnect(
        self,
        reconnect_number: int,
        retry_after_seconds: float | None,
    ) -> float:
        if retry_after_seconds is not None:
            return max(0.0, retry_after_seconds)
        multiplier = 2 ** max(0, reconnect_number - 1)
        delay_seconds = min(self.base_delay_seconds * multiplier, self.max_delay_seconds)
        return float(max(0.0, delay_seconds))


class TradeStationStream:
    def __init__(
        self,
        chunk_source: StreamChunkSource,
        *,
        reconnect_policy: StreamReconnectPolicy | None = None,
        raise_on_error: bool = True,
    ) -> None:
        self._chunk_source = chunk_source
        self._reconnect_policy = reconnect_policy or StreamReconnectPolicy()
        self._raise_on_error = raise_on_error

    async def events(self) -> AsyncIterator[StreamEvent]:
        reconnects = 0
        while True:
            parser = JsonStreamParser()
            try:
                async for chunk in self._chunk_source():
                    for payload in parser.feed(chunk):
                        event = classify_stream_message(payload)
                        if self._raise_on_error and event.kind is StreamEventKind.ERROR:
                            raise StreamError("TradeStation stream returned an error", payload)
                        yield event
                        if event.kind is StreamEventKind.GO_AWAY:
                            if reconnects >= self._reconnect_policy.max_reconnects:
                                return
                            reconnects += 1
                            break
                        reconnects = 0
                    else:
                        continue
                    break
                else:
                    return
            except (StreamError, StreamParseError):
                raise
            except (AuthenticationError, ConfigurationError):
                raise
            except RateLimitError as exc:
                if reconnects >= self._reconnect_policy.max_reconnects:
                    raise
                reconnects += 1
                await self._sleep_for_reconnect(reconnects, exc.retry_after_seconds)
                continue
            except TradeStationAPIError as exc:
                if 400 <= exc.status_code < 500:
                    raise
                if reconnects >= self._reconnect_policy.max_reconnects:
                    raise
                reconnects += 1
                await self._sleep_for_reconnect(reconnects, None)
                continue
            except Exception:
                if reconnects >= self._reconnect_policy.max_reconnects:
                    raise
                reconnects += 1
                await self._sleep_for_reconnect(reconnects, None)
                continue

    async def _sleep_for_reconnect(
        self,
        reconnect_number: int,
        retry_after_seconds: float | None,
    ) -> None:
        delay_seconds = self._reconnect_policy.delay_for_reconnect(
            reconnect_number,
            retry_after_seconds,
        )
        await self._reconnect_policy.sleeper(delay_seconds)


def classify_stream_message(payload: dict[str, Any]) -> StreamEvent:
    status = payload.get("StreamStatus")
    if status == "EndSnapshot":
        return StreamEvent(StreamEventKind.END_SNAPSHOT, payload)
    if status == "GoAway":
        return StreamEvent(StreamEventKind.GO_AWAY, payload)
    if "Heartbeat" in payload:
        return StreamEvent(StreamEventKind.HEARTBEAT, payload)
    if ("Error" in payload) or ("Message" in payload and not _looks_like_market_data(payload)):
        return StreamEvent(StreamEventKind.ERROR, payload)
    return StreamEvent(StreamEventKind.DATA, payload)


def _looks_incomplete(buffer: str) -> bool:
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in buffer:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append(char)
        elif char == "}":
            if not stack or stack.pop() != "{":
                return False
        elif char == "]":
            if not stack or stack.pop() != "[":
                return False
    return in_string or bool(stack)


def _looks_like_market_data(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "Symbol",
            "Bid",
            "Ask",
            "Last",
            "Close",
            "TimeStamp",
            "Bids",
            "Asks",
            "BidLevels",
            "AskLevels",
            "Side",
            "Price",
            "Size",
            "Entries",
        )
    )

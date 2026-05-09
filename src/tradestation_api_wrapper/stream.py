from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from tradestation_api_wrapper.errors import StreamParseError


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
        self._decoder = json.JSONDecoder()

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        if isinstance(chunk, bytes):
            self._buffer += chunk.decode("utf-8", errors="replace")
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
                raise StreamParseError(f"malformed stream JSON at byte {exc.pos}: {exc.msg}") from exc
            if not isinstance(decoded, dict):
                raise StreamParseError("TradeStation stream message must be a JSON object")
            messages.append(decoded)
            self._buffer = self._buffer[index:]


def classify_stream_message(payload: dict[str, Any]) -> StreamEvent:
    status = payload.get("StreamStatus")
    if status == "EndSnapshot":
        return StreamEvent(StreamEventKind.END_SNAPSHOT, payload)
    if status == "GoAway":
        return StreamEvent(StreamEventKind.GO_AWAY, payload)
    if "Heartbeat" in payload:
        return StreamEvent(StreamEventKind.HEARTBEAT, payload)
    if "Error" in payload or "Message" in payload and not _looks_like_market_data(payload):
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
    return any(key in payload for key in ("Symbol", "Bid", "Ask", "Last", "Close", "TimeStamp"))


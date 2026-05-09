from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEY_FRAGMENTS = (
    "access_token",
    "accountid",
    "account_id",
    "authorization",
    "client_secret",
    "orderconfirmid",
    "refresh_token",
    "token",
)
REDACTION = "[REDACTED]"
BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
AUTH0_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if any(fragment in normalized_key for fragment in SENSITIVE_KEY_FRAGMENTS):
                redacted[key] = REDACTION
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact(item) for item in value]
    return value


def redact_text(text: str) -> str:
    text = BEARER_PATTERN.sub(f"Bearer {REDACTION}", text)
    return AUTH0_TOKEN_PATTERN.sub(REDACTION, text)


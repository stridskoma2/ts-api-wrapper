from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0
    jitter_ratio: float = 0.2

    def delay_for_attempt(self, attempt_index: int, retry_after: str | None = None) -> float:
        parsed_retry_after = _parse_retry_after(retry_after)
        if parsed_retry_after is not None:
            return float(parsed_retry_after)
        delay = min(
            self.base_delay_seconds * (2 ** max(0, attempt_index - 1)),
            self.max_delay_seconds,
        )
        if self.jitter_ratio <= 0:
            return float(delay)
        spread = delay * self.jitter_ratio
        return float(max(0.0, delay + random.uniform(-spread, spread)))


async def sleep_with_policy(
    policy: RetryPolicy,
    attempt_index: int,
    retry_after: str | None = None,
    sleeper: Sleeper = asyncio.sleep,
) -> None:
    await sleeper(policy.delay_for_attempt(attempt_index, retry_after))


def _parse_retry_after(value: str | None) -> float | None:
    return parse_retry_after_seconds(value)


def parse_retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())

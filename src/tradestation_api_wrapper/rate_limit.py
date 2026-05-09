from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


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
            return min(parsed_retry_after, self.max_delay_seconds)
        delay = min(
            self.base_delay_seconds * (2 ** max(0, attempt_index - 1)),
            self.max_delay_seconds,
        )
        if self.jitter_ratio <= 0:
            return delay
        spread = delay * self.jitter_ratio
        return max(0.0, delay + random.uniform(-spread, spread))


async def sleep_with_policy(
    policy: RetryPolicy,
    attempt_index: int,
    retry_after: str | None = None,
    sleeper: Sleeper = asyncio.sleep,
) -> None:
    await sleeper(policy.delay_for_attempt(attempt_index, retry_after))


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


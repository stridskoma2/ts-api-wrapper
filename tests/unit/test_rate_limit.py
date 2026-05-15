from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from tradestation_api_wrapper.rate_limit import RetryPolicy


class RateLimitTests(unittest.TestCase):
    def test_retry_after_seconds_is_not_capped_by_backoff_max(self) -> None:
        policy = RetryPolicy(max_delay_seconds=5, jitter_ratio=0)

        self.assertEqual(policy.delay_for_attempt(1, "30"), 30)

    def test_retry_after_http_date_is_supported(self) -> None:
        policy = RetryPolicy(jitter_ratio=0)
        retry_after = format_datetime(datetime.now(UTC) + timedelta(seconds=2))

        delay = policy.delay_for_attempt(1, retry_after)

        self.assertGreater(delay, 0)
        self.assertLessEqual(delay, 2)


if __name__ == "__main__":
    unittest.main()

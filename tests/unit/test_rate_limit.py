from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from tradestation_api_wrapper.rate_limit import RetryPolicy, parse_retry_after_seconds


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

    def test_non_finite_retry_after_is_rejected(self) -> None:
        for malformed_value in ("inf", "-inf", "nan"):
            with self.subTest(retry_after=malformed_value):
                self.assertIsNone(parse_retry_after_seconds(malformed_value))

    def test_non_finite_retry_after_falls_back_to_bounded_backoff(self) -> None:
        policy = RetryPolicy(max_delay_seconds=5, jitter_ratio=0)

        self.assertLessEqual(policy.delay_for_attempt(1, "inf"), 5)

    def test_huge_retry_after_is_clamped_to_ceiling(self) -> None:
        policy = RetryPolicy(jitter_ratio=0, retry_after_ceiling_seconds=300)

        self.assertEqual(policy.delay_for_attempt(1, "100000"), 300)

    def test_far_future_retry_after_date_is_clamped_to_ceiling(self) -> None:
        policy = RetryPolicy(jitter_ratio=0, retry_after_ceiling_seconds=300)
        retry_after = format_datetime(datetime.now(UTC) + timedelta(days=365))

        self.assertEqual(policy.delay_for_attempt(1, retry_after), 300)


if __name__ == "__main__":
    unittest.main()

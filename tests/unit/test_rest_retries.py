from __future__ import annotations

import unittest

from tests.helpers import FakeTokenProvider, FakeTransport, json_response, sim_config
from tradestation_api_wrapper.errors import (
    AmbiguousOrderState,
    RateLimitError,
    TradeStationAPIError,
)
from tradestation_api_wrapper.rate_limit import RetryPolicy
from tradestation_api_wrapper.rest import TradeStationRestClient
from tradestation_api_wrapper.transport import HTTPResponse, NetworkTimeout


class RestRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sleeps: list[float] = []

    async def fake_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def test_safe_get_retries_transient_response(self) -> None:
        transport = FakeTransport(
            [
                json_response(503, {"Error": "ServiceUnavailable", "Message": "retry"}),
                json_response(200, {"Accounts": []}),
            ]
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
            retry_policy=RetryPolicy(jitter_ratio=0),
            sleeper=self.fake_sleep,
        )

        response = await client.get("/brokerage/accounts")

        self.assertEqual(response, {"Accounts": []})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(self.sleeps, [0.25])

    async def test_401_refreshes_once(self) -> None:
        token_provider = FakeTokenProvider()
        transport = FakeTransport(
            [
                json_response(401, {"Error": "Unauthorized", "Message": "expired"}),
                json_response(200, {"Accounts": []}),
            ]
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=token_provider,
            transport=transport,
        )

        await client.get("/brokerage/accounts")

        self.assertEqual(token_provider.refresh_count, 1)
        self.assertIn("refreshed-token", transport.requests[1].headers["Authorization"])

    async def test_submit_timeout_is_ambiguous_and_not_retried(self) -> None:
        transport = FakeTransport([NetworkTimeout("timeout")])
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
        )

        with self.assertRaises(AmbiguousOrderState):
            await client.post_order_write(
                "/orderexecution/orders",
                {"AccountID": "123456789"},
                local_request_id="request-1",
            )

        self.assertEqual(len(transport.requests), 1)

    async def test_submit_429_is_not_auto_retried(self) -> None:
        transport = FakeTransport(
            [
                json_response(
                    429,
                    {"Error": "TooManyRequests", "Message": "quota"},
                    {"Retry-After": "2"},
                )
            ]
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
            sleeper=self.fake_sleep,
        )

        with self.assertRaises(RateLimitError):
            await client.post_order_write(
                "/orderexecution/orders",
                {"AccountID": "123456789"},
                local_request_id="request-1",
            )

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.sleeps, [])

    async def test_non_idempotent_write_transient_response_is_ambiguous(self) -> None:
        for status_code in (408, 500, 502, 503, 504):
            transport = FakeTransport(
                [json_response(status_code, {"Error": "Transient", "Message": "unknown"})]
            )
            client = TradeStationRestClient(
                config=sim_config(),
                token_provider=FakeTokenProvider(),
                transport=transport,
            )

            with self.subTest(status_code=status_code), self.assertRaises(AmbiguousOrderState):
                await client.post_order_write(
                    "/orderexecution/orders",
                    {"AccountID": "123456789"},
                    local_request_id="request-1",
                )

            self.assertEqual(len(transport.requests), 1)

    async def test_success_response_must_be_valid_json_object(self) -> None:
        invalid_transport = FakeTransport(
            [HTTPResponse(status_code=200, headers={}, body=b"{not-json")]
        )
        invalid_client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=invalid_transport,
        )

        with self.assertRaises(TradeStationAPIError):
            await invalid_client.get("/brokerage/accounts")

        list_transport = FakeTransport([json_response(200, [])])
        list_client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=list_transport,
        )

        with self.assertRaises(TradeStationAPIError):
            await list_client.get("/brokerage/accounts")


if __name__ == "__main__":
    unittest.main()

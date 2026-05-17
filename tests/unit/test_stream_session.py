from __future__ import annotations

import unittest
from collections.abc import AsyncIterator, Awaitable, Callable

from tests.helpers import FakeTokenProvider, FakeTransport, sim_config
from tradestation_api_wrapper.errors import AuthenticationError, StreamError, TradeStationAPIError
from tradestation_api_wrapper.rate_limit import RetryPolicy
from tradestation_api_wrapper.rest import BROKERAGE_STREAM_ACCEPT, TradeStationRestClient
from tradestation_api_wrapper.stream import StreamEventKind, TradeStationStream
from tradestation_api_wrapper.transport import HTTPStreamOpenError


def recording_sleeper(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleeper(delay_seconds: float) -> None:
        delays.append(delay_seconds)

    return sleeper


class StreamSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnecting_stream_restarts_after_goaway(self) -> None:
        calls = 0

        async def chunk_source() -> AsyncIterator[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                yield '{"StreamStatus":"GoAway"}'
            else:
                yield '{"OrderID":"1"}'

        stream = TradeStationStream(chunk_source)
        events = []
        async for event in stream.events():
            events.append(event)
            if len(events) == 2:
                break

        self.assertEqual(
            [event.kind for event in events],
            [StreamEventKind.GO_AWAY, StreamEventKind.DATA],
        )

    async def test_error_event_terminates_stream(self) -> None:
        async def chunk_source() -> AsyncIterator[str]:
            yield '{"Error":"BadRequest","Message":"bad stream"}'

        stream = TradeStationStream(chunk_source)

        with self.assertRaises(StreamError):
            async for _event in stream.events():
                pass

    async def test_authentication_errors_are_not_reconnected(self) -> None:
        calls = 0

        async def chunk_source() -> AsyncIterator[str]:
            nonlocal calls
            calls += 1
            raise AuthenticationError(401, "Unauthorized", "expired", None)
            yield ""

        stream = TradeStationStream(chunk_source)

        with self.assertRaises(AuthenticationError):
            async for _event in stream.events():
                pass

        self.assertEqual(calls, 1)

    async def test_api_errors_are_not_reconnected(self) -> None:
        calls = 0

        async def chunk_source() -> AsyncIterator[str]:
            nonlocal calls
            calls += 1
            raise TradeStationAPIError(403, "Forbidden", "not allowed", None)
            yield ""

        stream = TradeStationStream(chunk_source)

        with self.assertRaises(TradeStationAPIError):
            async for _event in stream.events():
                pass

        self.assertEqual(calls, 1)

    async def test_rest_client_stream_events_uses_stream_transport(self) -> None:
        transport = FakeTransport([], streams=[[b'{"OrderID":"1"}']])
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
        )

        events = []
        async for event in client.stream_events("/brokerage/stream/accounts/123456789/orders"):
            events.append(event)

        self.assertEqual(events[0].payload["OrderID"], "1")
        self.assertEqual(transport.requests[0].headers["Accept"], BROKERAGE_STREAM_ACCEPT)

    async def test_stream_open_401_refreshes_token_once(self) -> None:
        token_provider = FakeTokenProvider()
        transport = FakeTransport(
            [],
            streams=[
                HTTPStreamOpenError(401, {}, b'{"Error":"Unauthorized","Message":"expired"}'),
                [b'{"OrderID":"1"}'],
            ],
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=token_provider,
            transport=transport,
        )

        events = []
        async for event in client.stream_events("/brokerage/stream/accounts/123456789/orders"):
            events.append(event)

        self.assertEqual(events[0].payload["OrderID"], "1")
        self.assertEqual(token_provider.refresh_count, 1)
        self.assertIn("refreshed-token", transport.requests[1].headers["Authorization"])

    async def test_stream_open_400_and_403_are_not_reconnected(self) -> None:
        for status_code in (400, 403):
            with self.subTest(status_code=status_code):
                transport = FakeTransport(
                    [],
                    streams=[
                        HTTPStreamOpenError(
                            status_code,
                            {},
                            b'{"Error":"Forbidden","Message":"not allowed"}',
                        ),
                    ],
                )
                client = TradeStationRestClient(
                    config=sim_config(),
                    token_provider=FakeTokenProvider(),
                    transport=transport,
                )

                with self.assertRaises(TradeStationAPIError):
                    async for _event in client.stream_events(
                        "/brokerage/stream/accounts/123456789/orders"
                    ):
                        pass

                self.assertEqual(len(transport.requests), 1)

    async def test_stream_open_429_reconnects_after_retry_after_delay(self) -> None:
        delays: list[float] = []
        transport = FakeTransport(
            [],
            streams=[
                HTTPStreamOpenError(
                    429,
                    {"Retry-After": "2"},
                    b'{"Error":"TooManyRequests","Message":"slow down"}',
                ),
                [b'{"OrderID":"1"}'],
            ],
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
            retry_policy=RetryPolicy(max_attempts=2),
            sleeper=recording_sleeper(delays),
        )

        events = []
        async for event in client.stream_events("/brokerage/stream/accounts/123456789/orders"):
            events.append(event)

        self.assertEqual(events[0].payload["OrderID"], "1")
        self.assertEqual(delays, [2.0])
        self.assertEqual(len(transport.requests), 2)

    async def test_stream_open_503_reconnects_with_backoff(self) -> None:
        delays: list[float] = []
        transport = FakeTransport(
            [],
            streams=[
                HTTPStreamOpenError(503, {}, b'{"Error":"Unavailable","Message":"retry"}'),
                [b'{"OrderID":"1"}'],
            ],
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.5),
            sleeper=recording_sleeper(delays),
        )

        events = []
        async for event in client.stream_events("/brokerage/stream/accounts/123456789/orders"):
            events.append(event)

        self.assertEqual(events[0].payload["OrderID"], "1")
        self.assertEqual(delays, [0.5])
        self.assertEqual(len(transport.requests), 2)

    async def test_stream_open_503_reconnect_limit_is_enforced(self) -> None:
        delays: list[float] = []
        transport = FakeTransport(
            [],
            streams=[
                HTTPStreamOpenError(503, {}, b'{"Error":"Unavailable","Message":"retry"}'),
                HTTPStreamOpenError(503, {}, b'{"Error":"Unavailable","Message":"still down"}'),
            ],
        )
        client = TradeStationRestClient(
            config=sim_config(),
            token_provider=FakeTokenProvider(),
            transport=transport,
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.5),
            sleeper=recording_sleeper(delays),
        )

        with self.assertRaises(TradeStationAPIError):
            async for _event in client.stream_events("/brokerage/stream/accounts/123456789/orders"):
                pass

        self.assertEqual(delays, [0.5])
        self.assertEqual(len(transport.requests), 2)


if __name__ == "__main__":
    unittest.main()

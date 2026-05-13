from __future__ import annotations

import unittest
from collections.abc import AsyncIterator

from tests.helpers import FakeTokenProvider, FakeTransport
from tradestation_api_wrapper.rest import BROKERAGE_STREAM_ACCEPT, TradeStationRestClient
from tradestation_api_wrapper.stream import StreamEventKind, TradeStationStream
from tests.helpers import sim_config


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

        self.assertEqual([event.kind for event in events], [StreamEventKind.GO_AWAY, StreamEventKind.DATA])

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


if __name__ == "__main__":
    unittest.main()

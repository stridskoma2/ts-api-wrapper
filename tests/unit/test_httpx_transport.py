from __future__ import annotations

import unittest
from types import SimpleNamespace

import tradestation_api_wrapper.transport as transport_module
from tradestation_api_wrapper.errors import TransportError
from tradestation_api_wrapper.transport import HTTPRequest, HttpxAsyncTransport, UrllibAsyncTransport


class FakeHttpxResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"ok":true}'


class FakeHttpxClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: object) -> FakeHttpxResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeHttpxResponse()

    async def aclose(self) -> None:
        self.closed = True


class FakeBlockingStream:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class StubUrllibTransport(UrllibAsyncTransport):
    def __init__(self, stream: FakeBlockingStream) -> None:
        self.opened_stream = stream

    def _open_stream_sync(self, request: HTTPRequest) -> FakeBlockingStream:
        return self.opened_stream


class FailingUrllibTransport(UrllibAsyncTransport):
    def _open_stream_sync(self, request: HTTPRequest) -> FakeBlockingStream:
        raise RuntimeError("reader exploded")


class HttpxTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_urllib_stream_yields_chunks_from_reader_thread(self) -> None:
        stream = FakeBlockingStream((b'{"one":1}', b'{"two":2}'))
        transport = StubUrllibTransport(stream)

        chunks = []
        async for chunk in transport.stream(HTTPRequest(method="GET", url="https://example.test")):
            chunks.append(chunk)

        self.assertEqual(chunks, [b'{"one":1}', b'{"two":2}'])
        self.assertTrue(stream.closed)

    async def test_urllib_stream_propagates_unexpected_reader_errors(self) -> None:
        transport = FailingUrllibTransport()

        with self.assertRaises(TransportError):
            async for _chunk in transport.stream(
                HTTPRequest(method="GET", url="https://example.test")
            ):
                pass

    def test_urllib_stream_queue_is_bounded(self) -> None:
        self.assertGreater(transport_module.STREAM_QUEUE_MAX_CHUNKS, 0)

    async def test_send_maps_http_request_to_httpx_client(self) -> None:
        original_loader = transport_module._load_httpx
        transport_module._load_httpx = lambda: SimpleNamespace(
            AsyncClient=FakeHttpxClient,
            TimeoutException=TimeoutError,
            HTTPError=OSError,
        )
        try:
            client = FakeHttpxClient()
            transport = HttpxAsyncTransport(client)

            response = await transport.send(
                HTTPRequest(
                    method="post",
                    url="https://example.test/path",
                    headers={"accept": "application/json"},
                    json_body={"hello": "world"},
                )
            )
        finally:
            transport_module._load_httpx = original_loader

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.calls[0]["method"], "POST")
        self.assertEqual(client.calls[0]["json"], {"hello": "world"})

    async def test_context_manager_closes_owned_httpx_client(self) -> None:
        original_loader = transport_module._load_httpx
        created_client = FakeHttpxClient()
        transport_module._load_httpx = lambda: SimpleNamespace(
            AsyncClient=lambda: created_client,
            TimeoutException=TimeoutError,
            HTTPError=OSError,
        )
        try:
            async with HttpxAsyncTransport():
                pass
        finally:
            transport_module._load_httpx = original_loader

        self.assertTrue(created_client.closed)


if __name__ == "__main__":
    unittest.main()

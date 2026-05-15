from __future__ import annotations

import unittest
from types import SimpleNamespace

import tradestation_api_wrapper.transport as transport_module
from tradestation_api_wrapper.transport import HTTPRequest, HttpxAsyncTransport


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


class HttpxTransportTests(unittest.IsolatedAsyncioTestCase):
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

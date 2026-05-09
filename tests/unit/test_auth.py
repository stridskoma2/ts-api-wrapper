from __future__ import annotations

import tempfile
import unittest
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.helpers import FakeTransport, json_response
from tradestation_api_wrapper.auth import (
    FileTokenStore,
    MemoryTokenStore,
    OAuthManager,
    OAuthToken,
    PlainTextTokenCodec,
    create_pkce_pair,
)


def expired_token(refresh_token: str = "refresh") -> OAuthToken:
    return OAuthToken(
        access_token="old",
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def test_pkce_pair_is_urlsafe(self) -> None:
        pkce = create_pkce_pair()

        self.assertGreaterEqual(len(pkce.verifier), 43)
        self.assertNotIn("=", pkce.challenge)

    def test_file_token_store_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileTokenStore(
                Path(temp_dir) / "token.json",
                PlainTextTokenCodec(allow_plaintext_for_tests=True),
            )
            token = expired_token("old-refresh")
            replacement = expired_token("new-refresh")
            store.save(token)

            self.assertFalse(store.compare_and_swap_refresh_token("wrong", replacement))
            self.assertTrue(store.compare_and_swap_refresh_token("old-refresh", replacement))
            self.assertEqual(store.load().refresh_token, "new-refresh")  # type: ignore[union-attr]

    async def test_refresh_updates_rotating_refresh_token_atomically(self) -> None:
        store = MemoryTokenStore(expired_token("old-refresh"))
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 1200,
                    },
                )
            ]
        )
        manager = OAuthManager(
            client_id="client",
            client_secret="secret",
            redirect_uri="http://localhost:31022/callback",
            scopes=("openid", "offline_access"),
            token_store=store,
            transport=transport,
        )

        token = await manager.refresh_access_token()

        self.assertEqual(token.access_token, "new-access")
        self.assertEqual(store.load().refresh_token, "new-refresh")  # type: ignore[union-attr]

    async def test_concurrent_refresh_uses_one_transport_call(self) -> None:
        store = MemoryTokenStore(expired_token("refresh"))
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "refresh",
                        "expires_in": 1200,
                    },
                )
            ]
        )
        manager = OAuthManager(
            client_id="client",
            client_secret=None,
            redirect_uri="http://localhost:31022/callback",
            scopes=("openid", "offline_access"),
            token_store=store,
            transport=transport,
        )

        first, second = await asyncio.gather(
            manager.get_access_token(),
            manager.get_access_token(),
        )

        self.assertEqual(first, "new-access")
        self.assertEqual(second, "new-access")
        self.assertEqual(len(transport.requests), 1)


if __name__ == "__main__":
    unittest.main()

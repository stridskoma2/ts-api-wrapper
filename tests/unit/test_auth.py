from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import tradestation_api_wrapper.auth as auth_module
from tests.helpers import FakeTransport, json_response
from tradestation_api_wrapper.auth import (
    FileTokenStore,
    MemoryTokenStore,
    OAuthManager,
    OAuthToken,
    PlainTextTokenCodec,
    create_pkce_pair,
)
from tradestation_api_wrapper.errors import ConfigurationError


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

    def test_file_token_store_compare_and_swap_detects_concurrent_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileTokenStore(
                Path(temp_dir) / "token.json",
                PlainTextTokenCodec(allow_plaintext_for_tests=True),
            )
            store.save(expired_token("original-refresh"))
            store.save(expired_token("other-writer-refresh"))

            changed = store.compare_and_swap_refresh_token(
                "original-refresh",
                expired_token("new-refresh"),
            )

            self.assertFalse(changed)
            self.assertEqual(
                store.load().refresh_token,  # type: ignore[union-attr]
                "other-writer-refresh",
            )

    def test_file_token_store_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileTokenStore(
                Path(temp_dir) / "token.json",
                PlainTextTokenCodec(allow_plaintext_for_tests=True),
            )

            store.save(expired_token("round-trip-refresh"))

            loaded = store.load()
            assert loaded is not None
            self.assertEqual(loaded.refresh_token, "round-trip-refresh")

    def test_file_token_store_removes_invalid_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token.json"
            path.with_suffix(path.suffix + ".lock").write_text("held", encoding="ascii")
            store = FileTokenStore(
                path,
                PlainTextTokenCodec(allow_plaintext_for_tests=True),
                lock_timeout_seconds=0.01,
            )

            store.save(expired_token())

            self.assertFalse(path.with_suffix(path.suffix + ".lock").exists())
            self.assertEqual(store.load().refresh_token, "refresh")  # type: ignore[union-attr]

    def test_file_token_store_removes_numeric_stale_lock(self) -> None:
        with patch.object(auth_module, "_process_is_running", return_value=False):
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "token.json"
                path.with_suffix(path.suffix + ".lock").write_text("999999", encoding="ascii")
                store = FileTokenStore(
                    path,
                    PlainTextTokenCodec(allow_plaintext_for_tests=True),
                    lock_timeout_seconds=0.01,
                )

                store.save(expired_token())

                self.assertFalse(path.with_suffix(path.suffix + ".lock").exists())
                self.assertEqual(store.load().refresh_token, "refresh")  # type: ignore[union-attr]

    def test_file_token_store_reports_live_held_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token.json"
            path.with_suffix(path.suffix + ".lock").write_text(str(os.getpid()), encoding="ascii")
            store = FileTokenStore(
                path,
                PlainTextTokenCodec(allow_plaintext_for_tests=True),
                lock_timeout_seconds=0.01,
            )

            with self.assertRaises(ConfigurationError):
                store.save(expired_token())

    def test_token_file_lock_cleans_up_after_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "token.json.lock"

            with self.assertRaises(RuntimeError):
                with auth_module._TokenFileLock(lock_path, 0.01):
                    raise RuntimeError("boom")

            self.assertFalse(lock_path.exists())

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

    async def test_authorization_code_exchange_saves_token_with_pkce(self) -> None:
        store = MemoryTokenStore()
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

        token = await manager.exchange_authorization_code("auth-code", pkce_verifier="verifier")

        self.assertEqual(token.access_token, "new-access")
        self.assertEqual(store.load().refresh_token, "new-refresh")  # type: ignore[union-attr]
        form_body = transport.requests[0].form_body
        assert form_body is not None
        self.assertEqual(form_body["grant_type"], "authorization_code")
        self.assertEqual(form_body["code_verifier"], "verifier")


if __name__ == "__main__":
    unittest.main()

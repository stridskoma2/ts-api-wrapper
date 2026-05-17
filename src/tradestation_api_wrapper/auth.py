from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import json
import os
import secrets
import tempfile
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel, ConfigDict, Field

from tradestation_api_wrapper.errors import AuthenticationError, ConfigurationError
from tradestation_api_wrapper.transport import AsyncTransport, HTTPRequest

AUTHORIZATION_URL = "https://signin.tradestation.com/authorize"
TOKEN_URL = "https://signin.tradestation.com/oauth/token"
REFRESH_MARGIN = timedelta(minutes=2)


class OAuthToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime
    scope: str | None = None

    @classmethod
    def from_token_response(
        cls,
        payload: dict[str, object],
        existing_refresh_token: str | None = None,
        now: datetime | None = None,
    ) -> "OAuthToken":
        issued_at = now or datetime.now(UTC)
        expires_in = int(payload.get("expires_in", 1200))
        refresh_token = payload.get("refresh_token") or existing_refresh_token
        if not isinstance(payload.get("access_token"), str):
            raise AuthenticationError(0, "InvalidTokenResponse", "missing access_token", payload)
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(refresh_token) if refresh_token else None,
            token_type=str(payload.get("token_type", "Bearer")),
            expires_at=issued_at + timedelta(seconds=expires_in),
            scope=str(payload["scope"]) if "scope" in payload else None,
        )

    def expires_soon(self, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(UTC)
        return self.expires_at <= current_time + REFRESH_MARGIN


class TokenStore(Protocol):
    def load(self) -> OAuthToken | None:
        ...

    def save(self, token: OAuthToken) -> None:
        ...

    def compare_and_swap_refresh_token(
        self,
        expected_refresh_token: str | None,
        replacement: OAuthToken,
    ) -> bool:
        ...


class MemoryTokenStore:
    def __init__(self, token: OAuthToken | None = None) -> None:
        self._token = token

    def load(self) -> OAuthToken | None:
        return self._token

    def save(self, token: OAuthToken) -> None:
        self._token = token

    def compare_and_swap_refresh_token(
        self,
        expected_refresh_token: str | None,
        replacement: OAuthToken,
    ) -> bool:
        current = self.load()
        current_refresh_token = current.refresh_token if current else None
        if current_refresh_token != expected_refresh_token:
            return False
        self.save(replacement)
        return True


class TokenCodec(Protocol):
    def encode(self, token: OAuthToken) -> bytes:
        ...

    def decode(self, payload: bytes) -> OAuthToken:
        ...


class PlainTextTokenCodec:
    def __init__(self, *, allow_plaintext_for_tests: bool = False) -> None:
        if not allow_plaintext_for_tests:
            raise ConfigurationError("plaintext token storage is only allowed in tests")

    def encode(self, token: OAuthToken) -> bytes:
        return token.model_dump_json().encode("utf-8")

    def decode(self, payload: bytes) -> OAuthToken:
        return OAuthToken.model_validate_json(payload)


class FileTokenStore:
    def __init__(
        self,
        path: Path,
        codec: TokenCodec,
        *,
        lock_timeout_seconds: float = 10.0,
    ) -> None:
        self._path = path
        self._codec = codec
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._lock_timeout_seconds = lock_timeout_seconds

    def load(self) -> OAuthToken | None:
        return self._load_unlocked()

    def save(self, token: OAuthToken) -> None:
        with _TokenFileLock(self._lock_path, self._lock_timeout_seconds):
            self._save_unlocked(token)

    def compare_and_swap_refresh_token(
        self,
        expected_refresh_token: str | None,
        replacement: OAuthToken,
    ) -> bool:
        with _TokenFileLock(self._lock_path, self._lock_timeout_seconds):
            current = self._load_unlocked()
            current_refresh_token = current.refresh_token if current else None
            if current_refresh_token != expected_refresh_token:
                return False
            self._save_unlocked(replacement)
            return True

    def _load_unlocked(self) -> OAuthToken | None:
        if not self._path.exists():
            return None
        return self._codec.decode(self._path.read_bytes())

    def _save_unlocked(self, token: OAuthToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = self._codec.encode(token)
        with tempfile.NamedTemporaryFile(delete=False, dir=self._path.parent) as temp_file:
            temp_file.write(encoded)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, self._path)

class _TokenFileLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._file_descriptor: int | None = None

    def __enter__(self) -> "_TokenFileLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            try:
                self._file_descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._file_descriptor, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError as exc:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise ConfigurationError("timed out waiting for token-store lock") from exc
                time.sleep(0.05)

    def __exit__(self, *exc_info: object) -> None:
        if self._file_descriptor is not None:
            os.close(self._file_descriptor)
            self._file_descriptor = None
        try:
            self._path.unlink()
        except FileNotFoundError:
            return

    def _remove_stale_lock(self) -> bool:
        pid = _read_lock_pid(self._path)
        if pid is not None and _process_is_running(pid):
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            return True
        return True


def _read_lock_pid(path: Path) -> int | None:
    try:
        pid_text = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    try:
        return int(pid_text)
    except ValueError:
        return None


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_running(pid: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = getattr(ctypes, "windll").kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


@dataclass(frozen=True, slots=True)
class PKCEPair:
    verifier: str
    challenge: str


@dataclass(slots=True)
class _LoopbackCallback:
    expected_state: str
    authorization_code: str | None = None
    error: str | None = None


def create_pkce_pair() -> PKCEPair:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return PKCEPair(verifier=verifier, challenge=challenge)


class OAuthManager:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        scopes: tuple[str, ...],
        token_store: TokenStore,
        transport: AsyncTransport,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._token_store = token_store
        self._transport = transport
        self._refresh_lock = asyncio.Lock()

    def authorization_url(self, *, state: str, pkce: PKCEPair | None = None) -> str:
        query = {
            "audience": "https://api.tradestation.com",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "state": state,
        }
        if pkce is not None:
            query["code_challenge"] = pkce.challenge
            query["code_challenge_method"] = "S256"
        return f"{AUTHORIZATION_URL}?{urlencode(query)}"

    async def exchange_authorization_code(
        self,
        authorization_code: str,
        *,
        pkce_verifier: str | None = None,
    ) -> OAuthToken:
        form_body = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": authorization_code,
            "redirect_uri": self._redirect_uri,
            **({"client_secret": self._client_secret} if self._client_secret else {}),
            **({"code_verifier": pkce_verifier} if pkce_verifier else {}),
        }
        response = await self._transport.send(
            HTTPRequest(method="POST", url=TOKEN_URL, form_body=form_body)
        )
        payload = response.json()
        if response.status_code >= 400 or not isinstance(payload, dict):
            error = str(payload.get("error", "AuthError")) if isinstance(payload, dict) else "AuthError"
            raise AuthenticationError(
                response.status_code,
                error,
                _token_error_message(payload, "authorization-code exchange failed"),
                payload if isinstance(payload, dict) else None,
            )
        token = OAuthToken.from_token_response(payload)
        self._token_store.save(token)
        return token

    async def get_access_token(self) -> str:
        token = self._token_store.load()
        if token is None:
            raise AuthenticationError(0, "MissingToken", "no OAuth token is available", None)
        if token.expires_soon():
            token = await self.refresh_access_token()
        return token.access_token

    async def force_refresh_access_token(self) -> str:
        return (await self.refresh_access_token(force=True)).access_token

    async def refresh_access_token(self, *, force: bool = False) -> OAuthToken:
        async with self._refresh_lock:
            current = self._token_store.load()
            if current is None or current.refresh_token is None:
                raise AuthenticationError(
                    0,
                    "MissingRefreshToken",
                    "no refresh token is available",
                    None,
                )
            if not force and not current.expires_soon():
                return current
            response = await self._transport.send(
                HTTPRequest(
                    method="POST",
                    url=TOKEN_URL,
                    form_body={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "refresh_token": current.refresh_token,
                        **({"client_secret": self._client_secret} if self._client_secret else {}),
                    },
                )
            )
            payload = response.json()
            if response.status_code >= 400:
                raise AuthenticationError(
                    response.status_code,
                    str(payload.get("error", "AuthError")),
                    _token_error_message(payload, "token refresh failed"),
                    payload if isinstance(payload, dict) else None,
                )
            replacement = OAuthToken.from_token_response(
                payload,
                existing_refresh_token=current.refresh_token,
            )
            if not self._token_store.compare_and_swap_refresh_token(
                current.refresh_token,
                replacement,
            ):
                latest = self._token_store.load()
                if latest is None:
                    raise AuthenticationError(
                        0,
                        "TokenStoreRace",
                        "token store lost current token",
                        None,
                    )
                return latest
            return replacement


async def authorize_with_loopback(
    manager: OAuthManager,
    *,
    state: str | None = None,
    pkce: PKCEPair | None = None,
    host: str = "127.0.0.1",
    port: int = 31022,
    callback_path: str = "/callback",
    open_browser: bool = True,
    browser_opener: Callable[[str], bool] | None = None,
    timeout_seconds: float = 300.0,
) -> OAuthToken:
    state_value = state or secrets.token_urlsafe(24)
    pkce_value = pkce or create_pkce_pair()
    callback = _LoopbackCallback(expected_state=state_value)
    server = HTTPServer(
        (host, port),
        _loopback_handler(callback, callback_path),
    )
    try:
        authorization_url = manager.authorization_url(state=state_value, pkce=pkce_value)
        if open_browser:
            (browser_opener or webbrowser.open)(authorization_url)
        deadline = time.monotonic() + timeout_seconds
        while (
            callback.authorization_code is None
            and callback.error is None
            and time.monotonic() < deadline
        ):
            server.timeout = max(0.0, min(1.0, deadline - time.monotonic()))
            await asyncio.to_thread(server.handle_request)
    finally:
        server.server_close()

    if callback.error is not None:
        raise AuthenticationError(0, "OAuthCallbackError", callback.error, None)
    if callback.authorization_code is None:
        raise AuthenticationError(
            0,
            "OAuthCallbackTimeout",
            "OAuth callback was not received",
            None,
        )
    return await manager.exchange_authorization_code(
        callback.authorization_code,
        pkce_verifier=pkce_value.verifier,
    )


def _loopback_handler(
    callback: _LoopbackCallback,
    callback_path: str,
) -> type[BaseHTTPRequestHandler]:
    class LoopbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self._write_response(404, b"Not found")
                return

            query = parse_qs(parsed.query)
            returned_state = _first_query_value(query, "state")
            if returned_state != callback.expected_state:
                callback.error = "OAuth state did not match"
                self._write_response(400, b"OAuth state did not match")
                return

            provider_error = _first_query_value(query, "error")
            if provider_error:
                callback.error = provider_error
                self._write_response(400, b"OAuth provider returned an error")
                return

            authorization_code = _first_query_value(query, "code")
            if not authorization_code:
                callback.error = "OAuth callback did not include an authorization code"
                self._write_response(400, b"Missing authorization code")
                return

            callback.authorization_code = authorization_code
            self._write_response(200, b"Authorization complete. You can close this window.")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_response(self, status_code: int, body: bytes) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return LoopbackHandler


def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _token_error_message(payload: object, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    return str(payload.get("error_description", payload.get("message", fallback)))

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

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
    def __init__(self, path: Path, codec: TokenCodec) -> None:
        self._path = path
        self._codec = codec

    def load(self) -> OAuthToken | None:
        if not self._path.exists():
            return None
        return self._codec.decode(self._path.read_bytes())

    def save(self, token: OAuthToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = self._codec.encode(token)
        with tempfile.NamedTemporaryFile(delete=False, dir=self._path.parent) as temp_file:
            temp_file.write(encoded)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, self._path)

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


@dataclass(frozen=True, slots=True)
class PKCEPair:
    verifier: str
    challenge: str


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
                raise AuthenticationError(0, "MissingRefreshToken", "no refresh token is available", None)
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
                    str(payload.get("error_description", payload.get("message", "token refresh failed"))),
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
                    raise AuthenticationError(0, "TokenStoreRace", "token store lost current token", None)
                return latest
            return replacement

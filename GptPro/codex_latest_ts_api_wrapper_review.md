# Codex Task Brief: Latest Review of `stridskoma2/ts-api-wrapper`

Repository: `https://github.com/stridskoma2/ts-api-wrapper`  
Review date: 2026-05-15  
Reviewed target: current `main` branch via GitHub connector  
Inputs reconciled:
- Latest repository source files.
- Uploaded second-pass review / markup from this conversation.
- Prior review items that remain applicable.

Do **not** place live orders while working on this. Keep SIM order-placement integration tests opt-in only.

## Executive summary

The repository has improved since the earlier review. In particular:

- Market-data stream `Accept` headers now use `application/vnd.tradestation.streams.v2+json`, while brokerage order/position streams use `application/vnd.tradestation.streams.v3+json`.
- The spec pinning tool no longer strips v2 stream media types from v3 market-data endpoints.
- `market_order()` now requires `estimated_price`.
- Option risk/reward decimal payloads now preserve decimal strings instead of converting to floats.
- `requested_scopes` are sorted deterministically.

However, several high-risk items remain open. The biggest unresolved blockers are:

1. `replace_order()` and `cancel_order()` still do not take or validate `account_id`.
2. Write-side HTTP `408/500/502/503/504` still do not become `AmbiguousOrderState`.
3. Several configured risk controls remain unenforced.
4. `asset_class` still defaults to `EQUITY`, which can bypass option/future guardrails.
5. `TradeStationTrade.reconcile_required` still ignores `ack.errors`.
6. Stream handling still needs hardening around UTF-8 chunking, auth/config failures, stream-open 401, and `ERROR` events.
7. OAuth loopback still handles only one HTTP request.
8. `Retry-After` is still capped to five seconds.
9. Transport/client lifecycle cleanup is still incomplete.

The wrapper is directionally good, but it should not claim production-grade trading safety until the Priority 0 issues below are resolved.

---

## Verification commands

Use portable commands. Do not use hard-coded local runtime paths.

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Status of earlier review items

### Fixed / improved

#### A. Market-data stream media type regression fixed

Current `rest.py` defines:

```python
BROKERAGE_STREAM_ACCEPT = "application/vnd.tradestation.streams.v3+json"
MARKET_DATA_STREAM_ACCEPT = "application/vnd.tradestation.streams.v2+json"
```

Current `client.py` passes `MARKET_DATA_STREAM_ACCEPT` for quote, bar, market-depth, option-chain, and option-quote streams. Keep this behavior.

#### B. Spec pinning no longer deletes v2 stream media types

`tools/pin_tradestation_spec.py` now filters to `/v3/` paths and no longer removes non-v3 stream media types. The lock note explicitly says v3 market-data stream endpoints preserve TradeStation’s legacy-labeled `streams.v2` media type. Keep this behavior.

#### C. `market_order()` now requires `estimated_price`

The builder signature now requires:

```python
estimated_price: Decimal
```

This matches `validate_order_for_config()`, which rejects market orders without an estimated price for risk validation.

#### D. Option risk/reward payload no longer converts `Decimal` to `float`

`option_risk_reward_payload()` now uses `_stringify_decimals()`, so values such as `Decimal("0.24")` are serialized as `"0.24"` rather than as an imprecise float. Keep this.

#### E. `requested_scopes` ordering is deterministic

`TradeStationConfig.require_refresh_scopes()` now returns:

```python
tuple(sorted(scopes))
```

Keep this.

#### F. `classify_stream_message()` precedence is now explicit

The expression is now written with parentheses:

```python
if ("Error" in payload) or ("Message" in payload and not _looks_like_market_data(payload)):
```

This resolves the original readability/operator-precedence issue. There are still stream classification gaps listed below.

#### G. README commands are portable

The README now uses `python -m ...` commands rather than Codex-local Windows paths. Consider changing code fences from `powershell` to `bash` or plain `text`, but this is cosmetic.

---

## Priority 0 — safety/correctness blockers

### 1. Make `replace_order()` and `cancel_order()` account-safe

Current issue:

```python
async def replace_order(
    self,
    order_id: str,
    replacement: OrderReplaceRequest | OrderRequest,
) -> TradeStationTrade:
    ...

async def cancel_order(self, order_id: str) -> dict[str, Any]:
    ...
```

These methods still do **not** take `account_id`. They do not call:

```python
config.assert_can_replace_orders(account_id)
config.assert_can_cancel_orders(account_id)
```

even though those helpers now exist in `config.py`.

Why this matters:

- `account_allowlist` is not a complete safety boundary if order modification/cancellation can target arbitrary order IDs.
- The current tests still encode the unsafe shape: `client.replace_order("123", replacement)`.

Required change:

```python
async def replace_order(
    self,
    account_id: str,
    order_id: str,
    replacement: OrderReplaceRequest | OrderRequest,
) -> TradeStationTrade:
    self.config.assert_can_replace_orders(account_id)
    ...

async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
    self.config.assert_can_cancel_orders(account_id)
    ...
```

Recommended preflight for replace:

```python
orders = await self.get_orders_by_id((account_id,), (order_id,))
if not orders:
    raise RequestValidationError("order was not found for allowlisted account")
if any(order.account_id is not None and order.account_id != account_id for order in orders):
    raise RequestValidationError("order account does not match requested account")
```

Cancellation note:

- `assert_can_cancel_orders()` should not check the kill-switch file.
- Cancellations are risk-reducing and should remain possible even when new risk is blocked.

Tests to add/update:

- `replace_order(account_id, order_id, replacement)` rejects non-allowlisted accounts.
- `cancel_order(account_id, order_id)` rejects non-allowlisted accounts.
- `replace_order()` rejects when preflight lookup returns no matching order.
- `replace_order()` rejects when the found order belongs to a different account.
- `cancel_order()` works when kill-switch is active.
- Existing replace/cancel call sites are updated to include `account_id`.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/errors.py`
- `tests/unit/test_client_features.py`

---

### 2. Treat write-side `408/500/502/503/504` as `AmbiguousOrderState`

Current issue:

The REST layer treats `NetworkTimeout` and `TransportError` as ambiguous for non-idempotent writes, but transient HTTP responses on writes still fall through to `_api_error(response)`.

For broker writes, the following response statuses should be treated as ambiguous:

```python
AMBIGUOUS_WRITE_STATUSES = {408, 500, 502, 503, 504}
```

Required change inside `TradeStationRestClient.request_json()`:

```python
if not retry_safe and response.status_code in AMBIGUOUS_WRITE_STATUSES:
    raise AmbiguousOrderState(
        ambiguous_operation or method,
        local_request_id,
        _api_error(response),
    )
```

Do **not** automatically retry non-idempotent writes.

Keep `429` as a normal `RateLimitError` unless there is evidence that TradeStation can accept a write and still return `429`.

Tests to add:

- `post_order_write()` raises `AmbiguousOrderState` on `408`, `500`, `502`, `503`, `504`.
- `put_order_write()` raises `AmbiguousOrderState` on those statuses.
- `delete_order_write()` raises `AmbiguousOrderState` on those statuses.
- Write-side `429` still raises `RateLimitError` and is not retried.
- Safe reads still retry retryable statuses.

Likely files:

- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_rest_retries.py`

---

### 3. Make `TradeStationTrade.reconcile_required` true when `ack.errors` is non-empty

Current issue:

```python
@property
def reconcile_required(self) -> bool:
    return self.is_ambiguous or (self.ack is not None and self.order_id is None)
```

If a group order partially succeeds and the response includes both acknowledged legs and `Errors`, `reconcile_required` can be false. That can leave a caller with a clean-looking `TradeStationTrade` while some protective legs failed.

Required change:

```python
@property
def reconcile_required(self) -> bool:
    if self.is_ambiguous:
        return True
    if self.ack is None:
        return False
    if self.order_id is None:
        return True
    return bool(self.ack.errors)
```

Tests to add:

- `ack=OrderAck(OrderID="123", Errors=[...])` yields `reconcile_required is True`.
- Group ack with partial `Orders` and non-empty `Errors` yields `reconcile_required is True`.
- Clean ack with order ID and no errors yields `False`.

Likely files:

- `src/tradestation_api_wrapper/trade.py`
- `tests/unit/test_trade.py`

---

### 4. Enforce `allow_extended_hours`

Current issue:

`TradeStationConfig` declares:

```python
allow_extended_hours: bool = False
```

but validation does not enforce it. `Duration` includes extended-hours values:

```python
Duration.DAY_PLUS   # DYP
Duration.GTC_PLUS   # GCP
Duration.GTD_PLUS   # GDP
```

Required change:

```python
EXTENDED_HOURS_DURATIONS = {
    Duration.DAY_PLUS,
    Duration.GTC_PLUS,
    Duration.GTD_PLUS,
}

def _validate_time_in_force(order: OrderRequest, config: TradeStationConfig) -> None:
    if (
        order.time_in_force.duration in EXTENDED_HOURS_DURATIONS
        and not config.allow_extended_hours
    ):
        raise RequestValidationError("extended-hours orders are disabled by configuration")
```

Apply recursively to:

- group orders,
- OSO children, if OSO children remain supported,
- replacement requests if replacement advanced options can change time rules.

Tests to add:

- `DYP`, `GCP`, and `GDP` are rejected by default.
- They are accepted with `allow_extended_hours=True`.
- Group/OSO children are checked.

Likely files:

- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`

---

### 5. Remove, rename, or enforce unenforced stateful risk fields

Current fields:

```python
max_symbol_position_notional: Decimal = Decimal("5000")
max_daily_loss: Decimal = Decimal("500")
max_daily_order_count: int = 20
```

These remain defined in `TradeStationConfig`, but they are not read in validation.

Why this matters:

The fields look like active safety controls. They are not.

Preferred options:

#### Option A — implement stateful risk validation

Add something like:

```python
class RiskSnapshot(BaseModel):
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[OrderSnapshot, ...]
    orders_today: int
    realized_daily_loss: Decimal | None = None

def validate_order_for_state(
    order: OrderRequest,
    config: TradeStationConfig,
    state: RiskSnapshot,
) -> None:
    ...
```

#### Option B — remove or rename

If stateful validation is out of scope, remove them or rename them to make non-enforcement explicit:

```python
reserved_max_symbol_position_notional
reserved_max_daily_loss
reserved_max_daily_order_count
```

Also document caller responsibility.

Tests:

- If implemented: show each field is enforced.
- If removed/renamed: update docs and config tests.

Likely files:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/validation.py`
- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

---

### 6. Fix `asset_class` bypass

Current issue:

`OrderRequest.asset_class` still defaults to:

```python
AssetClass.EQUITY
```

Builders do not expose `asset_class`. Validation only blocks options/futures when `asset_class` is explicitly set to `OPTION` or `FUTURE`.

This lets a caller submit an option/future-looking symbol while the wrapper treats it as equity.

Preferred change:

```python
asset_class: AssetClass = Field(default=AssetClass.UNKNOWN, exclude=True)
```

Then reject unknown asset class at submission validation:

```python
if order.asset_class is AssetClass.UNKNOWN:
    raise RequestValidationError("order asset_class must be explicit for risk validation")
```

Expose `asset_class` in builders:

```python
def limit_order(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    action: TradeAction,
    limit_price: Decimal,
    asset_class: AssetClass = AssetClass.EQUITY,
    ...
) -> OrderRequest:
    ...
```

Alternative:

- Infer asset class through `get_symbols()` before submitting.
- This is more complex and slower but reduces caller burden.

Tests:

- Missing/unknown asset class is rejected before submission.
- Option order is rejected unless `allow_options=True`.
- Future order is rejected unless `allow_futures=True`.
- Builder-created equity orders still work.
- Builders can create option/future orders only when asset class is explicitly supplied.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/builders.py`
- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`

---

### 7. Validate `'Trade'` scope when trading flags are enabled, and use scope checks in client methods

Current state:

`config.py` now defines scope constants and `assert_scope_requested()`, but current client methods do not appear to call it. Trading flags can still be set without `"Trade"` in `requested_scopes`.

Required config construction check:

```python
trading_enabled = self.allow_market_orders or self.allow_options or self.allow_futures
if trading_enabled and TRADE_SCOPE not in self.requested_scopes:
    raise ValueError("allow_* trading flags require 'Trade' in requested_scopes")
```

Recommended client preflight checks:

- Account reads: `ReadAccount`.
- Market data reads: `MarketData`.
- Order confirm/place/replace/cancel: `Trade`.
- Option risk/reward: `OptionSpreads` if required by the official spec.
- Market depth: `Matrix`.

Caveat:

Checking `requested_scopes` is a configuration preflight. It does not prove the actual OAuth token contains those scopes.

Tests:

- Config with `allow_market_orders=True` and no `Trade` scope fails.
- Config with `allow_options=True` and no `Trade` scope fails.
- Config with `allow_futures=True` and no `Trade` scope fails.
- Client order methods call `assert_scope_requested(Trade)`.
- Market-depth helpers call `assert_scope_requested(Matrix)` if applicable.

Likely files:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/client.py`
- `tests/unit/test_client_features.py`
- `tests/unit/test_models_and_validation.py`

---

## Priority 1 — correctness / reliability

### 8. Validate `GTD` and `GTD_PLUS` require expiration

Current issue:

`TimeInForce(Duration=Duration.GTD)` builds successfully with `expiration=None`.

Required model validator:

```python
@model_validator(mode="after")
def require_expiration_for_gtd(self) -> "TimeInForce":
    if self.duration in (Duration.GTD, Duration.GTD_PLUS) and self.expiration is None:
        raise ValueError("GTD and GTD_PLUS durations require an Expiration datetime")
    return self
```

Also consider rejecting `expiration` for durations where TradeStation does not accept it.

Tests:

- `TimeInForce(Duration=Duration.GTD)` raises.
- `TimeInForce(Duration=Duration.GTD_PLUS)` raises.
- `TimeInForce(Duration=Duration.GTD, Expiration=aware_datetime)` passes.

Likely file:

- `src/tradestation_api_wrapper/models.py`

---

### 9. Fix `bracket_order_group()` duration semantics

Current issue:

`bracket_order_group()` takes `duration`, but it hardcodes the parent entry leg to `Duration.DAY` while using the caller-provided duration for target and stop legs.

Current shape:

```python
parent = limit_order(..., duration=Duration.DAY)
target = limit_order(..., duration=duration)
stop = stop_market_order(..., duration=duration)
```

This is surprising. Passing `duration=Duration.GTC` produces GTC exits but a DAY entry.

Preferred API:

```python
def bracket_order_group(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    entry_action: TradeAction,
    entry_limit_price: Decimal,
    target_price: Decimal,
    stop_price: Decimal,
    entry_duration: Duration = Duration.DAY,
    exit_duration: Duration = Duration.GTC,
    route: str | None = None,
) -> GroupOrderRequest:
    ...
```

Alternatively document that the existing `duration` parameter means exit duration only. The explicit two-duration API is clearer.

Tests:

- Passing `entry_duration=GTC` sets parent to GTC.
- Passing `exit_duration=DAY` sets target and stop to DAY.
- Defaults preserve previous behavior if desired.

Likely files:

- `src/tradestation_api_wrapper/builders.py`
- `tests/unit/test_models_and_validation.py`

---

### 10. Wrap malformed JSON on successful responses as `TradeStationAPIError`

Current issue:

On successful responses, `request_json()` calls:

```python
decoded = response.json()
```

If JSON decoding fails, raw `json.JSONDecodeError` / `ValueError` escapes outside the wrapper’s error hierarchy.

Required change:

```python
if _is_success(response):
    try:
        decoded = response.json()
    except ValueError as exc:
        raise TradeStationAPIError(
            response.status_code,
            "InvalidResponse",
            "expected valid JSON object response",
            {"response": response.text()},
        ) from exc
    ...
```

Tests:

- A 200 response with invalid JSON raises `TradeStationAPIError`.
- A 200 response with a JSON list raises `TradeStationAPIError`.
- A valid JSON object still returns normally.

Likely files:

- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_rest_retries.py`

---

### 11. Harden stream parser and stream reconnect behavior

Current open stream issues:

- `JsonStreamParser.feed()` still decodes each byte chunk with `errors="replace"`.
- Auth/config errors are caught by `except Exception` and retried instead of surfacing immediately.
- Stream `ERROR` events are yielded and streaming continues.
- Stream-open HTTP errors are generic `TransportError`, so stream-open 401 cannot trigger a one-time token refresh.
- `_looks_like_market_data()` still lacks market-depth keys such as `Side`, `Price`, `Size`, and `Entries`.

#### Incremental UTF-8 decoder

Implement:

```python
import codecs

class JsonStreamParser:
    def __init__(self) -> None:
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._decoder = json.JSONDecoder()

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        if isinstance(chunk, bytes):
            self._buffer += self._utf8_decoder.decode(chunk, final=False)
        else:
            self._buffer += chunk
        ...
```

Tests:

- Multibyte UTF-8 character split across chunks parses correctly.
- Malformed UTF-8 behavior is explicit and tested.

#### Auth/config errors should bypass reconnect

In `stream.py`:

```python
except (AuthenticationError, ConfigurationError):
    raise
except Exception:
    ...
```

Import those error classes.

Tests:

- Chunk source raising `AuthenticationError` is not retried.
- Chunk source raising `ConfigurationError` is not retried.
- Transient network/transport errors still reconnect.

#### Stream `ERROR` policy

Add a policy field:

```python
@dataclass(frozen=True, slots=True)
class StreamReconnectPolicy:
    max_reconnects: int = 3
    stop_on_error: bool = True
```

When an `ERROR` event appears:

- If `stop_on_error=True`, raise a `StreamError` or terminate.
- If `False`, yield and continue.

Tests:

- `ERROR` terminates/raises by default.
- Opt-out policy can still yield errors if desired.

#### Typed stream-open HTTP error and stream 401 refresh

Add:

```python
@dataclass(slots=True)
class HTTPStreamOpenError(TransportError):
    status_code: int
    headers: dict[str, str]
    body: bytes
```

Transports should raise `HTTPStreamOpenError` on stream-open HTTP errors.

In `_stream_chunks()`:

```python
refreshed = False
while True:
    token = await self._token_provider.get_access_token()
    try:
        async for chunk in self._transport.stream(request):
            yield chunk
        return
    except HTTPStreamOpenError as exc:
        if exc.status_code == 401 and not refreshed:
            refreshed = True
            await self._token_provider.force_refresh_access_token()
            continue
        raise
```

Tests:

- Stream-open 401 refreshes once and retries.
- Stream-open 401 after refresh raises.
- Non-401 stream-open errors raise without blind retry.

#### Market-depth classification

Update:

```python
def _looks_like_market_data(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "Symbol",
            "Bid",
            "Ask",
            "Last",
            "Close",
            "TimeStamp",
            "Side",
            "Price",
            "Size",
            "Entries",
        )
    )
```

Tests:

- Depth-like payload with `Message` and `Entries` is `DATA`, not `ERROR`.
- Real error payload is still `ERROR`.

Likely files:

- `src/tradestation_api_wrapper/stream.py`
- `src/tradestation_api_wrapper/rest.py`
- `src/tradestation_api_wrapper/transport.py`
- `src/tradestation_api_wrapper/errors.py`
- `tests/unit/test_stream_session.py`

---

### 12. Fix OAuth loopback to handle more than one HTTP request

Current issue:

`authorize_with_loopback()` calls:

```python
await asyncio.to_thread(server.handle_request)
```

That handles exactly one request. Browsers can send a favicon request, preconnect, or other request before the actual OAuth callback.

Required behavior:

Loop until one of these occurs:

- `callback.authorization_code` is set,
- `callback.error` is set,
- absolute timeout expires.

Sketch:

```python
deadline = monotonic() + timeout_seconds
while callback.authorization_code is None and callback.error is None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        break
    server.timeout = min(remaining, 1.0)
    await asyncio.to_thread(server.handle_request)
```

Tests:

- First request to `/favicon.ico`, second request to `/callback?...` succeeds.
- State mismatch fails immediately.
- Timeout still raises `OAuthCallbackTimeout`.

Likely files:

- `src/tradestation_api_wrapper/auth.py`
- `tests/unit/test_auth.py`

---

### 13. Honor explicit `Retry-After` without capping at five seconds

Current issue:

```python
if parsed_retry_after is not None:
    return min(parsed_retry_after, self.max_delay_seconds)
```

This caps explicit server guidance to `max_delay_seconds`, currently five seconds.

Required change:

```python
if parsed_retry_after is not None:
    return parsed_retry_after
```

Also support HTTP-date `Retry-After`:

```python
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
```

Tests:

- `Retry-After: 120` yields `120`, not `5`.
- HTTP-date `Retry-After` parses.
- Exponential backoff without `Retry-After` remains capped.

Likely files:

- `src/tradestation_api_wrapper/rate_limit.py`
- `tests/unit/test_rest_retries.py`

---

### 14. Add async close/context manager support for transport and client

Current issue:

`HttpxAsyncTransport` exposes `aclose()` but has no async context manager. `TradeStationClient` does not store the transport and does not expose `aclose()`.

Required changes:

In `HttpxAsyncTransport`:

```python
async def __aenter__(self) -> "HttpxAsyncTransport":
    return self

async def __aexit__(self, *_: object) -> None:
    await self.aclose()
```

In `TradeStationClient`:

```python
self._transport = transport or UrllibAsyncTransport()
self._rest = TradeStationRestClient(..., transport=self._transport)

async def aclose(self) -> None:
    close = getattr(self._transport, "aclose", None)
    if close is not None:
        await close()

async def __aenter__(self) -> "TradeStationClient":
    return self

async def __aexit__(self, *_: object) -> None:
    await self.aclose()
```

Tests:

- Client calls transport `aclose()` if present.
- Async context manager closes an owned transport.
- No-op close works for `UrllibAsyncTransport`.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/transport.py`
- `tests/unit/test_client_features.py`
- `tests/unit/test_httpx_transport.py`

---

## Priority 2 — API design / usability

### 15. Add typed bar parameters

Current issue:

`get_bars()` and `stream_bars()` still take raw dicts:

```python
params: dict[str, Any] | None = None
```

This stands out because the rest of the wrapper is strongly typed.

Suggested model:

```python
class BarChartParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    interval: int | None = Field(default=None, alias="interval")
    unit: BarUnit | None = Field(default=None, alias="unit")
    bars_back: int | None = Field(default=None, alias="barsBack")
    first_date: datetime | None = Field(default=None, alias="firstDate")
    last_date: datetime | None = Field(default=None, alias="lastDate")
    session_template: BarSessionTemplate | None = Field(default=None, alias="sessionTemplate")
```

Or use exact parameter names/casing from the pinned spec.

Tests:

- Typed params serialize correctly.
- Invalid unit/session template is rejected.
- Existing dict behavior can remain temporarily for backwards compatibility if desired.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/client.py`
- `tests/unit/test_client_features.py`

---

### 16. Add typed option-chain params

Current issue:

`stream_option_chain()` accepts:

```python
params: Mapping[str, object | None] | None = None
```

This should be typed if the wrapper is otherwise typed.

Suggested model:

```python
class OptionChainStreamParams(BaseModel):
    ...
```

Use the pinned spec to define fields.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/client.py`

---

### 17. Add `AdvancedOptionsReplace`

Current issue:

`OrderReplaceRequest` still uses normal `AdvancedOptions`, but the spec has replace-specific advanced options shapes.

Suggested model:

```python
class AdvancedOptionsReplace(BaseModel):
    ...
```

Then:

```python
advanced_options: AdvancedOptionsReplace | None = Field(default=None, alias="AdvancedOptions")
```

Tests:

- Replacement payload emits replace-specific shape.
- Normal order advanced options still work.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

---

### 18. Decide whether group single-account/single-symbol should be model-level

Current state:

`GroupOrderRequest` validates only group shape. `validate_group_for_config()` enforces single account and single symbol.

This is okay if models are payload-only and client validation is required. But `group_order_payload(group)` can serialize a group that client validation would later reject.

Options:

- Add model-level validation for single account/symbol.
- Or document that request models are not fully broker-valid until `validate_*_for_config()` is called.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/validation.py`

---

### 19. Make `fetch_state_snapshot()` concurrent

Current implementation awaits accounts, balances, positions, and orders sequentially.

Suggested implementation:

```python
accounts_payload, balances, positions, orders = await asyncio.gather(
    self.get_accounts(),
    self.get_balances(account_ids),
    self.get_positions(account_ids),
    self.get_orders(account_ids),
)
```

Keep account filtering after gather.

Tests:

- Existing functionality preserved.
- Optional: use a fake transport/sleeper to prove requests can overlap if practical.

Likely file:

- `src/tradestation_api_wrapper/client.py`

---

### 20. Document or change `UrllibAsyncTransport.stream()` default for high-frequency streams

Current default transport uses `asyncio.to_thread(stream.read, 8192)` per chunk. That is fine for simple use but can be expensive for high-frequency streams.

Options:

- Document that serious streaming workloads should use `HttpxAsyncTransport`.
- Consider making `HttpxAsyncTransport` the recommended default for streaming examples.
- Keep `UrllibAsyncTransport` as no-extra fallback.

Likely files:

- `README.md`
- `src/tradestation_api_wrapper/transport.py`

---

### 21. Fix `max_order_notional` default/docs for futures

Current default:

```python
max_order_notional = Decimal("1000")
```

This blocks normal futures contracts even if `allow_futures=True`. That may be desirable as a conservative default, but it should be documented clearly.

Options:

- Keep the conservative default and document it.
- Require futures users to explicitly set `max_order_notional`.
- Make futures validation route to a different max contract count / multiplier model rather than equity notional.

Likely files:

- `README.md`
- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/validation.py`

---

### 22. Categorize or document `STATUS_MESSAGE = "STT"`

`TradeStationOrderStatus.STATUS_MESSAGE` is not in done, active, working, cancelable, or replaceable buckets.

Options:

- Put it in an explicit bucket if semantics are known.
- Add comments/tests proving it intentionally maps to none.

Likely files:

- `src/tradestation_api_wrapper/order_status.py`
- `tests/unit/test_order_status.py`

---

### 23. Enforce timezone-aware reconciliation timestamps

`UnknownOrderFingerprint.submitted_at` is a plain `datetime`. `match_unknown_order()` subtracts it from `snapshot.opened_at`.

If one is naive and one is timezone-aware, Python raises `TypeError`.

Suggested fix:

```python
@field_validator("submitted_at")
@classmethod
def require_timezone_aware(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("submitted_at must be timezone-aware")
    return value
```

Also normalize comparison inputs if needed.

Tests:

- Naive `submitted_at` is rejected.
- Aware timestamp matches aware `opened_at`.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/reconciliation.py`
- `tests/unit/test_reconciliation.py`

---

### 24. File token store compare-and-swap is not multi-process safe

Current `FileTokenStore.compare_and_swap_refresh_token()` is read-check-write without file locking. The `OAuthManager` lock is process-local only.

Options:

- Add cross-process file locking.
- Use platform-specific locking or optional dependency.
- Document that `FileTokenStore` is safe for a single process only.

Tests depend on chosen implementation.

Likely file:

- `src/tradestation_api_wrapper/auth.py`

---

### 25. Add wallet endpoints or explicit spec skip coverage

Wallet endpoints still appear unimplemented based on source search.

Suggested endpoints, if present in pinned spec:

```text
GET /v3/brokerage/accounts/{account}/wallets
GET /v3/brokerage/stream/accounts/{account}/wallets
```

Add:

```python
async def get_wallets(...)
def stream_wallets(...)
```

or explicitly skip with rationale.

Also add a spec coverage test:

```python
def test_openapi_paths_are_wrapped_or_explicitly_skipped() -> None:
    ...
```

Every skipped path should include a reason.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_spec_coverage.py`

---

## Minor cleanup

### A. README wording

The README still says:

```text
The wrapper is correctness-first...
```

That is okay aspirationally, but given the remaining safety gaps, consider adding a “Safety status” section:

```md
## Safety status

This wrapper is conservative by default but still requires caller-side risk controls.
The following stateful limits are not enforced by the wrapper yet: ...
```

Also change `powershell` fences to `bash` or `text` for cross-platform command snippets.

### B. Ruff cleanup

`rest.py` unused imports appear cleaned up. `auth.py` still imports `json` and `Field`, which appear unused. Confirm with `ruff check .`.

### C. Public review docs in repo

The repo contains review markdown files under `GptPro/`. If those are intentional, fine. If not, remove them or move them to an issue tracker.

---

## Suggested implementation order

1. Account-safe replace/cancel.
2. Write-side transient HTTP statuses become `AmbiguousOrderState`.
3. `TradeStationTrade.reconcile_required` checks `ack.errors`.
4. Enforce `allow_extended_hours`; remove/rename/document unenforced stateful risk fields.
5. Fix `asset_class` default and builder support.
6. Add Trade scope validation for trading flags and method-level scope checks.
7. GTD/GTD_PLUS expiration validation.
8. Bracket entry/exit duration API.
9. Successful-response JSON decode wrapper.
10. Stream hardening: incremental UTF-8, auth/config bypass, stream-open 401 refresh, `ERROR` policy, market-depth key classification.
11. OAuth loopback request loop.
12. Honor `Retry-After` without capping explicit server guidance.
13. Async close/context manager support.
14. Typed bar/option-chain params and `AdvancedOptionsReplace`.
15. Spec coverage / wallet endpoints or explicit skips.
16. Lower-priority ergonomics and docs.

---

## One-shot Codex prompt

Use this prompt for the next patch pass:

```text
Patch the latest `stridskoma2/ts-api-wrapper` main branch. Do not place live orders. Keep SIM order-placement tests opt-in.

Prioritize:
1. Require account_id for replace_order/cancel_order and validate account_allowlist; use assert_can_replace_orders/assert_can_cancel_orders and preflight replace by order lookup.
2. Treat write-side HTTP 408/500/502/503/504 as AmbiguousOrderState for submit/replace/cancel; do not retry non-idempotent writes.
3. Make TradeStationTrade.reconcile_required true when ack.errors is non-empty.
4. Enforce allow_extended_hours for DYP/GCP/GDP, and remove/rename/document unenforced max_symbol_position_notional/max_daily_loss/max_daily_order_count.
5. Make order asset_class explicit or safely inferred; avoid defaulting to EQUITY in a way that bypasses option/future checks.
6. Require Trade scope when allow_market_orders/allow_options/allow_futures are enabled, and add method-level scope preflight checks for Trade/ReadAccount/MarketData/Matrix/OptionSpreads where applicable.
7. Validate GTD/GTD_PLUS TimeInForce requires Expiration.
8. Fix bracket_order_group duration semantics with explicit entry_duration and exit_duration.
9. Wrap malformed JSON on successful responses as TradeStationAPIError.
10. Harden streams: incremental UTF-8 decoder, auth/config errors bypass reconnect, typed HTTPStreamOpenError with one-time stream-open 401 refresh, configurable ERROR policy, and market-depth keys in _looks_like_market_data.
11. Fix OAuth loopback to handle multiple HTTP requests before callback.
12. Honor explicit Retry-After without the 5-second cap and support HTTP-date Retry-After.
13. Add async close/context manager support to HttpxAsyncTransport and TradeStationClient.
14. Add typed bar params, typed option-chain params, AdvancedOptionsReplace, and spec coverage/wallet endpoint handling if feasible.
15. Update README with current safety status and run commands.

Add targeted unit tests for each behavior, then run:
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Acceptance criteria

- Unit tests pass.
- Ruff passes.
- Mypy passes.
- Replace/cancel cannot operate outside the account allowlist.
- No non-idempotent write retries occur after ambiguous write failures.
- Transient write responses are marked ambiguous.
- Extended-hours, asset-class, and scope safety gaps are resolved.
- Partial group errors trigger reconciliation.
- Streams handle UTF-8, auth/config errors, stream-open 401, and ERROR payloads predictably.
- README no longer overstates safety or hides unenforced risk controls.

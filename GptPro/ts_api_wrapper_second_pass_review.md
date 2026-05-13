# Codex Follow-up Review: `stridskoma2/ts-api-wrapper`

Repository: `stridskoma2/ts-api-wrapper`  
Review date: 2026-05-13  
Purpose: second-pass review after earlier Codex task brief. This focuses on fixes still missing, regressions introduced, and next implementation priorities.

Do **not** place live orders while working on this. Keep SIM order-placement integration tests opt-in only.

## Executive summary

Most of the important safety fixes are still missing.

The wrapper still has unsafe account handling for replace/cancel, still does not treat transient HTTP responses on write requests as ambiguous, still declares several unenforced risk controls, and still allows option/future safeguards to be bypassed through the default `asset_class=EQUITY`.

There is also a new or worsened streaming issue: non-order/non-position streams now appear to use the v3 stream `Accept` media type by default, even though TradeStation market-data streams generally use the v2 stream media type while order/position streams use v3.

This is still not ready to call production-grade.

## Run checks

Use portable commands, not hard-coded local Codex/Windows paths.

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

## Priority 0: safety and correctness blockers

### 1. `replace_order()` and `cancel_order()` are still not account-safe

Current issue:

- `replace_order()` still accepts only `order_id` and replacement.
- `cancel_order()` still accepts only `order_id`.
- Neither method accepts `account_id`.
- Neither method validates that the account is in `account_allowlist`.
- Neither method preflights the order to prove the target order belongs to an allowlisted account.
- Existing tests still exercise the old shape, for example `client.replace_order("123", replacement)`.

Why this matters:

`account_allowlist` is not a complete safety boundary if order modification/cancellation can target any order ID without account context.

Suggested API:

```python
await client.replace_order(
    account_id="123456789",
    order_id="...",
    replacement=...,
)

await client.cancel_order(
    account_id="123456789",
    order_id="...",
)
```

Suggested behavior:

- Validate `account_id` with `config.assert_account_allowed(account_id)`.
- For `replace_order()`, strongly consider preflighting with:

```python
orders = await client.get_orders_by_id((account_id,), (order_id,))
```

- Reject replacement if no matching order is returned.
- Reject replacement if the returned order has an account ID that does not match `account_id`.
- For `cancel_order()`, validation against `account_allowlist` is mandatory. Preflight is recommended but can be optional if cancellation latency is a concern.

Kill-switch recommendation:

- A kill switch should probably block new risk and replacements.
- A kill switch should probably **not** block cancellations, because cancellation is risk-reducing.

Suggested config methods:

```python
def assert_account_allowed(self, account_id: str) -> None: ...
def assert_can_submit_orders(self, account_id: str) -> None: ...
def assert_can_replace_orders(self, account_id: str) -> None: ...
def assert_can_cancel_orders(self, account_id: str) -> None: ...
```

Minimum tests:

- `replace_order(account_id, order_id, ...)` rejects non-allowlisted account.
- `cancel_order(account_id, order_id)` rejects non-allowlisted account.
- `replace_order()` rejects if preflight lookup returns no matching order.
- `replace_order()` rejects if preflight order belongs to a different account.
- `cancel_order()` remains allowed when the kill switch is active, if that is the intended behavior.
- Existing order-placement behavior remains unchanged.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/errors.py`
- `tests/unit/test_client_features.py`

### 2. Write-side `408/5xx` responses are still not treated as ambiguous

Current issue:

The REST layer treats `NetworkTimeout` and `TransportError` as ambiguous for non-idempotent writes. But if TradeStation returns an HTTP transient response such as:

```text
408
500
502
503
504
```

the wrapper still raises a normal API error instead of `AmbiguousOrderState`.

Why this matters:

For broker writes, a transient HTTP response can mean the broker received and acted on the request but failed to respond cleanly. The wrapper should not imply the order definitely failed.

Suggested implementation:

```python
AMBIGUOUS_WRITE_STATUSES = {408, 500, 502, 503, 504}

if not retry_safe and response.status_code in AMBIGUOUS_WRITE_STATUSES:
    raise AmbiguousOrderState(
        ambiguous_operation or method,
        local_request_id,
        _api_error(response),
    )
```

Do not automatically retry non-idempotent writes.

Keep `429` as a normal `RateLimitError` unless there is evidence that TradeStation can accept a write and still return `429`.

Minimum tests:

- `post_order_write()` raises `AmbiguousOrderState` on `408`, `500`, `502`, `503`, `504`.
- `put_order_write()` raises `AmbiguousOrderState` on those statuses.
- `delete_order_write()` raises `AmbiguousOrderState` on those statuses.
- Write-side `429` still raises `RateLimitError` and is not retried.
- Safe reads still retry retryable responses.

Likely files:

- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_rest_retries.py`
- `tests/unit/test_client_features.py`

### 3. Safety config knobs are still mostly unenforced

Current config fields include:

```python
max_symbol_position_notional
max_daily_loss
max_daily_order_count
allow_extended_hours
```

But validation currently appears to enforce only:

- account allowlist
- kill switch for submissions
- market-order permission
- option/future permission
- max single-order notional
- market-order estimated price

Still missing:

- `allow_extended_hours`
- `max_symbol_position_notional`
- `max_daily_loss`
- `max_daily_order_count`

Why this matters:

Declaring risk controls that are not enforced creates false assurance.

#### Enforce `allow_extended_hours`

Suggested implementation:

```python
EXTENDED_HOURS_DURATIONS = {
    Duration.DAY_PLUS,   # DYP
    Duration.GTC_PLUS,   # GCP
    Duration.GTD_PLUS,   # GDP
}

if (
    order.time_in_force.duration in EXTENDED_HOURS_DURATIONS
    and not config.allow_extended_hours
):
    raise RequestValidationError("extended-hours orders are disabled by configuration")
```

Apply recursively to order groups and OSO children if OSO support remains.

Minimum tests:

- `DYP`, `GCP`, and `GDP` are rejected by default.
- They are accepted when `allow_extended_hours=True`.
- Order groups are checked recursively.

#### Handle stateful risk controls

For these fields:

```python
max_symbol_position_notional
max_daily_loss
max_daily_order_count
```

Either implement state-aware validation or remove/rename them.

Preferred design:

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

Minimum tests:

- If implemented, tests must show these fields are enforced.
- If not implemented, README/config docs must clearly say these are reserved/not yet enforced.

Likely files:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/validation.py`
- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

### 4. Asset-class bypass is still present

Current issue:

`OrderRequest.asset_class` still defaults to `AssetClass.EQUITY`, and builder helpers still do not expose `asset_class`.

Validation only blocks options/futures when:

```python
order.asset_class is AssetClass.OPTION
order.asset_class is AssetClass.FUTURE
```

This means an option-looking or futures-looking symbol can pass as an equity order if the caller forgets to set `asset_class`.

Suggested implementation:

Change default:

```python
asset_class: AssetClass = Field(default=AssetClass.UNKNOWN, exclude=True)
```

Then require explicit asset class for order validation:

```python
if order.asset_class is AssetClass.UNKNOWN:
    raise RequestValidationError("order asset_class must be explicit for risk validation")
```

Expose `asset_class` in all order builders:

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

- Infer asset class via `get_symbols()` before order validation.
- This is more complex and slower but reduces caller burden.

Minimum tests:

- Direct `OrderRequest` with missing/unknown asset class is rejected before submission.
- Option order with `asset_class=OPTION` is rejected unless `allow_options=True`.
- Future order with `asset_class=FUTURE` is rejected unless `allow_futures=True`.
- Builder-created equity orders still work.
- Builders can create option/future orders only when the asset class is explicitly passed.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/builders.py`
- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`
- `tests/unit/test_client_features.py`

## Priority 1: new regression / stream media-type correctness

### 5. Non-order/non-position streams now appear to use the wrong `Accept` media type

Current issue:

`TradeStationRestClient.stream_events()` defaults to:

```python
application/vnd.tradestation.streams.v3+json
```

Order and position streams should use v3.

But current client methods for market-data streams no longer override this default, including:

- `stream_quotes()`
- `stream_bars()`
- `stream_market_depth_aggregates()`
- `stream_market_depth_quotes()`
- `stream_option_chain()`
- `stream_option_quotes()`

TradeStation’s stream documentation indicates:

- order/position streams use `application/vnd.tradestation.streams.v3+json`
- other stream resources generally use `application/vnd.tradestation.streams.v2+json`

Suggested implementation:

Restore explicit v2 accept headers for all non-order/non-position streams:

```python
MARKET_DATA_STREAM_ACCEPT = "application/vnd.tradestation.streams.v2+json"
BROKERAGE_STREAM_ACCEPT = "application/vnd.tradestation.streams.v3+json"
```

Use v3 for:

- `stream_orders()`
- `stream_orders_by_id()`
- `stream_positions()`

Use v2 for:

- quote streams
- bar streams
- market-depth streams
- option-chain streams
- option-quote streams
- any future non-order/non-position stream helper

Minimum tests:

- `stream_orders()` sends v3 accept header.
- `stream_positions()` sends v3 accept header.
- `stream_quotes()` sends v2 accept header.
- `stream_bars()` sends v2 accept header.
- market-depth streams send v2 accept header.
- option streams send v2 accept header.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_client_features.py`
- `tests/unit/test_stream_session.py`

### 6. Spec pinning tool filters out v2 stream media types incorrectly

Current issue:

The current `tools/pin_tradestation_spec.py` removes stream media types that are not:

```python
application/vnd.tradestation.streams.v3+json
```

That bakes the wrong assumption into the pinned v3 spec. v3 endpoint paths can still use v2 stream media types for market-data streams.

Suggested implementation:

Do **not** remove non-v3 stream media types just because they are v2. Keep whichever media type the official spec declares for each v3 endpoint.

Remove or rewrite this behavior:

```python
remove_non_v3_stream_media_types(spec)
```

Suggested replacement:

- Filter paths to `/v3/`.
- Keep all operation-level request/response content media types.
- If cleanup is needed, make it endpoint-aware, not media-version-aware.

Minimum tests:

- Pinning/filtering retains `application/vnd.tradestation.streams.v2+json` for v3 market-data stream endpoints.
- Pinning/filtering retains `application/vnd.tradestation.streams.v3+json` for order/position stream endpoints.
- Pinned spec still includes only `/v3/` paths.

Likely files:

- `tools/pin_tradestation_spec.py`
- `specs/tradestation/openapi.2026-05-09.json`
- optional: `tests/unit/test_spec_pin_tool.py`

## Priority 2: scope, rate limits, streams, lifecycle

### 7. Scope checks are still missing

Current issue:

Default scopes are still read-oriented:

```python
("openid", "offline_access", "MarketData", "ReadAccount")
```

But methods exist that need additional scopes:

- order execution: `Trade`
- option spread/risk endpoints: likely `OptionSpreads`
- market-depth/matrix endpoints: likely `Matrix`

Suggested implementation:

Add a config helper:

```python
def assert_scope_requested(self, scope: str) -> None:
    if scope not in set(self.requested_scopes):
        raise ConfigurationError(f"requested_scopes missing required scope: {scope}")
```

Then call it before methods that require extra scopes.

Suggested mapping:

```python
TRADE_SCOPE = "Trade"
MARKET_DATA_SCOPE = "MarketData"
READ_ACCOUNT_SCOPE = "ReadAccount"
OPTION_SPREADS_SCOPE = "OptionSpreads"
MATRIX_SCOPE = "Matrix"
```

Likely checks:

- `confirm_order`, `place_order`, `replace_order`, `cancel_order`: `Trade`
- order group confirm/place: `Trade`
- brokerage account reads: `ReadAccount`
- quotes/symbols/bars/option expirations/strikes/spread types: `MarketData`
- option risk/reward: `OptionSpreads` if required by the official docs/spec
- market depth streams: `Matrix`

Important caveat:

Checking `requested_scopes` is not proof the actual OAuth token has those scopes. It is still a useful preflight config check. Document that.

Minimum tests:

- Order methods reject configs missing `Trade`.
- Market-depth methods reject configs missing `Matrix`, if that scope is required.
- Option risk/reward rejects configs missing `OptionSpreads`, if required.
- Read-only methods still work with read-only default scopes.

Likely files:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/client.py`
- `tests/unit/test_client_features.py`

### 8. README still contains hard-coded local Windows/Codex paths

Current issue:

README still uses commands like:

```powershell
C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ...
```

Suggested README commands:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

Also adjust wording:

- Do not call the wrapper production-grade until the Priority 0 issues are fixed.
- Explain required scopes for write/order/option/matrix methods.
- Explain SIM tests are opt-in.
- Explain live trading requires explicit config and acknowledgement.
- Explain some risk fields are reserved/not yet enforced if stateful validation is not implemented.

Likely file:

- `README.md`

### 9. Ruff likely still fails because of unused imports

Likely unused imports still present:

- `src/tradestation_api_wrapper/rest.py`
  - `Awaitable`
  - `Callable`
- `src/tradestation_api_wrapper/auth.py`
  - `json`
  - `Field`

Fix these and run:

```bash
python -m ruff check .
```

Likely files:

- `src/tradestation_api_wrapper/rest.py`
- `src/tradestation_api_wrapper/auth.py`

### 10. Explicit `Retry-After` is still capped to five seconds

Current issue:

`RetryPolicy.delay_for_attempt()` still caps parsed `Retry-After` to `max_delay_seconds`, currently five seconds.

That is wrong for explicit server guidance.

Suggested implementation:

- If `Retry-After` is present and valid, honor it.
- Apply `max_delay_seconds` only to local exponential backoff when no server guidance is present.
- Support both numeric seconds and HTTP-date formats.

Example:

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

And in `delay_for_attempt()`:

```python
if parsed_retry_after is not None:
    return parsed_retry_after
```

Minimum tests:

- `Retry-After: 120` returns `120`, not `5`.
- HTTP-date retry-after is parsed.
- Exponential backoff without retry-after remains capped.

Likely files:

- `src/tradestation_api_wrapper/rate_limit.py`
- `tests/unit/test_rest_retries.py`

### 11. Stream hardening is still missing

Current issues:

- `JsonStreamParser.feed()` still decodes each byte chunk with `errors="replace"`.
- This can corrupt multibyte UTF-8 split across chunks.
- Stream `ERROR` messages are yielded but do not terminate/restart by default.
- Stream-open HTTP errors are collapsed into generic `TransportError`.
- Stream 401 cannot force-refresh the token like normal REST requests.

#### Incremental UTF-8 decoder

Suggested implementation:

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

Minimum tests:

- A JSON object with a multibyte character split across chunks parses correctly.
- Malformed UTF-8 behavior is explicit and tested.

#### Stream `ERROR` behavior

Add policy:

```python
@dataclass(frozen=True, slots=True)
class StreamReconnectPolicy:
    max_reconnects: int = 3
    stop_on_error: bool = True
```

When `ERROR` is seen:

- either terminate the stream,
- or raise a dedicated `StreamError`,
- or reconnect if that is the desired behavior.

Minimum tests:

- `ERROR` terminates/raises when `stop_on_error=True`.
- Existing `DATA`, `HEARTBEAT`, `END_SNAPSHOT`, and `GO_AWAY` behavior still works.

#### Stream 401 refresh

Add a typed stream-open error:

```python
@dataclass(slots=True)
class HTTPStreamOpenError(TransportError):
    status_code: int
    body: bytes
    headers: dict[str, str]
```

Transports should raise this on stream open HTTP errors.

Then `_stream_chunks()` can:

- open stream with current token,
- if stream open returns 401 and refresh has not been attempted:
  - call `force_refresh_access_token()`,
  - retry once,
- otherwise propagate the error.

Minimum tests:

- Stream-open 401 refreshes once and retries with refreshed token.
- Stream-open 401 after refresh fails normally.
- Non-401 stream-open errors are not blindly retried forever.

Likely files:

- `src/tradestation_api_wrapper/errors.py`
- `src/tradestation_api_wrapper/transport.py`
- `src/tradestation_api_wrapper/rest.py`
- `src/tradestation_api_wrapper/stream.py`
- `tests/unit/test_stream_session.py`

### 12. `HttpxAsyncTransport.aclose()` is still not surfaced by `TradeStationClient`

Current issue:

`HttpxAsyncTransport` has `aclose()`, but `TradeStationClient` does not store/expose the transport or support async context manager usage.

Suggested implementation:

Store the transport:

```python
self._transport = transport or UrllibAsyncTransport()
self._rest = TradeStationRestClient(
    config=config,
    token_provider=token_provider,
    transport=self._transport,
)
```

Add:

```python
async def aclose(self) -> None:
    close = getattr(self._transport, "aclose", None)
    if close is not None:
        await close()

async def __aenter__(self) -> "TradeStationClient":
    return self

async def __aexit__(self, *exc_info: object) -> None:
    await self.aclose()
```

Minimum tests:

- Client calls transport `aclose()` if present.
- Async context manager closes owned transport.
- No-op close works for `UrllibAsyncTransport`.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `tests/unit/test_client_features.py`
- `tests/unit/test_httpx_transport.py`

## Priority 3: model/spec/API coverage

### 13. `AdvancedOptionsReplace` is still missing

Current issue:

The pinned spec distinguishes normal `AdvancedOptions` from `AdvancedOptionsReplace`, but `OrderReplaceRequest` still uses normal `AdvancedOptions`.

Suggested implementation:

Add models:

```python
class MarketActivationRulesReplace(BaseModel): ...
class TimeActivationRulesReplace(BaseModel): ...
class AdvancedOptionsReplace(BaseModel): ...
```

Then:

```python
class OrderReplaceRequest(BaseModel):
    advanced_options: AdvancedOptionsReplace | None = Field(default=None, alias="AdvancedOptions")
```

Minimum tests:

- Replace payload emits replace-specific advanced-option shapes.
- Normal order advanced options still work.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`

### 14. Wallet endpoints still appear missing

Likely missing endpoints:

```text
GET /v3/brokerage/accounts/{account}/wallets
GET /v3/brokerage/stream/accounts/{account}/wallets
```

Check the pinned OpenAPI spec before implementing exact path and envelope names.

Suggested API:

```python
async def get_wallets(self, account_id: str) -> tuple[WalletSnapshot, ...]:
    ...

def stream_wallets(self, account_id: str) -> AsyncIterator[StreamEvent]:
    ...
```

Use permissive response models via `TradeStationEnvelope`.

Minimum tests:

- Account allowlist is enforced.
- REST path is built correctly.
- Response parses into typed model.
- Stream helper uses the correct media type from the spec.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/__init__.py`
- `tests/unit/test_client_features.py`

### 15. Spec coverage test still missing

Current issue:

There is a pinned OpenAPI spec and lock file, but no test that forces the wrapper to acknowledge new/missing v3 paths.

Suggested implementation:

```python
WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS = {
    "/v3/brokerage/accounts",
    ...
}

EXPLICITLY_SKIPPED = {
    "/v3/some/path": "reason",
}

def test_openapi_paths_are_wrapped_or_explicitly_skipped() -> None:
    spec = json.loads(Path("specs/tradestation/openapi.2026-05-09.json").read_text())
    paths = set(spec["paths"])
    missing = paths - WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS - set(EXPLICITLY_SKIPPED)
    assert not missing
```

Every skipped endpoint should have a reason.

Minimum tests:

- The test fails when a new spec path is unwrapped and unskipped.
- Skip list entries include non-empty reasons.

Likely files:

- `tests/unit/test_spec_coverage.py`
- `specs/tradestation/openapi.2026-05-09.json`

## Improvements already made

Some things did improve since the first pass:

- Python support now targets 3.11 instead of 3.12, which is a good compatibility improvement.
- `market_order()` now requires `estimated_price`, matching risk-validation expectations.
- The pinned spec is now filtered to v3-only paths/material, which may be reasonable in concept, but the stream media-type filter must be fixed.

## Suggested final acceptance criteria

This follow-up work is done when:

- `python -m unittest discover -s tests` passes.
- `python -m ruff check .` passes.
- `python -m mypy src tests` passes.
- Replace/cancel require account context and cannot operate outside `account_allowlist`.
- Write-side `408/500/502/503/504` are treated as `AmbiguousOrderState`.
- `allow_extended_hours` is enforced or removed.
- Stateful risk fields are enforced or clearly documented as not yet enforced.
- Asset class can no longer silently default to equity in a way that bypasses option/future guards.
- Non-order/non-position streams use the v2 stream `Accept` media type.
- The spec pinning tool no longer deletes valid v2 stream media types from v3 endpoint specs.
- Required scopes are checked or clearly documented.
- README uses portable commands.
- Ruff unused imports are fixed.
- Explicit `Retry-After` is honored without the 5-second cap.
- Stream parser uses incremental UTF-8 decoding.
- Stream-open 401 can refresh once.
- `HttpxAsyncTransport.aclose()` is reachable through `TradeStationClient`.
- Missing endpoints are either wrapped or explicitly skipped with reasons.

## One-shot Codex prompt

Use this prompt for the next implementation pass:

> Most Priority 0 fixes are still missing. Patch `stridskoma2/ts-api-wrapper` to require account context for replace/cancel and validate against `account_allowlist`; treat write-side `408/500/502/503/504` as `AmbiguousOrderState`; enforce `allow_extended_hours` or remove it; make `asset_class` explicit or safely inferred; restore v2 stream `Accept` headers for all non-order/non-position streams; stop filtering v2 stream media types out of the pinned v3 spec; add scope preflight checks for `Trade`, `OptionSpreads`, and `Matrix` where required; honor explicit `Retry-After` without the 5-second cap; add stream 401 refresh/error handling and incremental UTF-8 decoding; expose client `aclose()` and async context-manager support; fix README commands and ruff unused imports. Add targeted unit tests for each behavior and run unittest, ruff, and mypy.

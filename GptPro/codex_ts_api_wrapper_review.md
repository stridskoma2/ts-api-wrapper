# Codex Task Brief: Review/Fix `stridskoma2/ts-api-wrapper`

Repository: `stridskoma2/ts-api-wrapper`  
Goal: harden the TradeStation v3 wrapper before calling it production-grade.

Do **not** place live orders while working on this. Keep SIM order-placement integration tests opt-in only.

## Executive summary

The architecture is good: `client`, `rest`, `transport`, `models`, `validation`, and `trade` are separated cleanly; ambiguous order state is treated as a first-class concept; response models are permissive enough for a broker API that may add fields.

The main problem is that the wrapper currently overclaims safety. Fix account-safety gaps, ambiguous write handling, unenforced config knobs, asset-class validation, scope checks, and missing endpoint coverage.

## Run checks

Use normal portable commands, not the hard-coded Windows/Codex runtime path currently in the README.

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

## Priority 0: safety fixes

### 1. Make `replace_order()` and `cancel_order()` account-safe

Current issue:

- `place_order()` validates the order account through `validate_order_for_config()`.
- `replace_order(order_id, replacement)` and `cancel_order(order_id)` take only an order ID.
- `validate_replace_for_config()` has no account context.
- This means `account_allowlist` is not a complete safety boundary.

Preferred implementation:

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

- For `replace_order()`:
  - Validate `account_id` with the config.
  - Prefer a preflight fetch using `get_orders_by_id((account_id,), (order_id,))`.
  - Require the returned order to belong to the requested account.
  - Raise `RequestValidationError` if no matching order is found.
  - Then call the existing `/orderexecution/orders/{order_id}` endpoint.
- For `cancel_order()`:
  - Validate `account_id` with the config.
  - Do **not** necessarily block cancellation because of the kill-switch file; cancellation is risk-reducing.
  - Consider adding separate config methods:
    - `assert_account_allowed(account_id)`
    - `assert_can_submit_orders(account_id)`
    - `assert_can_modify_orders(account_id)`
    - `assert_can_cancel_orders(account_id)`

Minimum tests:

- `cancel_order()` rejects non-allowlisted account.
- `replace_order()` rejects non-allowlisted account.
- `replace_order()` rejects if preflight order lookup returns no matching account/order.
- `cancel_order()` still works when the kill switch is active, if that is the intended risk-reducing behavior.
- Existing order-placement tests still pass.

Files likely involved:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/errors.py`
- `tests/unit/test_client_features.py`

### 2. Treat write-side 5xx/408 responses as ambiguous

Current issue:

- Write requests treat `NetworkTimeout` and `TransportError` as `AmbiguousOrderState`.
- If a write returns HTTP `408`, `500`, `502`, `503`, or `504`, the wrapper raises a normal API error.
- For non-idempotent broker writes, a transient server response can still mean the broker received/acted on the request but failed before responding clearly.

Suggested implementation in `TradeStationRestClient.request_json()`:

```python
AMBIGUOUS_WRITE_STATUSES = {408, 500, 502, 503, 504}

if not retry_safe and response.status_code in AMBIGUOUS_WRITE_STATUSES:
    raise AmbiguousOrderState(
        ambiguous_operation or method,
        local_request_id,
        _api_error(response),
    )
```

Keep `429` as a normal `RateLimitError` unless there is evidence that TradeStation can accept a write and still return `429`.

Minimum tests:

- `post_order_write()` returns/raises `AmbiguousOrderState` on `408`, `500`, `502`, `503`, `504`.
- `put_order_write()` and `delete_order_write()` get equivalent coverage.
- `429` still returns `RateLimitError` and is not retried for writes.
- Safe reads still retry retryable responses.

Files likely involved:

- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_rest_retries.py`
- `tests/unit/test_client_features.py`

### 3. Enforce or remove safety config knobs that are currently not enforced

Current issue:

`TradeStationConfig` declares:

- `max_symbol_position_notional`
- `max_daily_loss`
- `max_daily_order_count`
- `allow_extended_hours`

But validation appears to enforce only:

- account allowlist
- kill-switch for submissions
- `max_order_notional`
- `allow_market_orders`
- `allow_options`
- `allow_futures`

This creates false assurance.

Suggested changes:

#### Enforce `allow_extended_hours`

Block extended-hours time-in-force durations unless `allow_extended_hours=True`.

Likely extended-hours durations from the current enum:

```python
EXTENDED_HOURS_DURATIONS = {
    Duration.DAY_PLUS,  # DYP
    Duration.GTC_PLUS,  # GCP
    Duration.GTD_PLUS,  # GDP
}
```

Validation:

```python
if (
    order.time_in_force.duration in EXTENDED_HOURS_DURATIONS
    and not config.allow_extended_hours
):
    raise RequestValidationError("extended-hours orders are disabled by configuration")
```

#### Deal with stateful controls

For these:

- `max_symbol_position_notional`
- `max_daily_loss`
- `max_daily_order_count`

Either implement a state-aware risk guard or remove/rename them so users do not think they are active.

Preferred design:

```python
class RiskSnapshot(BaseModel):
    positions: tuple[PositionSnapshot, ...]
    orders_today: int
    realized_daily_loss: Decimal | None = None

def validate_order_for_state(
    order: OrderRequest,
    config: TradeStationConfig,
    state: RiskSnapshot,
) -> None:
    ...
```

Then expose an optional high-level flow:

```python
snapshot = await client.fetch_state_snapshot((account_id,))
validate_order_for_state(order, config, snapshot)
```

Minimum tests:

- Extended-hours durations are rejected by default.
- Extended-hours durations are accepted when `allow_extended_hours=True`.
- Either stateful fields are enforced by tests or README/config docs explicitly say they are not enforced yet.

Files likely involved:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/validation.py`
- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

### 4. Fix asset-class bypass

Current issue:

- `OrderRequest.asset_class` defaults to `AssetClass.EQUITY`.
- `asset_class` is excluded from the TradeStation payload.
- `allow_options=False` and `allow_futures=False` only work if callers explicitly set `asset_class=OPTION` or `FUTURE`.
- A user can submit an option-looking symbol while leaving the default `EQUITY`.

Preferred implementation:

- Change the default to `AssetClass.UNKNOWN`.
- Require explicit asset class before validating/submitting an order, unless a safe inference mechanism is implemented.
- Add `asset_class` parameters to builder helpers.

Example:

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

Validation:

```python
if order.asset_class is AssetClass.UNKNOWN:
    raise RequestValidationError("order asset_class must be explicit for risk validation")
```

Minimum tests:

- Direct `OrderRequest` with unknown/missing asset class is rejected at submission validation.
- Option order with `asset_class=OPTION` is rejected unless `allow_options=True`.
- Future order with `asset_class=FUTURE` is rejected unless `allow_futures=True`.
- Builder-created equity orders still work.

Files likely involved:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/builders.py`
- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`
- `tests/unit/test_client_features.py`

## Priority 1: correctness and coverage

### 5. Add scope checks or explicit scope docs

Current issue:

Default scopes are read-oriented:

```python
("openid", "offline_access", "MarketData", "ReadAccount")
```

But the README prominently demonstrates order placement, and some methods need additional scopes:

- order execution: `Trade`
- options spread/risk endpoints: `OptionSpreads`
- market depth/matrix endpoints: `Matrix`

Suggested implementation:

Add a config helper:

```python
def assert_scope_requested(self, scope: str) -> None:
    if scope not in set(self.requested_scopes):
        raise ConfigurationError(f"requested_scopes missing required scope: {scope}")
```

Call it before methods that need specific scopes:

- `Trade`: confirm/place/replace/cancel routes and activation-trigger endpoints if applicable.
- `OptionSpreads`: option risk/reward and other spread-specific option helpers if applicable.
- `Matrix`: market depth helpers.

Note: the actual OAuth token scope can differ from `requested_scopes`. This check is not a cryptographic guarantee; it is still useful as an early configuration error. Document that.

Minimum tests:

- Order methods reject configs missing `Trade`.
- Market depth stream rejects configs missing `Matrix`.
- Option risk/reward rejects configs missing `OptionSpreads`, if that endpoint requires it.
- Read-only market data still works with read-only default scopes.

Files likely involved:

- `src/tradestation_api_wrapper/config.py`
- `src/tradestation_api_wrapper/client.py`
- `tests/unit/test_client_features.py`

### 6. Add wallet endpoints

Likely missing endpoints:

```text
GET /v3/brokerage/accounts/{account}/wallets
GET /v3/brokerage/stream/accounts/{account}/wallets
```

Check the pinned OpenAPI spec before implementing exact models and payload envelope names.

Suggested client API:

```python
async def get_wallets(self, account_id: str) -> tuple[WalletSnapshot, ...]:
    ...

def stream_wallets(self, account_id: str) -> AsyncIterator[StreamEvent]:
    ...
```

Suggested model approach:

- Add a permissive `WalletSnapshot(TradeStationEnvelope)`.
- Include known fields from the pinned spec.
- Use `extra="allow"` through `TradeStationEnvelope` to avoid brittleness.

Minimum tests:

- Path is built exactly.
- Account allowlist is enforced.
- Response parses into typed model.
- Stream helper uses the expected media type from the spec.

Files likely involved:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_client_features.py`

### 7. Add spec coverage checks

Current issue:

There is a pinned OpenAPI spec and lock file, but no coverage test appears to verify that intended v3 endpoints are wrapped or consciously skipped.

Suggested implementation:

Add a test such as:

```python
WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS = {
    "/brokerage/accounts",
    "/brokerage/accounts/{accountIDs}/balances",
    ...
}

def test_openapi_paths_are_wrapped_or_explicitly_skipped():
    spec = json.loads(Path("specs/tradestation/openapi.2026-05-09.json").read_text())
    paths = set(spec["paths"])
    missing = paths - WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS
    assert not missing
```

Use an explicit skip list with reasons:

```python
EXPLICITLY_SKIPPED = {
    "/some/path": "not exposed because ...",
}
```

Minimum tests:

- The test fails if the pinned spec adds a new endpoint and the wrapper neither wraps nor skips it.
- The skip list includes rationale.

Files likely involved:

- `tests/unit/test_spec_coverage.py`
- `specs/tradestation/openapi.2026-05-09.json`

## Priority 2: streaming hardening

### 8. Make stream error handling explicit

Current issue:

- `classify_stream_message()` labels stream error messages as `StreamEventKind.ERROR`.
- `TradeStationStream.events()` yields them and continues.
- TradeStation stream errors often require terminating/restarting the stream.

Suggested implementation options:

Option A: strict default

```python
@dataclass(frozen=True, slots=True)
class StreamReconnectPolicy:
    max_reconnects: int = 3
    stop_on_error: bool = True
```

When `ERROR` is seen, terminate or raise `StreamError`.

Option B: permissive default but documented

- Keep yielding errors.
- Add `stop_on_error=True` opt-in.
- Document caller responsibility clearly.

Minimum tests:

- Stream `ERROR` event terminates/raises when configured.
- Existing data, heartbeat, `EndSnapshot`, and `GoAway` behavior still works.

Files likely involved:

- `src/tradestation_api_wrapper/stream.py`
- `tests/unit/test_stream_session.py`

### 9. Refresh token when stream open receives 401

Current issue:

- Normal REST requests refresh once on 401.
- Stream opening converts HTTP errors into `TransportError`, so an expired token can cause reconnect attempts with the same stale token.

Suggested implementation:

Introduce a typed stream-open error:

```python
@dataclass(slots=True)
class HTTPStreamOpenError(TransportError):
    status_code: int
    body: bytes
    headers: dict[str, str]
```

Have transports raise it on stream open HTTP errors.

Then in `TradeStationRestClient._stream_chunks()`:

- Open stream with current token.
- If stream open returns 401 and refresh has not been attempted:
  - call `force_refresh_access_token()`
  - retry once.
- Otherwise propagate error.

Minimum tests:

- Stream open 401 refreshes once and retries with refreshed token.
- Stream open 401 after refresh fails normally.
- Non-401 stream open errors are not blindly retried forever.

Files likely involved:

- `src/tradestation_api_wrapper/errors.py`
- `src/tradestation_api_wrapper/transport.py`
- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_stream_session.py`

### 10. Use an incremental UTF-8 decoder for stream parsing

Current issue:

`JsonStreamParser.feed()` decodes each byte chunk with:

```python
chunk.decode("utf-8", errors="replace")
```

If a UTF-8 multibyte character is split across chunks, the parser can silently corrupt a JSON string.

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

- A JSON object containing a multibyte character split across byte chunks parses correctly.
- Malformed UTF-8 behavior is deliberate and tested.

Files likely involved:

- `src/tradestation_api_wrapper/stream.py`
- `tests/unit/test_stream_session.py`

## Priority 3: rate limits, models, and ergonomics

### 11. Do not cap explicit `Retry-After` at 5 seconds

Current issue:

`RetryPolicy.delay_for_attempt()` parses `Retry-After` but caps it to `max_delay_seconds`. That is wrong for explicit server guidance.

Suggested implementation:

- If `Retry-After` is present and valid, honor it.
- Apply `max_delay_seconds` only to local exponential backoff.
- Add support for HTTP-date `Retry-After`, not just numeric seconds.

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
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None
```

Minimum tests:

- Numeric `Retry-After: 120` returns about `120`, not `5`.
- HTTP-date retry-after parses.
- Exponential backoff remains capped.

Files likely involved:

- `src/tradestation_api_wrapper/rate_limit.py`
- `tests/unit/test_rest_retries.py`

### 12. Add `AdvancedOptionsReplace`

Current issue:

The pinned spec distinguishes normal advanced options from replace advanced options. `OrderReplaceRequest` currently reuses `AdvancedOptions`.

Suggested implementation:

- Add `AdvancedOptionsReplace`.
- Use the replace-specific shapes for:
  - market activation rules
  - time activation rules
  - trailing stop
  - show-only quantity
- Update `OrderReplaceRequest.advanced_options` to use `AdvancedOptionsReplace`.

Minimum tests:

- Replacement payload emits spec-correct advanced-option shape.
- Normal order `AdvancedOptions` still works.

Files likely involved:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/validation.py`
- `tests/unit/test_models_and_validation.py`

### 13. Add async close/context manager support

Current issue:

`HttpxAsyncTransport` owns an `httpx.AsyncClient` when no client is passed and exposes `aclose()`, but `TradeStationClient` does not expose `aclose()` or async context-manager support.

Suggested implementation:

```python
class TradeStationClient:
    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def __aenter__(self) -> "TradeStationClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
```

This likely requires storing `self._transport`.

Minimum tests:

- Client calls transport `aclose()` if present.
- Async context manager closes owned transport.

Files likely involved:

- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/transport.py`
- `tests/unit/test_httpx_transport.py`
- `tests/unit/test_client_features.py`

### 14. Clean up ruff issues

Likely unused imports:

- `rest.py`: `Awaitable`, `Callable`
- `auth.py`: `json`, `Field`

Run `ruff check .` and fix all findings.

Files likely involved:

- `src/tradestation_api_wrapper/rest.py`
- `src/tradestation_api_wrapper/auth.py`

### 15. Fix README commands and wording

Current README hard-codes a local Windows Codex runtime path. Replace it with portable commands.

Suggested README sections:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

Also adjust safety wording:

- Do not call the wrapper “production-grade” until the Priority 0 issues are fixed.
- Mention that SIM order-placement tests require `TRADESTATION_SIM_TRADE_TESTS=1`.
- Mention required scopes for write/order/option/matrix methods.
- Mention that live trading requires explicit `LIVE` config and acknowledgement.

Files likely involved:

- `README.md`

## Additional design notes

### Cancellation and kill switch

A kill switch should probably block new risk and replacements, but allow cancellations. If you keep one `kill_switch_file`, be explicit:

- `assert_can_submit_orders()` should check kill switch.
- `assert_can_replace_orders()` should probably check kill switch.
- `assert_can_cancel_orders()` should probably not check kill switch.

### Scope checks are not token verification

Checking `config.requested_scopes` is only a preflight config check. The access token might still have different scopes. Do not claim this proves token authorization.

### Wallet models should be permissive

Use `extra="allow"` because broker response fields may change. Avoid brittle schema assumptions unless the pinned OpenAPI spec is explicit.

### Spec coverage should include skip reasons

A coverage test with a skip list is useful only if each skipped endpoint has a reason. Avoid a giant silent allowlist.

## Suggested final acceptance criteria

The work is done when:

- `python -m unittest discover -s tests` passes.
- `python -m ruff check .` passes.
- `python -m mypy src tests` passes.
- Replace/cancel require account context and cannot operate outside the allowlist.
- Write-side `408/500/502/503/504` are treated as ambiguous for non-idempotent writes.
- `allow_extended_hours` is enforced or removed.
- Unenforced stateful risk fields are either implemented or clearly documented as not enforced.
- Asset class can no longer silently default to equity in a way that bypasses option/future guards.
- Scope requirements are either enforced as config preflight checks or clearly documented.
- Wallet endpoints are either wrapped or explicitly skipped in a spec coverage file.
- Stream 401/error/UTF-8 behaviors have tests.
- README uses portable commands and does not overstate production readiness.

## One-shot Codex prompt

Use the following as the implementation prompt:

> Patch `stridskoma2/ts-api-wrapper` according to this task brief. Prioritize safety fixes first: account-safe replace/cancel, ambiguous write handling for transient HTTP write responses, enforcement/removal of unused safety config knobs, and asset-class validation. Add targeted unit tests for every behavior change. Do not place live orders. Keep SIM order-placement tests opt-in. After patching, run `python -m unittest discover -s tests`, `python -m ruff check .`, and `python -m mypy src tests`; fix failures before finishing. Update README with portable commands and honest safety wording.

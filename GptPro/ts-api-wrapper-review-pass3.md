# Code Review Pass 3: `stridskoma2/ts-api-wrapper`

> Reviewed against commits up to `5104996 Add v3 option and account helpers`.
> This pass covers what the two new commits fixed, what new issues they introduced, and what remains unresolved from earlier passes.

---

## What Got Fixed

The two new commits addressed the majority of the previously flagged issues. Confirmed resolved:

- **Loopback OAuth single-request bug** — now loops `handle_request` with a monotonic deadline until the code arrives or timeout elapses.
- **Reconnect counter never resets** — `reconnects = 0` now fires after every successful non-GO_AWAY yield, giving a correct rolling window instead of a lifetime budget.
- **Auth errors burning reconnect budget** — `except (AuthenticationError, ConfigurationError): raise` now bypasses the reconnect loop entirely.
- **GTD without Expiration** — `TimeInForce` now has a `model_validator` that rejects GTD/GTD_PLUS with `expiration=None`.
- **`reconcile_required` missing partial failures** — now includes `bool(self.ack.errors)` so a group ack with errors correctly signals reconciliation.
- **`JSONDecodeError` on 200 response** — now caught and re-raised as `TradeStationAPIError`.
- **`Trade` scope not validated on config** — enforced at construction time in `validate_environment_contract`.
- **`HttpxAsyncTransport` missing context manager** — `__aenter__` / `__aexit__` added.
- **`_looks_like_market_data` missing depth keys** — now covers `Bids`, `Asks`, `BidLevels`, `AskLevels`, `Side`, `Price`, `Size`.
- **`requested_scopes` non-deterministic ordering** — now uses `tuple(sorted(scopes))`.
- **`bracket_order_group` hardcoded `Duration.DAY`** — now has explicit `entry_duration` / `exit_duration` parameters.
- **`market_order` `estimated_price` looks optional** — now required in the builder signature.
- **Operator precedence in `classify_stream_message`** — now has explicit parentheses.
- **`max_daily_order_count` / `max_daily_loss` etc. silent non-enforcement** — now documented via `Field(description=...)` acknowledging they're intentionally stateless.
- **Server-side 5xx on writes not ambiguous** — `AMBIGUOUS_WRITE_STATUSES = {408, 500, 502, 503, 504}` now correctly raises `AmbiguousOrderState` instead of a plain API error.
- **`_stream_chunks` 401 handling** — now has its own refresh-and-retry loop independent of the reconnect budget.
- **`TradeStationClient` no context manager** — `__aenter__` / `__aexit__` / `aclose()` added; `aclose()` delegates to the transport.

---

## New Issues Introduced

### 1. `option_risk_reward_payload` switched to `_stringify_decimals` — API spec violation (`validation.py`) 🔴

```python
# Before (correct):
def option_risk_reward_payload(request: OptionRiskRewardRequest) -> dict[str, Any]:
    return _numeric_decimals(...)

# After (wrong):
def option_risk_reward_payload(request: OptionRiskRewardRequest) -> dict[str, Any]:
    return _stringify_decimals(...)
```

The TradeStation OpenAPI spec for `POST /v3/marketdata/options/riskreward` defines:

```json
"SpreadPrice": { "type": "number", "format": "double" }
"Quantity":    { "type": "integer", "format": "int32" }
```

Both must be JSON numbers. `_stringify_decimals` now sends `"0.24"` and `"1"` (strings). The live API will reject these. To make matters worse, the test was updated to assert the wrong value:

```python
# test_client_features.py — asserts a string, which is incorrect per spec:
self.assertEqual(transport.requests[4].json_body["SpreadPrice"], "0.24")
```

All 84 unit tests pass and the regression is invisible without hitting the live API.

**Fix:** Revert `option_risk_reward_payload` to `_numeric_decimals`, and fix the precision problem it originally had without changing serializers:

```python
def _numeric_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        # Use str→float conversion to avoid binary float representation noise
        return float(str(value))
    ...
```

Also revert the test assertion to `0.24` (float).

---

### 2. `OrderRequest.asset_class` defaults to `UNKNOWN` — silent breaking change for non-builder usage (`models.py` / `validation.py`) 🔴

```python
# Before:
asset_class: AssetClass = Field(default=AssetClass.EQUITY, exclude=True)

# After:
asset_class: AssetClass = Field(default=AssetClass.UNKNOWN, exclude=True)
```

Validation now rejects `UNKNOWN`:

```python
if order.asset_class is AssetClass.UNKNOWN:
    raise RequestValidationError("order asset_class must be set explicitly for risk checks")
```

Any code building `OrderRequest` directly (not through the builders) now fails at `validate_order_for_config` with a non-obvious error. This is a hard silent break — no deprecation warning, no version bump, nothing in the README. Users building ES/MES futures orders via `OrderRequest(asset_class=AssetClass.FUTURE, ...)` are fine, but anyone who previously relied on the EQUITY default now has a latent runtime failure.

**Fix:** Add a migration note to the README and/or a clear error message that names the field that needs to be set. Consider keeping `EQUITY` as the default and instead making the futures/options guards check explicitly rather than requiring all callers to opt in.

---

### 3. `replace_order` and `cancel_order` are breaking API changes without a version bump (`client.py`) 🟡

Old signatures:
```python
async def replace_order(self, order_id: str, replacement: ...) -> TradeStationTrade
async def cancel_order(self, order_id: str) -> dict[str, Any]
```

New signatures:
```python
async def replace_order(self, account_id: str, order_id: str, replacement: ...) -> TradeStationTrade
async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]
```

Both fail at runtime with `TypeError` for any existing caller. The version is still `0.1.0` and there is no changelog or migration guide. Pre-1.0 breakage is common, but at minimum this deserves a note in a CHANGELOG or the README's migration section, since any automated trading system calling these methods by position will silently shift its arguments and either hit a `ConfigurationError` (account_id mismatch) or a wrong-order cancel.

---

### 4. `replace_order` has an undocumented `ReadAccount` scope dependency (`client.py`) 🟡

`replace_order` internally calls `_assert_order_belongs_to_account`, which calls `get_orders_by_id`, which calls `_require_scope(READ_ACCOUNT_SCOPE)`. A user configured with only `Trade` scope gets:

```
ConfigurationError: requested_scopes missing required scope: ReadAccount
```

…raised from deep inside `replace_order`, with no mention of `ReadAccount` in the method signature, docstring, or the `TRADE_SCOPE` check at the top of the method. The scope check fires after the `TRADE_SCOPE` guard, so it's even more surprising.

**Fix:** Either document the dependency explicitly in the method docstring, or add `_require_scope(READ_ACCOUNT_SCOPE)` at the top of `replace_order` alongside the `TRADE_SCOPE` check so the error is immediate and predictable.

---

### 5. `stream_events` hardcodes `raise_on_error=True` with no way to opt out (`rest.py`) 🟡

`TradeStationStream` now has `raise_on_error: bool = True`, which causes any `StreamEventKind.ERROR` payload to immediately raise `StreamError` and terminate the stream. But `stream_events` constructs it without exposing the parameter:

```python
def stream_events(self, path: str, *, accept: str = ...) -> AsyncIterator[StreamEvent]:
    stream = TradeStationStream(lambda: self._stream_chunks(path, accept=accept))
    return stream.events()
```

This is a problem in practice. TS quote streams send error events for non-fatal conditions — for example, if one symbol in a multi-symbol stream subscription is invalid or delisted, TS sends an error payload for that symbol while continuing to stream data for the rest. With `raise_on_error=True`, the entire stream dies on the first such event, taking down all other symbols.

**Fix:** Expose `raise_on_error` through `stream_events` and all `stream_*` methods on `TradeStationClient`:

```python
def stream_events(
    self,
    path: str,
    *,
    accept: str = BROKERAGE_STREAM_ACCEPT,
    raise_on_error: bool = True,
) -> AsyncIterator[StreamEvent]:
    stream = TradeStationStream(
        lambda: self._stream_chunks(path, accept=accept),
        raise_on_error=raise_on_error,
    )
    return stream.events()
```

---

### 6. `BarUnit` and `BarSessionTemplate` enums are orphaned — defined but never wired in (`models.py`) 🟢

Two new enums were added and exported in `__all__`:

```python
class BarUnit(str, Enum):
    MINUTE = "Minute"
    DAILY = "Daily"
    WEEKLY = "Weekly"
    MONTHLY = "Monthly"

class BarSessionTemplate(str, Enum):
    USEQ_PRE = "USEQPre"
    ...
```

But `get_bars` and `stream_bars` still accept `dict[str, Any]`:

```python
async def get_bars(self, symbol: str, *, params: dict[str, Any] | None = None) -> ...:
```

The enums exist but nothing enforces or uses them. This looks like the start of a typed `BarParams` model that wasn't finished. Either complete it or remove the enums until the typed interface is ready — exported but unused types create false confidence.

---

## Still Unresolved From Earlier Passes

These were flagged in passes 1 and 2 and remain in the current code:

| # | File | Severity | Issue |
|---|------|----------|-------|
| 7 | `client.py` | 🟡 Medium | `get_bars` / `stream_bars` take untyped `dict` — enums now exist (`BarUnit`, `BarSessionTemplate`) but aren't wired into a typed `BarParams` model |
| 8 | `client.py` | 🟢 Low | `fetch_state_snapshot` still makes 4 sequential awaits — `asyncio.gather` would halve the latency |
| 10 | `validation.py` | 🟢 Low | Single-symbol constraint for group orders enforced only in `validate_group_for_config`, not in `GroupOrderRequest` itself |
| 12 | `transport.py` | 🟡 Medium | `UrllibAsyncTransport.stream()` still dispatches one thread-pool task per 8192-byte chunk; `HttpxAsyncTransport` doesn't have this overhead |
| — | `client.py` | 🟡 Medium | `_get_order_pages` has no maximum page guard — a misbehaving API that always returns a `NextToken` loops indefinitely |
| 5 | `auth.py` | 🟡 Medium | `FileTokenStore.compare_and_swap_refresh_token` is still not multi-process safe — the read/check/write is not atomic across OS processes |

---

## Complete Issue Index (All Three Passes)

| ID | File | Status | Severity | Issue |
|----|------|--------|----------|-------|
| 1 | `stream.py` | ✅ Fixed | — | `or`/`and` precedence in `classify_stream_message` |
| 2 | `auth.py` | ✅ Fixed | — | `authorize_with_loopback` handled only one HTTP request |
| 3 | `stream.py` | ✅ Fixed | — | `reconnects` counter never reset |
| 4 | `rest.py` | ✅ Fixed | — | Token-expiry reconnects burned reconnect budget |
| 5 | `auth.py` | ⚠️ Open | 🟡 Medium | `FileTokenStore` CAS not multi-process safe |
| 6 | `validation.py` | ✅ Fixed (differently) | — | `_numeric_decimals` float precision — but see issue P3-1 |
| 7 | `client.py` | ⚠️ Open | 🟡 Medium | `get_bars` / `stream_bars` untyped `dict` params |
| 8 | `client.py` | ⚠️ Open | 🟢 Low | `fetch_state_snapshot` 4 sequential awaits |
| 9 | `builders.py` | ✅ Fixed | — | `market_order` `estimated_price` looked optional |
| 10 | `validation.py` | ⚠️ Open | 🟢 Low | Single-symbol group constraint not on the model |
| 11 | `config.py` | ✅ Fixed | — | `requested_scopes` non-deterministic ordering |
| 12 | `transport.py` | ⚠️ Open | 🟡 Medium | `UrllibAsyncTransport` thread-per-chunk overhead |
| A | `builders.py` | ✅ Fixed | — | `bracket_order_group` hardcoded `Duration.DAY` for entry |
| B | `models.py` | ✅ Fixed | — | GTD/GTD_PLUS accepted `None` expiration |
| C | `rest.py` | ✅ Fixed | — | `JSONDecodeError` on 200 escaped as raw exception |
| D | `trade.py` | ✅ Fixed | — | `reconcile_required` False for partial group failures |
| E | `stream.py` | ✅ Fixed | — | Auth errors burned reconnect budget |
| F | `config.py` | ✅ Documented | — | `max_daily_order_count` etc. not enforced — now Field-documented |
| G | `config.py` | ✅ Fixed | — | `Trade` scope not validated when trading flags enabled |
| H | `transport.py` | ✅ Fixed | — | `HttpxAsyncTransport` no context manager |
| I | `stream.py` | ✅ Fixed | — | `_looks_like_market_data` missed market depth keys |
| P3-1 | `validation.py` | 🔴 **Regression** | 🔴 High | `option_risk_reward_payload` now stringifies Decimals — API spec requires numbers |
| P3-2 | `models.py` | 🔴 **New** | 🔴 High | `asset_class` defaults to `UNKNOWN` — breaking change for non-builder `OrderRequest` usage |
| P3-3 | `client.py` | 🟡 **New** | 🟡 Medium | `replace_order` / `cancel_order` signatures changed — breaking API without version bump |
| P3-4 | `client.py` | 🟡 **New** | 🟡 Medium | `replace_order` has undocumented `ReadAccount` scope dependency |
| P3-5 | `rest.py` | 🟡 **New** | 🟡 Medium | `stream_events` hardcodes `raise_on_error=True` — non-fatal stream errors terminate the stream |
| P3-6 | `models.py` | 🟢 **New** | 🟢 Low | `BarUnit` / `BarSessionTemplate` enums exported but never wired into any interface |
| — | `client.py` | ⚠️ Open | 🟡 Medium | `_get_order_pages` has no max page guard — infinite loop on a misbehaving paginator |

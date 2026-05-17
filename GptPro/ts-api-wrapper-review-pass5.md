# Code Review Pass 5: `stridskoma2/ts-api-wrapper`

> Reviewed against commit `d2908f1 Tighten wrapper validation and stream handling`.
> This is the final pass. All 32 TS v3 API endpoints are now wrapped and covered by
> `test_spec_coverage.py`. The codebase is in very good shape — no 🔴 issues remain.

---

## What Got Fixed

Everything from pass 4 is resolved:

- **P4-1 `_TokenFileLock` stale lock** — fixed with proper cross-platform stale detection.
  POSIX uses `os.kill(pid, 0)`; Windows uses `OpenProcess` / `GetExitCodeProcess` via
  `ctypes`. Clean and correct on both platforms.
- **P4-2 `startdate` missing from `BarChartParams`** — added as `start_date`.
- **P4-3 mutual-exclusion validators** — `first_date`/`bars_back` and `last_date`/`start_date`
  conflicts now caught at construction time on `BarChartParams`.
- **P4-4 `BarChartParams` reused for streaming** — split into `BarChartParams` (REST, includes
  date fields) and `StreamBarChartParams` (stream, date fields omitted). `stream_bars` now
  accepts `StreamBarChartParams`.
- **P4-5 unbounded queue** — `STREAM_QUEUE_MAX_CHUNKS = 1024` with blocking
  `run_coroutine_threadsafe` gives real backpressure. The background thread stalls instead of
  accumulating memory when the consumer is slow.
- **P4-6 background thread silent EOF** — bare `except Exception` now catches unexpected
  errors and enqueues them as `TransportError` before the `finally`. No more silent EOF on
  out-of-scope exceptions.
- **`OptionChainStreamParams`** — `stream_option_chain` now has a fully typed and validated
  parameter model replacing the raw `Mapping[str, object | None]`.
- **`cancel_order`** — now requires `ReadAccount` scope and calls
  `_assert_order_belongs_to_account`, consistent with `replace_order`.
- **`AdvancedOptionsReplace` / `ActivationRulesReplace`** — replace-specific advanced option
  models added; `OrderReplaceRequest.advanced_options` now correctly typed as
  `AdvancedOptionsReplace`.
- **`_model_query_params`** — `_bar_query_params` refactored into a generic helper reused
  for both bar and option chain params.
- **Full spec coverage** — `test_spec_coverage.py` asserts all 32 TS v3 spec endpoints are
  explicitly wrapped or skipped. 103 tests passing.

---

## Remaining Issues

### P5-1. `TradeStationAPIError` in the stream reconnect bypass is too broad (`stream.py`) 🟡

```python
except (AuthenticationError, ConfigurationError, TradeStationAPIError):
    raise
```

The intent was to stop reconnecting on deterministic client errors (403 Forbidden, 404 Not
Found) where retrying won't help. But `TradeStationAPIError` is the base class for
`RateLimitError` (429) and any server error surfaced via `_stream_open_api_error`. Both of
those are transient and *should* reconnect:

- **429** on a stream open → `RateLimitError` (subclass of `TradeStationAPIError`) →
  immediately raises, no reconnect. TS does rate-limit stream connections; a
  backoff-and-retry is the correct response.
- **503** on a stream open → goes through `_stream_open_api_error` →
  `TradeStationAPIError` → immediately raises. This used to reconnect with exponential
  backoff, which is correct for a temporary server outage.

The right split is: raise immediately on 4xx (except 429); reconnect with backoff on 429
and 5xx:

```python
except (AuthenticationError, ConfigurationError):
    raise
except TradeStationAPIError as exc:
    # 4xx errors (other than rate limits) are deterministic — reconnecting won't fix them
    if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
        raise
    # 429 (rate limit) and 5xx (server error): fall through to reconnect with backoff
    if reconnects >= self._reconnect_policy.max_reconnects:
        raise
    reconnects += 1
    continue
```

---

### P5-2. `OptionChainStreamParams` string fields have no enum or value validation (`models.py`) 🟢

Three fields accept any arbitrary string with no check against the TS spec's documented
valid values:

| Field | TS valid values |
|---|---|
| `spread_type` | `Single`, `Vertical`, `Collar`, `Butterfly`, `Condor`, `Diagonal`, `Calendar`, … |
| `strike_range` | `ITM`, `OTM`, `All` |
| `option_type` | `All`, `Call`, `Put` |

A typo like `option_type="Calls"` silently sends an invalid value to the streaming
endpoint. The stream either returns no data or emits an error event — both hard to
debug. Small `str` enums or `Literal` annotations would catch these at construction time,
consistent with how `BarUnit`, `BarSessionTemplate`, `Duration`, and `TradeAction` are all
enums rather than raw strings.

```python
class OptionType(str, Enum):
    ALL  = "All"
    CALL = "Call"
    PUT  = "Put"

class StrikeRange(str, Enum):
    ALL = "All"
    ITM = "ITM"
    OTM = "OTM"
```

---

### P5-3. `riskFreeRate=0` is rejected, but zero is a valid economic value (`models.py`) 🟢

```python
@field_validator("risk_free_rate", "price_center")
@classmethod
def require_positive_decimal(cls, value: Decimal | None) -> Decimal | None:
    if value is not None and value <= 0:
        raise ValueError("option-chain decimal parameters must be positive")
    return value
```

`price_center <= 0` being invalid makes sense — you can't center an option chain on a
zero or negative strike. But `risk_free_rate = 0` is a legitimate economic scenario (zero
interest rate policy) and the TS spec just says `number` with no minimum constraint. The
shared validator is overly strict for `risk_free_rate`. Split into two validators:

```python
@field_validator("price_center")
@classmethod
def require_positive_price_center(cls, value: Decimal | None) -> Decimal | None:
    if value is not None and value <= 0:
        raise ValueError("price_center must be positive")
    return value

@field_validator("risk_free_rate")
@classmethod
def require_non_negative_risk_free_rate(cls, value: Decimal | None) -> Decimal | None:
    if value is not None and value < 0:
        raise ValueError("risk_free_rate cannot be negative")
    return value
```

---

### P5-4. `test_spec_coverage.py` hardcodes the spec filename (`tests/unit/test_spec_coverage.py`) 🟢

```python
SPEC_PATH = Path("specs/tradestation/openapi.2026-05-09.json")
```

When `tools/pin_tradestation_spec.py` is run to update the pinned spec, this path must be
manually updated too or the coverage test silently continues to validate against a stale
spec. Derive the path dynamically instead:

```python
SPEC_DIR = Path("specs/tradestation")
SPEC_PATH = max(SPEC_DIR.glob("openapi.*.json"))  # always picks the latest by filename sort
```

---

### P5-5. `OrderReplaceRequest.advanced_options` type change is undocumented under v0.2.0 (`models.py`) 🟢

```python
# Before:
advanced_options: AdvancedOptions | None = Field(...)

# After:
advanced_options: AdvancedOptionsReplace | None = Field(...)
```

Any code building `OrderReplaceRequest(AdvancedOptions=some_advanced_options_instance)`
now gets a Pydantic validation error at runtime because `AdvancedOptions` and
`AdvancedOptionsReplace` are different, incompatible types. The type change is correct and
necessary, but it is a breaking change that is not mentioned in the v0.2.0 changelog
alongside the `replace_order` / `cancel_order` signature changes. Add a note there.

---

## Complete Issue Index (All Five Passes)

| ID | File | Status | Severity | Issue |
|----|------|--------|----------|-------|
| 1 | `stream.py` | ✅ Fixed | — | `or`/`and` precedence in `classify_stream_message` |
| 2 | `auth.py` | ✅ Fixed | — | `authorize_with_loopback` handled only one HTTP request |
| 3 | `stream.py` | ✅ Fixed | — | `reconnects` counter never reset — lifetime budget not rolling |
| 4 | `rest.py` | ✅ Fixed | — | Token-expiry reconnects burned reconnect budget |
| 5 | `auth.py` | ✅ Fixed | — | `FileTokenStore` CAS not multi-process safe |
| 6 | `validation.py` | ✅ Fixed | — | `_numeric_decimals` float precision for risk/reward payload |
| 7 | `client.py` | ✅ Fixed | — | `get_bars` / `stream_bars` untyped `dict` params |
| 8 | `client.py` | ✅ Fixed | — | `fetch_state_snapshot` 4 sequential awaits |
| 9 | `builders.py` | ✅ Fixed | — | `market_order` `estimated_price` looked optional |
| 10 | `validation.py` | ✅ Fixed | — | Single-symbol/account group constraint not enforced on model |
| 11 | `config.py` | ✅ Fixed | — | `requested_scopes` non-deterministic ordering |
| 12 | `transport.py` | ✅ Fixed | — | `UrllibAsyncTransport` per-chunk thread-pool overhead |
| A | `builders.py` | ✅ Fixed | — | `bracket_order_group` hardcoded `Duration.DAY` for entry leg |
| B | `models.py` | ✅ Fixed | — | `GTD`/`GTD_PLUS` accepted `None` expiration |
| C | `rest.py` | ✅ Fixed | — | `JSONDecodeError` on 200 escaped as raw exception |
| D | `trade.py` | ✅ Fixed | — | `reconcile_required` was `False` for partial group failures |
| E | `stream.py` | ✅ Fixed | — | Auth errors in reconnect loop burned reconnect budget |
| F | `config.py` | ✅ Documented | — | `max_daily_order_count` etc. not enforced — documented as intentional |
| G | `config.py` | ✅ Fixed | — | `Trade` scope not validated when trading flags enabled |
| H | `transport.py` | ✅ Fixed | — | `HttpxAsyncTransport` had no context manager |
| I | `stream.py` | ✅ Fixed | — | `_looks_like_market_data` missed market depth keys |
| P3-1 | `validation.py` | ✅ Fixed | — | `option_risk_reward_payload` regression to string serialisation |
| P3-2 | `models.py` | ✅ Fixed | — | `asset_class` defaulted to `UNKNOWN` — broke non-builder usage |
| P3-3 | `client.py` | ✅ Versioned | — | `replace_order`/`cancel_order` signature breaking changes |
| P3-4 | `client.py` | ✅ Fixed | — | `replace_order` undocumented `ReadAccount` scope dependency |
| P3-5 | `rest.py` | ✅ Fixed | — | `stream_events` hardcoded `raise_on_error=True` with no escape hatch |
| P3-6 | `models.py` | ✅ Fixed | — | `BarUnit`/`BarSessionTemplate` enums orphaned |
| — | `client.py` | ✅ Fixed | — | `_get_order_pages` had no max page guard |
| P4-1 | `auth.py` | ✅ Fixed | — | `_TokenFileLock` no stale lock detection |
| P4-2 | `models.py` | ✅ Fixed | — | `BarChartParams` missing `startdate` parameter |
| P4-3 | `models.py` | ✅ Fixed | — | `BarChartParams` no mutual-exclusion validators |
| P4-4 | `client.py` / `models.py` | ✅ Fixed | — | `BarChartParams` shared between REST and stream endpoints |
| P4-5 | `transport.py` | ✅ Fixed | — | `UrllibAsyncTransport` queue unbounded |
| P4-6 | `transport.py` | ✅ Fixed | — | Background thread unhandled exceptions appeared as clean EOF |
| **P5-1** | `stream.py` | ⚠️ Open | 🟡 Medium | `TradeStationAPIError` in stream bypass too broad — 429 and 5xx should reconnect |
| **P5-2** | `models.py` | ⚠️ Open | 🟢 Low | `OptionChainStreamParams` `spread_type`/`strike_range`/`option_type` are unvalidated strings |
| **P5-3** | `models.py` | ⚠️ Open | 🟢 Low | `riskFreeRate=0` rejected by validator — zero is a valid economic value |
| **P5-4** | `tests/` | ⚠️ Open | 🟢 Low | `test_spec_coverage.py` hardcodes spec filename — breaks silently on spec update |
| **P5-5** | `models.py` | ⚠️ Open | 🟢 Low | `OrderReplaceRequest.advanced_options` type change undocumented in v0.2.0 changelog |

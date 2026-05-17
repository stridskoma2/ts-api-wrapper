# Code Review Pass 4: `stridskoma2/ts-api-wrapper`

> Reviewed against commit `b191d70 Add v3 order, stream, and auth safety fixes`.
> Version bumped to `0.2.0`. This pass covers what the new commit fixed, what new issues it introduced, and what remains open.

---

## What Got Fixed

This commit closed out almost the entire backlog from passes 1–3. Confirmed resolved:

- **`option_risk_reward_payload` regression** — reverted to `_numeric_decimals` with `float(str(value))` to avoid binary float noise. Test assertion corrected back to a float. Correct.
- **`asset_class` default restored to `EQUITY`** — non-builder `OrderRequest` usage works again without hitting the `UNKNOWN` guard.
- **Version bumped to `0.2.0`** — breaking API changes from pass 3 acknowledged.
- **`replace_order` undocumented `ReadAccount` scope** — `_require_scope(READ_ACCOUNT_SCOPE)` now added at the top of the method alongside `TRADE_SCOPE`. Error is immediate and clear.
- **`raise_on_error` exposed on all stream methods** — every `stream_*` method on `TradeStationClient` now accepts and passes through `raise_on_error`.
- **`BarChartParams` model wired in** — `get_bars` and `stream_bars` now accept `BarChartParams` instead of `dict[str, Any]`. Typed, validated, correct lowercase aliases matching the TS spec.
- **`fetch_state_snapshot` sequential awaits** — now uses `asyncio.gather`. All four calls (accounts, balances, positions, orders) fire concurrently.
- **`GroupOrderRequest` single-symbol/single-account at model level** — enforced in the model validator, not just in `validate_group_for_config`. The validation-layer checks are now redundant (harmless, but could be cleaned up).
- **`UrllibAsyncTransport` per-chunk thread overhead** — replaced with a single background reader thread pushing chunks to an `asyncio.Queue`. One thread for the lifetime of the stream.
- **`FileTokenStore` multi-process safety** — `_TokenFileLock` uses `O_CREAT | O_EXCL` (POSIX-atomic) to wrap `compare_and_swap_refresh_token` and `save`. PID written to lock file.
- **`_get_order_pages` infinite loop** — `MAX_ORDER_PAGES = 1000` guard added, raises `PaginationError` on breach.

93 tests, all passing.

---

## New Issues

### P4-1. `_TokenFileLock` has no stale lock detection — 10-second deadlock if the holder crashes (`auth.py`) 🟡

The lock file has the holder's PID written into it:

```python
os.write(self._file_descriptor, str(os.getpid()).encode("ascii"))
```

But that PID is never read back or validated. If a process is hard-killed (SIGKILL, OOM, power loss) while holding the lock, the `.lock` file stays on disk. Every subsequent process polls every 50ms for the full 10-second timeout before raising `ConfigurationError("timed out waiting for token-store lock")`. In a production ES/MES trading setup where processes can be forcibly killed, this creates a guaranteed 10-second blackout on token refresh after any unclean exit.

**Fix:** read the PID from the lock file and check whether that process is still alive using `os.kill(pid, 0)`:

```python
except FileExistsError:
    if time.monotonic() >= deadline:
        raise ConfigurationError("timed out waiting for token-store lock") from exc
    try:
        pid_text = self._path.read_text(encoding="ascii").strip()
        pid = int(pid_text)
        os.kill(pid, 0)   # raises OSError if process no longer exists
    except (ValueError, OSError):
        # stale lock — process is gone, steal it
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        continue   # retry O_CREAT|O_EXCL immediately
    time.sleep(0.05)
```

---

### P4-2. `BarChartParams` is missing the `startdate` parameter (`models.py`) 🟡

The TS spec for `GET /v3/marketdata/barcharts/{symbol}` documents three date-range parameters:

| Parameter | Modelled |
|-----------|----------|
| `firstdate` | ✅ `first_date` |
| `lastdate` | ✅ `last_date` |
| `startdate` | ❌ missing |

`startdate` and `lastdate` are mutually exclusive alternatives — `lastdate` anchors the right edge while `barsback` counts backward; `startdate` anchors the left edge directly (complementary to `lastdate`). Users who need `startdate` currently have no way to pass it through `BarChartParams` other than falling back to a raw dict, which the typed interface no longer accepts.

**Fix:** add the field:

```python
start_date: date | datetime | None = Field(default=None, alias="startdate")
```

And add the mutual-exclusion validator (see P4-3).

---

### P4-3. `BarChartParams` has no mutual-exclusion validators (`models.py`) 🟡

The TS spec documents two pairs of mutually exclusive parameters:

- `firstdate` and `barsback` — cannot both be set
- `lastdate` and `startdate` — cannot both be set

`BarChartParams` silently accepts both sides of either pair with no error:

```python
params = BarChartParams(
    unit=BarUnit.DAILY,
    first_date=date(2026, 1, 1),
    bars_back=200,            # mutually exclusive with first_date — no error raised
)
# _bar_query_params sends: {'barsback': 200, 'firstdate': '2026-01-01', 'unit': 'Daily'}
# TS API silently picks one and ignores the other
```

**Fix:**

```python
@model_validator(mode="after")
def check_mutual_exclusions(self) -> "BarChartParams":
    if self.first_date is not None and self.bars_back is not None:
        raise ValueError(
            "first_date and bars_back are mutually exclusive — use one or the other"
        )
    if self.last_date is not None and self.start_date is not None:
        raise ValueError(
            "last_date and start_date are mutually exclusive — use one or the other"
        )
    return self
```

---

### P4-4. `BarChartParams` is shared between REST and stream endpoints — stream silently ignores date params (`client.py` / `models.py`) 🟢

`get_bars` (REST: `/v3/marketdata/barcharts/{symbol}`) and `stream_bars` (STREAM: `/v3/marketdata/stream/barcharts/{symbol}`) both accept `BarChartParams`. The two endpoints have different parameter support:

| Parameter | REST `get_bars` | STREAM `stream_bars` |
|-----------|:-:|:-:|
| `interval` | ✅ | ✅ |
| `unit` | ✅ | ✅ |
| `barsback` | ✅ | ✅ |
| `sessiontemplate` | ✅ | ✅ |
| `firstdate` | ✅ | ❌ silently ignored |
| `lastdate` | ✅ | ❌ silently ignored |
| `startdate` | ✅ | ❌ silently ignored |

A user passing `BarChartParams(first_date=date(2026, 1, 1))` to `stream_bars` gets bars from the API's default start point with no error or warning. The date param is sent in the query string and silently dropped by the streaming endpoint.

**Options:**
1. Split into `BarChartParams` (REST, full parameter set) and `StreamBarParams` (stream-only, no date fields).
2. Keep one model but document in `stream_bars` that date params are not supported by the streaming endpoint.

Option 1 is cleaner for a typed interface.

---

### P4-5. `UrllibAsyncTransport` reader queue is unbounded — memory risk with slow consumers (`transport.py`) 🟢

```python
queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
```

No `maxsize`. The background reader thread pushes 8192-byte chunks as fast as the TCP socket delivers them via `loop.call_soon_threadsafe(queue.put_nowait, item)`. If the consuming `async for` loop is slower than the stream (ES quote stream at market open, for example), the queue grows without bound.

Since `put_nowait` is used (not a blocking `put`), adding backpressure requires a design change — the background thread would need to block, which requires `run_coroutine_threadsafe` rather than `call_soon_threadsafe`. Alternatively, cap the queue and document the intended behaviour. Worth noting explicitly so a high-frequency streaming use case doesn't silently consume unbounded memory.

---

### P4-6. Background thread exceptions outside the caught set silently appear as clean EOF (`transport.py`) 🟢

`read_stream()` catches `TimeoutError`, `socket.timeout`, `HTTPStreamOpenError`, and `OSError`. Any other exception — including `MemoryError`, an unexpected `RuntimeError`, or a bug in the stream-open logic — falls through to the `finally` block, which calls `enqueue(None)`:

```python
def read_stream() -> None:
    try:
        ...
    except (TimeoutError, socket.timeout) as exc:
        enqueue(NetworkTimeout(str(exc)))
    except HTTPStreamOpenError as exc:
        enqueue(exc)
    except OSError as exc:
        enqueue(TransportError(str(exc)))
    # ← anything else falls through
    finally:
        ...
        enqueue(None)   # looks like a clean end-of-stream to the consumer
```

The async consumer sees a normal EOF and exits its `async for` loop silently. The original exception is lost.

**Fix:** add a catch-all before the `finally`:

```python
except Exception as exc:
    enqueue(TransportError(f"unexpected stream reader error: {exc}"))
```

---

## Complete Issue Index (All Four Passes)

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
| F | `config.py` | ✅ Documented | — | `max_daily_order_count` etc. not enforced — Field-documented as intentional |
| G | `config.py` | ✅ Fixed | — | `Trade` scope not validated when trading flags enabled |
| H | `transport.py` | ✅ Fixed | — | `HttpxAsyncTransport` had no context manager |
| I | `stream.py` | ✅ Fixed | — | `_looks_like_market_data` missed market depth keys |
| P3-1 | `validation.py` | ✅ Fixed | — | `option_risk_reward_payload` regression to string serialisation |
| P3-2 | `models.py` | ✅ Fixed | — | `asset_class` defaulted to `UNKNOWN` — broke non-builder usage |
| P3-3 | `client.py` | ✅ Versioned | — | `replace_order`/`cancel_order` signature breaking changes — now under v0.2.0 |
| P3-4 | `client.py` | ✅ Fixed | — | `replace_order` undocumented `ReadAccount` scope dependency |
| P3-5 | `rest.py` | ✅ Fixed | — | `stream_events` hardcoded `raise_on_error=True` with no escape hatch |
| P3-6 | `models.py` | ✅ Fixed | — | `BarUnit`/`BarSessionTemplate` enums orphaned |
| — | `client.py` | ✅ Fixed | — | `_get_order_pages` had no max page guard |
| **P4-1** | `auth.py` | ⚠️ **Open** | 🟡 Medium | `_TokenFileLock` no stale lock detection — 10s deadlock if holder process crashes |
| **P4-2** | `models.py` | ⚠️ **Open** | 🟡 Medium | `BarChartParams` missing `startdate` parameter from TS spec |
| **P4-3** | `models.py` | ⚠️ **Open** | 🟡 Medium | `BarChartParams` no mutual-exclusion validators for `firstdate`/`barsback` and `lastdate`/`startdate` |
| **P4-4** | `client.py` / `models.py` | ⚠️ **Open** | 🟢 Low | `BarChartParams` shared between REST and stream — stream silently ignores date params |
| **P4-5** | `transport.py` | ⚠️ **Open** | 🟢 Low | `UrllibAsyncTransport` queue unbounded — memory risk with slow consumers on high-frequency streams |
| **P4-6** | `transport.py` | ⚠️ **Open** | 🟢 Low | Background thread unhandled exceptions silently appear as clean EOF to the consumer |

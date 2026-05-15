# Code Review: `stridskoma2/ts-api-wrapper`

> Python wrapper for the TradeStation API v3 (`https://api.tradestation.com/docs/`)

---

## Overall

This is genuinely well-crafted code. The architecture is clean, the error hierarchy is thoughtful, the config safety guards are solid, and the test coverage is good for the surface area. The things flagged below are real issues, not nitpicks for the sake of it.

---

## Bugs / Correctness Issues

### 1. `classify_stream_message` — operator precedence landmine (`stream.py`)

```python
if "Error" in payload or "Message" in payload and not _looks_like_market_data(payload):
```

`and` binds tighter than `or`, so this actually evaluates as:

```python
if "Error" in payload or ("Message" in payload and not _looks_like_market_data(payload)):
```

That's *probably* intentional (an `"Error"` key always means error, regardless of market data appearance), but it means any market data tick that happens to contain an `"Error"` key would be misclassified. Add explicit parentheses. Right now it's a reader trap and a silent future bug waiting to happen.

---

### 2. `authorize_with_loopback` handles exactly one HTTP request (`auth.py`)

```python
await asyncio.to_thread(server.handle_request)
```

`handle_request()` processes **one** request and returns. Some browsers (especially Chrome) fire a favicon request or a preconnect to `127.0.0.1:31022` before or alongside the OAuth callback. That preflight consumes the single `handle_request()` call, `callback.authorization_code` stays `None`, and you get `OAuthCallbackTimeout` after 5 minutes.

**Fix:** Loop `handle_request` until `callback.authorization_code` is set or the timeout elapses.

---

### 3. `reconnects` counter never resets (`stream.py`)

```python
reconnects = 0
while True:
    ...
    except Exception:
        if reconnects >= self._reconnect_policy.max_reconnects:
            raise
        reconnects += 1
        continue
```

With the default `max_reconnects=3`, the stream gets a **lifetime budget** of 3 reconnects total. Three early network blips exhaust the budget, and any subsequent error propagates instead of reconnecting. For a long-running ES futures quote stream (hours), this is a reliability problem.

**Fix:** Either reset `reconnects` on successful data delivery, or make the budget a rolling window.

---

### 4. Stream token not explicitly refreshed on reconnect (`rest.py`)

`_stream_chunks` fetches the token once at stream open. When the stream reconnects (e.g., GoAway or network error), `_stream_chunks` is called again and `get_access_token()` will re-check expiry — that part is fine. The problem compounds with issue #3: a long-lived stream will reconnect when the token expires (~20 min), burning one reconnect slot each time. On a stream running 60+ minutes, token-expiry reconnects + any actual network events = reconnect budget exhausted = dead stream.

---

### 5. `FileTokenStore.compare_and_swap_refresh_token` is not multi-process safe (`auth.py`)

```python
current = self.load()                     # read
if current_refresh_token != expected:     # check
    return False
self.save(replacement)                    # write
```

The `asyncio.Lock` in `OAuthManager.refresh_access_token` protects within a single process, but not across multiple. Two processes sharing the same token file can both pass the check and both save their own replacement, causing one to hold a consumed refresh token and loop into 401s. For a setup running multiple instances against the same account, this will eventually bite.

---

### 6. `_numeric_decimals` converts `Decimal` to `float` — precision loss (`validation.py`)

```python
def _numeric_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)   # ← loses precision
```

This is used for the option risk/reward payload. `Decimal("0.24")` becomes IEEE 754 `0.23999999999999999...`. For financial data passed to an API, that's a problem. Use `str(value)` or `format(value, "f")` like the order payload functions do, unless the TS endpoint specifically requires a numeric JSON type — in which case at least document the precision loss.

---

## Design / Usability Issues

### 7. `get_bars` / `stream_bars` accept an untyped `dict` (`client.py`)

```python
async def get_bars(self, symbol: str, *, params: dict[str, Any] | None = None) -> tuple[BarSnapshot, ...]:
```

This is the most-used endpoint for an algo trader and it's the only one in the whole client without typed parameters. The TradeStation bar API has a meaningful set of params: `Interval`, `Unit` (Minute, Daily, Tick, Volume, etc.), `BarsBack`, `FirstDate`, `LastDate`, `SessionTemplate`. All unvalidated, all untyped, all pass-through. `stream_bars` and `stream_option_chain` have the same problem. This stands out against an otherwise very consistent typed interface.

---

### 8. `fetch_state_snapshot` does 4 sequential awaits that could be concurrent (`client.py`)

```python
accounts = await self.get_accounts()
balances = await self.get_balances(account_ids)
positions = await self.get_positions(account_ids)
orders = await self.get_orders(account_ids)
```

These are independent requests. `asyncio.gather()` would cut the latency to roughly that of the slowest single call. If this is called before placing an order to sanity-check state, that sequential latency adds up — especially if `get_orders` triggers pagination.

---

### 9. `market_order()` builder makes `estimated_price` look optional but it's effectively required (`builders.py` / `validation.py`)

```python
def market_order(..., estimated_price: Decimal | None = None) -> OrderRequest:
```

But in validation:

```python
if order.order_type is OrderType.MARKET and order.estimated_price is None:
    raise RequestValidationError("market orders require estimated_price for risk validation")
```

So `market_order(account_id=..., symbol=..., quantity=..., action=...)` builds fine but explodes on `validate_order_for_config`. The builder signature implies optionality that doesn't exist. Either make it required in the builder, or validate it inside the builder itself.

---

### 10. `validate_group_for_config` enforces single-symbol at validation, not model level (`validation.py`)

```python
symbols = {order.symbol for order in group.orders}
if len(symbols) != 1:
    raise RequestValidationError("protective order groups must use one symbol")
```

`GroupOrderRequest` itself doesn't enforce this. So `group_order_payload()` called directly (bypassing `client.place_order_group`) will happily serialize a multi-symbol group that TS would reject. Either add a `model_validator` to `GroupOrderRequest` or document that the model is not self-validating on this constraint.

---

### 11. `requested_scopes` validator uses a set, so tuple order is non-deterministic (`config.py`)

```python
scopes = {scope.strip() for scope in value if scope.strip()}
return tuple(scopes)
```

Sets are unordered. Two `TradeStationConfig` instances built with identical scope lists in different orders will produce different `requested_scopes` tuples. Since the config is frozen and equality compares field values, two "identical" configs won't compare equal if scope order differs.

**Fix:** Use `tuple(sorted(scopes))`.

---

### 12. `UrllibAsyncTransport.stream()` dispatches one thread-pool task per 8192-byte chunk (`transport.py`)

```python
while True:
    chunk = await asyncio.to_thread(stream.read, 8192)
```

Each `asyncio.to_thread` call schedules a separate thread pool dispatch. For a high-frequency quotes stream (ES tick-by-tick), this is significant overhead. `HttpxAsyncTransport` doesn't have this problem. Worth noting in docs so users running serious streaming workloads know to use `HttpxAsyncTransport` instead of the urllib default.

---

## Minor Notes

- **`max_order_notional` default of `Decimal("1000")` (`config.py`)** — this will silently block all futures orders even after setting `allow_futures=True`, because one ES contract is ~$250k notional. The notional check also runs before the futures-permission check, so the error message isn't even futures-specific. Consider raising the default or documenting the interaction explicitly.

- **`STATUS_MESSAGE = "STT"` is uncategorized (`order_status.py`)** — this status code is not in `DONE_STATUSES`, `WORKING_STATUSES`, or `ACTIVE_STATUSES`. So `is_done`, `is_active`, and `is_working` all return `False` for it, even if the order is live. It's the only status code that falls through every bucket. Worth explicitly placing it somewhere or at minimum calling it out in the status map comments.

- **Timezone safety in reconciliation (`reconciliation.py`)** — `UnknownOrderFingerprint.submitted_at` is a plain `datetime` with no timezone enforcement. If a user passes a naive datetime and TS returns a timezone-aware `opened_at`, `abs(snapshot.opened_at - fingerprint.submitted_at)` will raise `TypeError`. Low probability but a `field_validator` enforcing timezone-awareness would prevent a confusing runtime crash.

---

## Summary Table

| # | File | Severity | Issue |
|---|------|----------|-------|
| 1 | `stream.py` | 🟡 Medium | `or`/`and` precedence — missing parentheses in `classify_stream_message` |
| 2 | `auth.py` | 🔴 High | `authorize_with_loopback` handles only one HTTP request — browser preflight breaks OAuth |
| 3 | `stream.py` | 🔴 High | `reconnects` counter never resets — stream dies permanently after 3 lifetime errors |
| 4 | `rest.py` | 🟡 Medium | Token-expiry reconnects burn the same reconnect budget as network errors |
| 5 | `auth.py` | 🟡 Medium | `FileTokenStore` CAS is not multi-process safe |
| 6 | `validation.py` | 🔴 High | `_numeric_decimals` silently converts `Decimal` to `float`, losing precision |
| 7 | `client.py` | 🟡 Medium | `get_bars` / `stream_bars` take untyped `dict` — inconsistent with rest of API |
| 8 | `client.py` | 🟢 Low | `fetch_state_snapshot` makes 4 sequential requests instead of using `asyncio.gather` |
| 9 | `builders.py` | 🟡 Medium | `market_order()` makes `estimated_price` look optional but validation rejects `None` |
| 10 | `validation.py` | 🟢 Low | Single-symbol group constraint lives in validation layer, not model |
| 11 | `config.py` | 🟢 Low | `requested_scopes` set ordering is non-deterministic |
| 12 | `transport.py` | 🟡 Medium | `UrllibAsyncTransport` thread-per-chunk overhead in streaming |

---

## Second Pass — Additional Issues

### A. `bracket_order_group` hardcodes `Duration.DAY` for the entry leg regardless of `duration` param (`builders.py`)

```python
parent = limit_order(..., duration=Duration.DAY, ...)   # hardcoded — ignores caller
target = limit_order(..., duration=duration, ...)        # uses caller's param
stop   = stop_market_order(..., duration=duration, ...)  # uses caller's param
```

If you pass `duration=Duration.GTC`, the entry leg still expires at day end while the target and stop legs survive as GTC. You end up with orphaned exit orders with no open position to protect. The `duration` parameter is silently ignored for the leg that matters most. Either remove the `duration` parameter entirely and add an explicit `entry_duration` / `exit_duration` pair, or at minimum document that `duration` only applies to exit legs.

---

### B. `GTD` / `GTD_PLUS` duration accepts `None` expiration — rejected silently at runtime (`models.py`)

```python
TimeInForce(Duration=Duration.GTD)  # builds fine, expiration=None
```

`TimeInForce` has no `model_validator` enforcing that GTD-family durations supply `Expiration`. The model validates fine, but the TS API will reject the order at submission with a vague error. A validator on `TimeInForce` would surface this at construction time instead:

```python
@model_validator(mode="after")
def require_expiration_for_gtd(self) -> "TimeInForce":
    if self.duration in (Duration.GTD, Duration.GTD_PLUS) and self.expiration is None:
        raise ValueError("GTD and GTD_PLUS durations require an Expiration datetime")
    return self
```

---

### C. `JSONDecodeError` on a 200 response escapes as a raw exception (`rest.py`)

```python
if _is_success(response):
    decoded = response.json()   # json.JSONDecodeError not caught here
```

`_response_payload()` wraps `json.loads` in `except ValueError`, but `request_json` calls `response.json()` directly on success paths without that protection. A 200 response with a malformed body (e.g., partial write during a gateway restart) raises a raw `json.JSONDecodeError` to the caller instead of a `TradeStationAPIError`. Callers only need to catch one exception class hierarchy for API failures; this breaks that contract.

**Fix:** wrap the `response.json()` call in a `try/except (ValueError, json.JSONDecodeError)` and raise `TradeStationAPIError` on decode failure.

---

### D. `TradeStationTrade.reconcile_required` is `False` for partial group failures (`trade.py`)

When a group order partially succeeds — some legs acknowledged, some in `ack.errors` — `reconcile_required` is `False` and `is_ambiguous` is `False`. The caller sees a clean-looking trade object, but an open position may be one-sided (entry filled, exit not placed). This is especially dangerous for ES/MES bracket orders where an unfilled stop leg leaves a live position unprotected.

**Fix:**

```python
@property
def reconcile_required(self) -> bool:
    if self.is_ambiguous:
        return True
    if self.ack is None:
        return False
    if self.order_id is None:
        return True
    return bool(self.ack.errors)   # ← add this
```

---

### E. Auth errors in the stream reconnect loop burn the reconnect budget (`stream.py`)

`events()` catches a bare `except Exception` on stream failure. If `get_access_token()` raises `AuthenticationError` inside `_stream_chunks`, that error is caught, `reconnects` increments, and the stream retries — only to fail the same way up to `max_reconnects` times before finally re-raising. On a broken or expired token you waste 3 × reconnect delay before the real error surfaces.

`AuthenticationError` and `ConfigurationError` should bypass the reconnect logic entirely:

```python
except (AuthenticationError, ConfigurationError):
    raise
except Exception:
    if reconnects >= self._reconnect_policy.max_reconnects:
        raise
    reconnects += 1
    continue
```

---

### F. `max_daily_order_count`, `max_daily_loss`, `max_symbol_position_notional` are defined but never enforced (`config.py` / `validation.py`)

`max_order_notional` is checked in `validate_order_for_config`. The other three risk config fields are not checked anywhere in the codebase:

```python
max_symbol_position_notional: Decimal = Decimal("5000")   # never read
max_daily_loss: Decimal = Decimal("500")                   # never read
max_daily_order_count: int = 20                            # never read
```

A developer adding these to their config believing they're protected is not. Either enforce them (which requires stateful order tracking the wrapper doesn't currently hold) or remove them from config and document that they're the caller's responsibility. As-is they create a false sense of safety.

---

### G. `'Trade'` scope not validated when trading flags are enabled (`config.py`)

```python
config = TradeStationConfig(
    allow_futures=True,
    requested_scopes=("openid", "offline_access", "MarketData", "ReadAccount"),  # no "Trade"
    ...
)
# No ConfigurationError — first order write gets a silent 403
```

The model validator checks that `live_trading_enabled` and `live_acknowledgement` are set for LIVE, but never checks that `"Trade"` is in `requested_scopes` when `allow_market_orders`, `allow_options`, or `allow_futures` are `True`. This should be caught at config construction time, not at the first order rejection.

**Fix:** add to `validate_environment_contract`:

```python
trading_enabled = self.allow_market_orders or self.allow_options or self.allow_futures
if trading_enabled and "Trade" not in set(self.requested_scopes):
    raise ValueError("allow_* trading flags require 'Trade' in requested_scopes")
```

---

### H. `HttpxAsyncTransport` has no context manager — client leaks if `aclose()` is forgotten (`transport.py`)

The transport has `aclose()` but no `__aenter__` / `__aexit__`. Users who instantiate it inline without an explicit teardown leak the underlying `httpx.AsyncClient` and its connection pool. This is especially likely in script-style or notebook usage. Adding support is a one-liner pair:

```python
async def __aenter__(self) -> "HttpxAsyncTransport":
    return self

async def __aexit__(self, *_: object) -> None:
    await self.aclose()
```

---

### I. `_looks_like_market_data` doesn't cover market depth payload keys (`stream.py`)

The heuristic checks for `Symbol`, `Bid`, `Ask`, `Last`, `Close`, `TimeStamp`. Market depth stream payloads use distinct keys — `Side`, `Price`, `Size`, `Entries` — none of which appear in that list. A depth payload that happens to contain a `Message` key (a server annotation on a tick) would be classified as `StreamEventKind.ERROR` instead of `DATA`, silently dropping real data.

**Fix:** add depth-specific keys to the check:

```python
def _looks_like_market_data(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in (
        "Symbol", "Bid", "Ask", "Last", "Close", "TimeStamp",
        "Side", "Entries",   # ← market depth keys
    ))
```

---

## Complete Summary Table

| # | File | Severity | Issue |
|---|------|----------|-------|
| 1 | `stream.py` | 🟡 Medium | `or`/`and` precedence — missing parentheses in `classify_stream_message` |
| 2 | `auth.py` | 🔴 High | `authorize_with_loopback` handles only one HTTP request — browser preflight breaks OAuth |
| 3 | `stream.py` | 🔴 High | `reconnects` counter never resets — stream dies permanently after 3 lifetime errors |
| 4 | `rest.py` | 🟡 Medium | Token-expiry reconnects burn the same reconnect budget as network errors |
| 5 | `auth.py` | 🟡 Medium | `FileTokenStore` CAS is not multi-process safe |
| 6 | `validation.py` | 🔴 High | `_numeric_decimals` silently converts `Decimal` to `float`, losing precision |
| 7 | `client.py` | 🟡 Medium | `get_bars` / `stream_bars` take untyped `dict` — inconsistent with rest of API |
| 8 | `client.py` | 🟢 Low | `fetch_state_snapshot` makes 4 sequential requests instead of `asyncio.gather` |
| 9 | `builders.py` / `validation.py` | 🟡 Medium | `market_order()` makes `estimated_price` look optional but validation rejects `None` |
| 10 | `validation.py` | 🟢 Low | Single-symbol group constraint lives in validation layer, not model |
| 11 | `config.py` | 🟢 Low | `requested_scopes` set ordering is non-deterministic |
| 12 | `transport.py` | 🟡 Medium | `UrllibAsyncTransport` thread-per-chunk overhead in streaming |
| A | `builders.py` | 🔴 High | `bracket_order_group` hardcodes parent entry as `Duration.DAY` — exit legs outlive expired entry |
| B | `models.py` | 🟡 Medium | `GTD`/`GTD_PLUS` duration allows `None` expiration — silent runtime rejection |
| C | `rest.py` | 🟡 Medium | `JSONDecodeError` on 200 response escapes as raw exception, not `TradeStationAPIError` |
| D | `trade.py` | 🔴 High | `reconcile_required` is `False` for partial group fills — errors in `ack.errors` invisible |
| E | `stream.py` | 🟡 Medium | Auth errors in reconnect loop burn reconnect budget before re-raising |
| F | `config.py` / `validation.py` | 🟡 Medium | `max_daily_order_count`, `max_daily_loss`, `max_symbol_position_notional` defined but never enforced |
| G | `config.py` | 🟡 Medium | `'Trade'` scope not validated when `allow_*` trading flags are set |
| H | `transport.py` | 🟢 Low | `HttpxAsyncTransport` has no context manager — client leaks if `aclose()` forgotten |
| I | `stream.py` | 🟡 Medium | `_looks_like_market_data` misses market depth keys — depth payloads with `Message` misclassified as `ERROR` |

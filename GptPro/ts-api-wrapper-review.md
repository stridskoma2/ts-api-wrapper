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

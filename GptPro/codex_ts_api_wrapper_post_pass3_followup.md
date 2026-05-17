# Codex Follow-up Brief: Latest Review of `stridskoma2/ts-api-wrapper`

Repository: `https://github.com/stridskoma2/ts-api-wrapper`  
Reviewed target: latest `main` inspected through GitHub source review  
Input considered: uploaded pass-3 review plus current repository files

Do **not** place live orders while working on this. Keep SIM order-placement integration tests opt-in only.

## Executive summary

The latest repo state is much better than the earlier passes. Most of the previous high-risk write-path and stream-path issues have been addressed:

- Write-side transient HTTP responses now become `AmbiguousOrderState`.
- Successful-response malformed JSON is wrapped as `TradeStationAPIError`.
- `replace_order()` now requires account context and performs account-scoped preflight.
- `cancel_order()` now requires account context and validates the allowlist.
- `asset_class=UNKNOWN` is now intentional and rejected before write validation.
- Builders now pass explicit `asset_class`.
- `allow_extended_hours` is enforced.
- `GTD` / `GTD_PLUS` require `Expiration`.
- `TradeStationTrade.reconcile_required` now accounts for `ack.errors`.
- Stream parsing now uses incremental UTF-8 decoding.
- Stream-open 401 can refresh once.
- `Retry-After` is no longer capped to five seconds.
- OAuth loopback handles multiple HTTP requests.
- Client and `HttpxAsyncTransport` support async close/context-manager usage.
- `fetch_state_snapshot()` is concurrent.
- README now has migration and safety sections.

Remaining work is mostly edge-case safety, documentation, typed parameter polish, and transport/token-store reliability.

---

## Verification commands

Run these after any patch:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Items from pass-3 review that are now addressed

### 1. `option_risk_reward_payload` JSON number regression

Resolved. The current implementation uses numeric conversion again:

```python
def option_risk_reward_payload(request: OptionRiskRewardRequest) -> dict[str, Any]:
    return _numeric_decimals(...)
```

`_numeric_decimals()` now converts integral decimals to `int` and non-integral decimals using `float(str(value))`, avoiding the earlier direct `float(Decimal(...))` path.

### 2. `OrderRequest.asset_class` defaulting to `UNKNOWN`

The behavior is now intentional and better wired:

- `OrderRequest.asset_class` defaults to `AssetClass.UNKNOWN`.
- `validate_order_for_config()` rejects `UNKNOWN`.
- Builders pass explicit `asset_class`, defaulting to `AssetClass.EQUITY`.
- README now mentions explicit unknown asset-class rejection.

One small README clarification remains below.

### 3. `replace_order()` / `cancel_order()` breaking signatures

Mostly addressed:

- Package version is now `0.2.0`.
- README includes migration notes.
- `replace_order(account_id, order_id, replacement)` performs account-scoped preflight.
- `cancel_order(account_id, order_id)` validates account allowlist.

One caveat remains: `cancel_order()` does not prove the order belongs to the supplied account.

### 4. `replace_order()` hidden `ReadAccount` dependency

Resolved. `replace_order()` now explicitly requires both:

```python
self._require_scope(TRADE_SCOPE)
self._require_scope(READ_ACCOUNT_SCOPE)
```

### 5. Stream `raise_on_error` hardcoded

Resolved. Stream helpers now accept `raise_on_error`, and `TradeStationStream` exposes this behavior.

### 6. Bar enums not wired

Mostly resolved. `get_bars()` and `stream_bars()` now accept `BarChartParams`.

Confirm that `BarChartParams` is exported in `__init__.py` if it should be public.

### 7. `fetch_state_snapshot()` sequential awaits

Resolved. It now uses `asyncio.gather()`.

### 8. `_get_order_pages()` infinite pagination risk

Resolved. There is now `MAX_ORDER_PAGES = 1000` and `PaginationError`.

### 9. Stream-open 401 handling

Resolved. `HTTPStreamOpenError` exists and `_stream_chunks()` handles stream-open 401 by refreshing once.

### 10. `Retry-After` cap

Resolved. Explicit `Retry-After` is returned directly, and HTTP-date parsing exists.

### 11. OAuth loopback one-request bug

Resolved. `authorize_with_loopback()` now loops on `handle_request()` until callback, error, or deadline.

---

## Remaining issues to sort

### 1. Decide whether `cancel_order()` should preflight account ownership

Current state:

```python
async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
    self._require_scope(TRADE_SCOPE)
    self.config.assert_can_cancel_orders(account_id)
    cleaned_order_id = self._order_id_value(order_id)
    return await self._rest.delete_order_write(
        f"/orderexecution/orders/{quote(cleaned_order_id, safe='')}",
        local_request_id=cleaned_order_id,
    )
```

This validates that `account_id` is allowlisted, but it does not prove `order_id` belongs to that account. The actual write endpoint is still accountless:

```text
DELETE /orderexecution/orders/{order_id}
```

There are two reasonable choices:

#### Option A — preflight cancel like replace

Use `get_orders_by_id((account_id,), (order_id,))` before cancel. This makes the account allowlist a hard boundary for cancel writes too.

Pros:
- Stronger safety boundary.
- Symmetric with `replace_order()`.

Cons:
- Adds latency to a risk-reducing action.
- A cancel may fail if the order is already gone from the account-scoped endpoint but still cancelable by ID.

#### Option B — keep current behavior but document it

If cancellation is intentionally allowed without ownership preflight because it is risk-reducing and latency-sensitive, document that explicitly in README and method docs.

Suggested README text:

```md
`cancel_order(account_id, order_id)` validates that `account_id` is allowlisted, but it does not preflight order ownership before sending the cancel request. This is intentional to avoid delaying a risk-reducing operation. Use `get_orders_by_id()` before cancel if your integration requires a hard account-ownership proof.
```

Recommended action: pick one and add tests/docs.

---

### 2. Prevent stream-open API errors from blind reconnect loops

Current concern:

`_stream_chunks()` converts non-401 `HTTPStreamOpenError` into an API error. `TradeStationStream.events()` bypasses reconnect for:

```python
StreamError
StreamParseError
AuthenticationError
ConfigurationError
```

but generic `TradeStationAPIError` and `RateLimitError` can still fall into the generic reconnect loop.

Suggested change:

```python
from tradestation_api_wrapper.errors import (
    AuthenticationError,
    ConfigurationError,
    RateLimitError,
    TradeStationAPIError,
)

...

except RateLimitError:
    raise
except TradeStationAPIError:
    raise
```

Alternative: if you want stream-open 429 to retry, sleep for `retry_after_seconds` before retrying instead of immediate reconnect loops.

Tests to add:

- Stream-open 403 raises once, no reconnect.
- Stream-open 400 raises once, no reconnect.
- Stream-open 429 either raises once or sleeps according to `retry_after_seconds`, depending on chosen policy.

Likely files:

- `src/tradestation_api_wrapper/stream.py`
- `tests/unit/test_stream_session.py`

---

### 3. Add file locking or a single-process warning for `FileTokenStore`

Current issue:

`FileTokenStore.compare_and_swap_refresh_token()` is still read-check-write. `OAuthManager` has an `asyncio.Lock`, but that only protects one process. Multiple bot processes sharing one token file can still race during refresh-token rotation.

Options:

#### Option A — document single-process only

Add README and class docstring language:

```md
`FileTokenStore` is safe for one process. It does not provide cross-process file locking. If multiple bot instances share credentials, use an external token store or add your own process-level lock.
```

#### Option B — add file locking

Use platform-specific locking or an optional dependency. Keep it simple if you support only common local filesystems.

Recommended action: document immediately; add locking later if needed.

Likely files:

- `src/tradestation_api_wrapper/auth.py`
- `README.md`

---

### 4. Add `"Entries"` to market-depth classification

Current `_looks_like_market_data()` includes several depth keys:

```python
"Bids",
"Asks",
"BidLevels",
"AskLevels",
"Side",
"Price",
"Size",
```

The pass-3 review also mentioned `Entries`. Add it. This is cheap and harmless.

Suggested change:

```python
"Entries",
```

Test:

- A payload like `{"Message": "depth update", "Entries": [...]}` is classified as `DATA`, not `ERROR`.

Likely files:

- `src/tradestation_api_wrapper/stream.py`
- `tests/unit/test_stream_session.py`

---

### 5. Add a direct-`OrderRequest` asset-class migration note

README says unknown asset class is rejected and builders now set `AssetClass.EQUITY`, but direct model callers need an explicit migration warning.

Add to `Migration notes for 0.2.0`:

```md
- Direct `OrderRequest(...)` construction must set `asset_class`; the default is now `AssetClass.UNKNOWN` and write validation rejects unknown asset classes. Builder helpers default to `AssetClass.EQUITY`.
```

Likely file:

- `README.md`

---

### 6. Add typed `OptionChainStreamParams`

Bars now have typed params, but `stream_option_chain()` still accepts:

```python
params: Mapping[str, object | None] | None = None
```

Suggested direction:

```python
class OptionChainStreamParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expiration: date | datetime | str | None = Field(default=None, alias="expiration")
    strike_price: Decimal | None = Field(default=None, alias="strikePrice")
    option_type: str | None = Field(default=None, alias="optionType")
    enable_greeks: bool | None = Field(default=None, alias="enableGreeks")
    ...
```

Use exact fields from the pinned spec.

Client signature:

```python
def stream_option_chain(
    self,
    underlying: str,
    *,
    params: OptionChainStreamParams | None = None,
    raise_on_error: bool = True,
) -> AsyncIterator[StreamEvent]:
    ...
```

Consider keeping mapping support temporarily if backward compatibility matters.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/client.py`
- `src/tradestation_api_wrapper/__init__.py`
- `tests/unit/test_client_features.py`

---

### 7. Add `AdvancedOptionsReplace`

`OrderReplaceRequest` still uses normal `AdvancedOptions`. If TradeStation’s replace schema distinguishes `AdvancedOptionsReplace`, keep replacement payloads spec-accurate.

Suggested model:

```python
class AdvancedOptionsReplace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    market_activation_rules: MarketActivationRulesReplace | None = Field(
        default=None,
        alias="MarketActivationRules",
    )
    show_only_quantity: Decimal | None = Field(default=None, alias="ShowOnlyQuantity")
    time_activation_rules: TimeActivationRulesReplace | None = Field(
        default=None,
        alias="TimeActivationRules",
    )
    trailing_stop: TrailingStop | None = Field(default=None, alias="TrailingStop")
```

Then:

```python
class OrderReplaceRequest(BaseModel):
    advanced_options: AdvancedOptionsReplace | None = Field(default=None, alias="AdvancedOptions")
```

Tests:

- Replace advanced options serialize in the replace-specific shape.
- Normal order `AdvancedOptions` still serialize unchanged.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

---

### 8. Recommend `HttpxAsyncTransport` for serious streaming workloads

`UrllibAsyncTransport.stream()` still calls `asyncio.to_thread(stream.read, 8192)` for every chunk. This is okay for a no-extra fallback but can be expensive on high-frequency streams.

Add README note:

```md
For heavy streaming workloads, prefer `HttpxAsyncTransport` by installing `tradestation-api-wrapper[httpx]`. The urllib fallback is intentionally dependency-free but uses thread-pool reads for streaming.
```

Likely file:

- `README.md`

---

### 9. Confirm `BarChartParams` exists and is exported

`client.py` imports `BarChartParams`, and README migration notes mention it. Make sure:

- `BarChartParams` is defined in `models.py`.
- It is exported in `src/tradestation_api_wrapper/__init__.py`.
- Tests verify serialization for common bar params.
- The public README example includes it, if desired.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/__init__.py`
- `tests/unit/test_client_features.py`

---

### 10. Add spec coverage or explicit skip list

This is still useful for a wrapper whose purpose is to wrap the official TradeStation v3 API.

Add a test:

```python
WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS = {
    ...
}

EXPLICITLY_SKIPPED = {
    "/v3/brokerage/accounts/{account}/wallets": "not yet implemented",
    ...
}

def test_openapi_paths_are_wrapped_or_explicitly_skipped() -> None:
    spec = json.loads(Path("specs/tradestation/openapi.2026-05-09.json").read_text())
    paths = set(spec["paths"])
    missing = paths - WRAPPED_OR_EXPLICITLY_SKIPPED_ENDPOINTS - set(EXPLICITLY_SKIPPED)
    assert not missing
```

Every skipped endpoint should have a reason.

Likely files:

- `tests/unit/test_spec_coverage.py`
- `specs/tradestation/openapi.2026-05-09.json`

---

## Lower-priority considerations

### A. Group single-account/single-symbol validation is still outside the model

`GroupOrderRequest` itself only validates group shape. `validate_group_for_config()` enforces single account and symbol.

This is okay if the design is “models are payload-ish; client validation makes them broker-safe.” If so, document it. If you want model-level safety, add a model validator.

### B. `max_order_notional` default may block futures

`max_order_notional=1000` is conservative and will block normal futures contracts. That may be desirable, but README should say futures users need to set a higher limit or use a futures-specific risk model.

### C. Status `STT` remains uncategorized

If `TradeStationOrderStatus.STATUS_MESSAGE = "STT"` intentionally maps to none of active/done/working, add a comment or test to make that explicit.

---

## Suggested next Codex prompt

```text
Review the latest `stridskoma2/ts-api-wrapper` main branch and patch the remaining post-pass3 items.

Do not place live orders. Keep SIM order-placement tests opt-in.

Prioritize:
1. Decide whether cancel_order(account_id, order_id) should preflight account ownership like replace_order. If yes, implement it; if not, document clearly that cancel only validates the supplied account ID is allowlisted and skips ownership preflight for latency/risk-reduction reasons.
2. Prevent stream-open API errors from blind reconnect loops: TradeStationAPIError should bypass reconnect; RateLimitError should either bypass reconnect or sleep according to retry_after_seconds.
3. Document FileTokenStore as single-process only, or add cross-process file locking.
4. Add "Entries" to stream market-depth data classification and test it.
5. Add README migration note: direct OrderRequest(...) construction must now set asset_class; builders default to AssetClass.EQUITY.
6. Add typed OptionChainStreamParams and wire it into stream_option_chain().
7. Add AdvancedOptionsReplace for OrderReplaceRequest if the pinned spec has a distinct replace advanced-options schema.
8. Recommend HttpxAsyncTransport in README for heavy streaming workloads.
9. Confirm BarChartParams is defined, exported, documented, and tested.
10. Add an OpenAPI spec coverage test with explicit skip reasons for unwrapped endpoints.

Run:
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Acceptance criteria

- Unit tests pass.
- Ruff passes.
- Mypy passes.
- Remaining pass-3 issues are either fixed or clearly documented as intentional tradeoffs.
- Cancel-account behavior is explicit and tested/documented.
- Stream-open non-401 failures no longer spin through immediate reconnects.
- Token-store process-safety limitation is documented or fixed.
- Typed params and replace-specific models improve spec fidelity without breaking existing documented examples unexpectedly.

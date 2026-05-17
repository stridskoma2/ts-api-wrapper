# Codex Follow-up Brief: Post Pass-5 Cleanup for `stridskoma2/ts-api-wrapper`

Repository: `https://github.com/stridskoma2/ts-api-wrapper`  
Basis: attached pass-5 review plus latest source review from current `main`  
Goal: close the remaining pre-release polish and edge-case reliability items.

Do **not** place live orders while working on this. Keep SIM order-placement integration tests opt-in only.

## Executive summary

The wrapper is now in good shape. The earlier high-risk issues around ambiguous writes, account-scoped replace/cancel, malformed JSON handling, typed bar params, stream 401 refresh, token file locking, and direct stream error configuration are largely addressed.

The remaining items are not large architectural problems. They are mostly:

- stream-open retry policy refinement,
- option-chain parameter validation,
- risk-free-rate validation,
- spec-coverage robustness,
- README/changelog clarity,
- a few spec-fidelity and strict-mode safety improvements.

Prioritize P5-1 first. The rest are low-risk cleanup.

---

## Run checks

After changes, run:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Priority 1

### P5-1. Refine stream reconnect behavior for `TradeStationAPIError`

Current concern:

`TradeStationStream.events()` bypasses reconnect for all `TradeStationAPIError`:

```python
except (AuthenticationError, ConfigurationError, TradeStationAPIError):
    raise
```

That is correct for deterministic client/config failures such as 400, 403, or 404, but too broad for transient stream-open failures:

- `429` should honor rate-limit guidance or back off.
- `5xx` should reconnect with backoff up to the reconnect limit.

Do **not** blindly immediate-reconnect in a tight loop. Add a real delay path.

Suggested behavior:

```python
except (AuthenticationError, ConfigurationError):
    raise

except RateLimitError as exc:
    if reconnects >= self._reconnect_policy.max_reconnects:
        raise
    reconnects += 1
    await self._sleep_for_reconnect(reconnects, exc.retry_after_seconds)
    continue

except TradeStationAPIError as exc:
    # Deterministic client errors should not reconnect.
    if 400 <= exc.status_code < 500:
        raise

    # 5xx / non-4xx stream-open failures can reconnect with backoff.
    if reconnects >= self._reconnect_policy.max_reconnects:
        raise
    reconnects += 1
    await self._sleep_for_reconnect(reconnects, None)
    continue
```

Implementation options:

1. Add a sleeper/backoff to `StreamReconnectPolicy`.
2. Reuse `RetryPolicy` / `sleep_with_policy` from `rate_limit.py`.
3. Handle stream-open retry policy in `TradeStationRestClient._stream_chunks()` instead of in `TradeStationStream`.

A small policy addition is probably cleanest:

```python
@dataclass(frozen=True, slots=True)
class StreamReconnectPolicy:
    max_reconnects: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0
```

Tests to add:

- Stream-open 400 raises immediately, no reconnect.
- Stream-open 403 raises immediately, no reconnect.
- Stream-open 429 sleeps according to `retry_after_seconds` and retries.
- Stream-open 503 reconnects with backoff.
- Reconnect limit is still enforced.

Likely files:

- `src/tradestation_api_wrapper/stream.py`
- `src/tradestation_api_wrapper/rest.py`
- `tests/unit/test_stream_session.py`

---

## Priority 2

### P5-2. Add enums or validation for `OptionChainStreamParams` string fields

Current issue:

`OptionChainStreamParams` has typed structure, but several fields are still raw strings:

```python
spread_type: str | None
strike_range: str | None
option_type: str | None
```

This allows typos like:

```python
OptionChainStreamParams(option_type="Calls")
```

Suggested enums:

```python
class OptionType(str, Enum):
    ALL = "All"
    CALL = "Call"
    PUT = "Put"

class StrikeRange(str, Enum):
    ALL = "All"
    ITM = "ITM"
    OTM = "OTM"
```

For `spread_type`, use the exact values from the pinned spec. Likely candidates include:

```python
class OptionSpreadTypeName(str, Enum):
    SINGLE = "Single"
    VERTICAL = "Vertical"
    COLLAR = "Collar"
    BUTTERFLY = "Butterfly"
    CONDOR = "Condor"
    DIAGONAL = "Diagonal"
    CALENDAR = "Calendar"
```

Confirm against `specs/tradestation/openapi.lock` / pinned OpenAPI before committing exact values.

Update `OptionChainStreamParams`:

```python
spread_type: OptionSpreadTypeName | None = Field(default=None, alias="spreadType")
strike_range: StrikeRange | None = Field(default=None, alias="strikeRange")
option_type: OptionType | None = Field(default=None, alias="optionType")
```

Tests:

- Valid enum values serialize to the expected API strings.
- Invalid raw strings are rejected by Pydantic.
- Existing examples still work.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `src/tradestation_api_wrapper/__init__.py`
- `tests/unit/test_models_and_validation.py`
- `tests/unit/test_client_features.py`

---

### P5-3. Allow `risk_free_rate=0`, and consider allowing negative rates

Current issue:

`OptionChainStreamParams` validates both `risk_free_rate` and `price_center` with the same positive-decimal validator:

```python
@field_validator("risk_free_rate", "price_center")
def require_positive_decimal(...)
```

`price_center <= 0` should remain invalid.

But `risk_free_rate=0` is a valid economic value. Negative rates are also plausible in real markets unless the TradeStation spec forbids them.

Recommended change:

```python
@field_validator("price_center")
@classmethod
def require_positive_price_center(cls, value: Decimal | None) -> Decimal | None:
    if value is not None and value <= 0:
        raise ValueError("price_center must be positive")
    return value

@field_validator("risk_free_rate")
@classmethod
def allow_risk_free_rate(cls, value: Decimal | None) -> Decimal | None:
    return value
```

If you want to reject negative rates as a product choice:

```python
if value is not None and value < 0:
    raise ValueError("risk_free_rate cannot be negative")
```

But only do that if the pinned spec or TradeStation docs imply a non-negative constraint.

Tests:

- `risk_free_rate=Decimal("0")` is accepted.
- `price_center=Decimal("0")` is rejected.
- Negative `risk_free_rate` is either accepted or rejected according to the chosen policy, with a test.

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_models_and_validation.py`

---

### P5-4. Make `test_spec_coverage.py` read `openapi.lock`

Current issue from the pass-5 review:

```python
SPEC_PATH = Path("specs/tradestation/openapi.2026-05-09.json")
```

This is brittle when the pinned spec updates. Instead of hardcoding or picking the latest filename, read the lock file. That ensures the coverage test uses the exact pinned spec the repo declares.

Suggested implementation:

```python
import json
from pathlib import Path

SPEC_LOCK_PATH = Path("specs/tradestation/openapi.lock")

def pinned_spec_path() -> Path:
    lock = json.loads(SPEC_LOCK_PATH.read_text(encoding="utf-8"))
    spec_path = SPEC_LOCK_PATH.parent / lock["pinned_file"]
    assert spec_path.exists(), f"pinned OpenAPI file does not exist: {spec_path}"
    return spec_path
```

Then use:

```python
SPEC_PATH = pinned_spec_path()
```

Tests:

- Coverage test loads the lock file.
- If the pinned file is missing, the failure message is clear.

Likely file:

- `tests/unit/test_spec_coverage.py`

---

### P5-5. Document `OrderReplaceRequest.AdvancedOptions` type change

Current behavior:

`OrderReplaceRequest.advanced_options` now expects `AdvancedOptionsReplace`, not `AdvancedOptions`.

This is correct for spec fidelity, but it is a runtime breaking change for code like:

```python
OrderReplaceRequest(AdvancedOptions=AdvancedOptions(...))
```

Add a README migration note under `0.2.0`:

```md
- `OrderReplaceRequest.AdvancedOptions` now expects `AdvancedOptionsReplace`, not `AdvancedOptions`. For legacy `OrderRequest` coercion, the client maps compatible advanced-option fields automatically, but direct replace requests must use the replace-specific model.
```

Also consider a short docstring on `OrderReplaceRequest`.

Likely files:

- `README.md`
- optionally `src/tradestation_api_wrapper/models.py`

---

## Priority 3

### P5-6. Add targeted tests for `_TokenFileLock`

`FileTokenStore` locking is a major improvement, but lock-file code needs tests.

Add tests for:

- stale lock removal when PID is not running,
- active lock timeout,
- lock cleanup on exception,
- compare-and-swap returns `False` if another writer changed the refresh token,
- save/load still works normally.

Notes:

- Avoid brittle OS-specific assumptions.
- Use a temporary directory.
- If testing “active process” lock is hard, monkeypatch `_process_is_running()`.

Likely files:

- `src/tradestation_api_wrapper/auth.py`
- `tests/unit/test_auth.py`

---

### P5-7. Verify `BarChartParams` aliases against the pinned spec

Current aliases appear lower-case:

```python
barsback
firstdate
lastdate
startdate
sessiontemplate
```

That may be correct for TradeStation, but verify against the pinned OpenAPI spec. If the spec uses camelCase (`barsBack`, `firstDate`, `lastDate`, `sessionTemplate`), update the aliases or add tests proving the lowercase names are intentional.

Suggested test:

```python
params = BarChartParams(...)
query = _bar_query_params(params)
assert set(query) == {... exact spec keys ...}
```

Likely files:

- `src/tradestation_api_wrapper/models.py`
- `tests/unit/test_client_features.py`
- `tests/unit/test_models_and_validation.py`

---

### P5-8. Consider strict asset-class mode for direct `OrderRequest`

Current behavior:

`OrderRequest.asset_class` defaults to `AssetClass.EQUITY`. This is friendlier for direct equity order construction, but it means direct `OrderRequest(...)` callers can accidentally submit an option/future-looking symbol as equity unless they explicitly set `asset_class`.

Potential opt-in safety mode:

```python
require_explicit_asset_class: bool = False
```

In validation:

```python
if config.require_explicit_asset_class and order.asset_class is AssetClass.EQUITY and not order.asset_class_was_explicit:
    ...
```

Pydantic does not make “was explicit” trivial, so a simpler variant is:

```python
strict_asset_class_validation: bool = False
```

and require callers to avoid direct `OrderRequest` or use builders. This may not be worth the complexity; document the tradeoff if left as-is.

Minimum action:

- Keep README warning for direct `OrderRequest`.
- Make sure builder docs emphasize `asset_class` for non-equity orders.

Likely files:

- `README.md`
- optionally `src/tradestation_api_wrapper/config.py`
- optionally `src/tradestation_api_wrapper/validation.py`

---

### P5-9. Confirm scope mapping against official docs / pinned spec

Client-side scope preflight is now strong, which is good, but overly strict preflight can reject calls the API would accept.

Check these mappings:

- `get_option_risk_reward()` → `OptionSpreads`
- market-depth streams → `Matrix`
- order streams → `ReadAccount`
- replace/cancel preflight → `ReadAccount` plus `Trade`
- routes / activation triggers → `Trade`

If any scope is uncertain, document the basis or make scope enforcement configurable.

Likely files:

- `src/tradestation_api_wrapper/client.py`
- `README.md`
- `tests/unit/test_client_features.py`

---

### P5-10. Consider softening “production-grade” metadata

`README.md` is now more nuanced, but package metadata may still say:

```toml
description = "Production-grade TradeStation API v3 REST wrapper ..."
```

If this package has not yet been validated with real SIM/live integration beyond tests, consider softening:

```toml
description = "Correctness-first TradeStation API v3 REST wrapper with validation, retries, streaming parsing, and reconciliation helpers."
```

Likely file:

- `pyproject.toml`

---

## Small implementation checklist

Suggested Codex prompt:

```text
Patch the latest stridskoma2/ts-api-wrapper main branch. Do not place live orders.

Focus on the post-pass5 cleanup:
1. Refine stream-open API-error reconnect policy: deterministic 4xx should raise immediately; 429 should honor retry_after_seconds/backoff; 5xx should reconnect with backoff up to the reconnect limit.
2. Add enums or validation for OptionChainStreamParams.spread_type, strike_range, and option_type using values from the pinned spec.
3. Split OptionChainStreamParams decimal validators so risk_free_rate=0 is accepted; decide whether negative risk_free_rate is allowed based on the spec.
4. Make tests/unit/test_spec_coverage.py derive the OpenAPI file from specs/tradestation/openapi.lock.
5. Add a README migration note for OrderReplaceRequest.AdvancedOptions now requiring AdvancedOptionsReplace.
6. Add targeted tests for FileTokenStore lock behavior.
7. Verify BarChartParams query aliases against the pinned spec and update tests.
8. Optionally soften pyproject “production-grade” wording.

Run:
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

---

## Acceptance criteria

- Tests pass.
- Ruff passes.
- Mypy passes.
- Stream-open 429/5xx handling is not a tight loop and does not fail too early.
- Option-chain params reject obvious typo values.
- `risk_free_rate=0` is accepted.
- Spec coverage always follows `openapi.lock`.
- README migration notes cover the replace advanced-options type break.
- Token file locking has meaningful tests.

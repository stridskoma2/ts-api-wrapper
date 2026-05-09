# Codex Implementation Plan: Production-Grade Python TradeStation Trading Integration

**Audience:** Codex / implementation agent  
**Target broker:** TradeStation only  
**API target:** TradeStation REST API v3 + TradeStation HTTP streaming  
**Language:** Python 3.12+  
**Quality bar:** Production-grade, live-trading-safe, auditable, failure-tolerant.

---

## 0. Scope correction

This is a **TradeStation-only** implementation plan.

Do **not** implement Interactive Brokers / IBKR support. Do **not** build an IBKR adapter. Do **not** include IBKR API calls, `ib_async`, TWS, Client Portal, IBKR-specific `parentId`, `transmit`, `ocaGroup`, `ocaType`, or other IBKR-specific mechanics.

However, the TradeStation system must support the same **trading behaviors** that are already handled in the existing IBKR implementation:

1. Entry orders with protective bracket exits.
2. OCO / bracket exits consisting of profit target + stop loss.
3. Moving an existing stop loss to breakeven.
4. Partial profit-taking followed by resizing the remaining target/stop orders.
5. Adding to an existing position and replacing old exits with a new aggregate protective exit structure.
6. Never leaving a live position unprotected because of an unsafe cancel/replace sequence.

All of those behaviors must be implemented using **TradeStation-native order, group order, cancel, replace, stream, and reconciliation workflows**.

---

## 1. Non-negotiable safety principles

This is not a thin REST wrapper. It is an execution-control system for live trading.

Mandatory rules:

1. **The strategy must never call TradeStation directly.** Strategy code emits intent. The execution system validates, journals, confirms, submits, monitors, reconciles, and can freeze/cancel/kill trading.
2. **No blind retry of order placement.** If a non-idempotent submit request times out after it may have reached TradeStation, freeze the intent and reconcile before doing anything else.
3. **No casual SIM/LIVE switching.** SIM and LIVE must be separate configs/deployments. No UI dropdown or runtime toggle.
4. **Durable journal before broker submission.** Persist the intent, mapped TradeStation payload, payload hash, and submit attempt before sending an order.
5. **Risk checks before confirmation, submission, and replacement.** Replacement can change exposure and protection, so it must be risk-checked too.
6. **Streams are not trusted alone.** Order and position streams are useful but must be reconciled with REST snapshots.
7. **Broker state wins after reconciliation.** Local state is an event ledger. TradeStation state is authoritative once reconciled.
8. **Fail closed.** Unknown order state, stale market data, token failure, position mismatch, stream outage, or missing protective exits must stop new opening orders.
9. **No token leakage.** Access tokens, refresh tokens, client secrets, account IDs, and sensitive order payloads must be redacted from ordinary logs.
10. **Every edge case gets a test.** Especially order-placement timeout, duplicate signal, stream split JSON, partial fills, bracket resizing, add-to-position exit replacement, and manual broker intervention.

---

## 2. TradeStation facts to encode into the implementation

Use the current TradeStation OpenAPI specification as the authoritative contract. Download it from TradeStation's API Specification page before implementation and commit a pinned copy under:

```text
specs/tradestation/openapi.YYYY-MM-DD.json
```

Important facts from TradeStation documentation:

- TradeStation recommends API v3.
- Live v3 base URL: `https://api.tradestation.com/v3`.
- SIM v3 base URL: `https://sim-api.tradestation.com/v3`.
- API access is HTTPS.
- Standard request/response payloads are JSON unless a specific auth endpoint requires form encoding.
- Blank fields may appear as `null` or may be omitted. Response models must tolerate both.
- Authentication uses Auth0-style OAuth.
- Default API keys use Authorization Code Flow. PKCE may be available if requested/configured.
- Access tokens expire after 20 minutes.
- `offline_access` is required to receive refresh tokens.
- Refresh tokens must be stored securely. If rotating refresh tokens are enabled, the newly returned refresh token must be atomically stored.
- SIM uses fake accounts/fake money and simulated fills. Do not let the app casually switch between SIM and LIVE.
- Rate limits are per user/client/resource category. Exceeding quota returns HTTP `429 Too Many Requests`.
- TradeStation recommends streaming where available.
- TradeStation streaming is HTTP chunked streaming, not WebSocket.
- HTTP chunks are not application-message boundaries. One JSON object can be split across chunks, or several JSON objects can appear in one chunk.
- v3 order/position streams may emit `{"StreamStatus":"EndSnapshot"}` and `{"StreamStatus":"GoAway"}`. On `GoAway`, the client must restart the stream.
- Historical minute-bar requests have limits and must be planned/chunked.
- TradeStation exposes order confirmation, order placement, group order confirmation, group order placement, cancel, replace, routes, activation triggers, accounts, balances, positions, orders, and streaming endpoints. Verify exact endpoint paths and payload schemas against the pinned OpenAPI spec.
- TradeStation supports OCO/OSO/bracket concepts. Use native group-order endpoints where supported by the API/spec.

Reference URLs:

```text
https://api.tradestation.com/docs/
https://api.tradestation.com/docs/specification/
https://api.tradestation.com/docs/fundamentals/http-requests/
https://api.tradestation.com/docs/fundamentals/authentication/auth-overview/
https://api.tradestation.com/docs/fundamentals/authentication/refresh-tokens/
https://api.tradestation.com/docs/fundamentals/rate-limiting/rate-limiting-overview/
https://api.tradestation.com/docs/fundamentals/rate-limiting/historical-bar/
https://api.tradestation.com/docs/fundamentals/sim-vs-live/
https://api.tradestation.com/docs/fundamentals/http-streaming/
https://help.tradestation.com/10_00/eng/tradestationhelp/tb/oco_oso_orders.htm
https://help.tradestation.com/10_00/eng/tradestationhelp/tb/bracket_settings.htm
```

---

## 3. Target architecture

Implement a TradeStation execution system, not a generic broker framework.

```text
Strategy / signal source
        |
        v
Signal normalizer
        |
        v
Pre-trade risk engine  --->  kill switch / exposure limits / duplicate guard
        |
        v
TradeStation OMS  --->  durable order journal / state machine / reconciliation
        |
        v
TradeStation adapter
        |
        +--> OAuth/token service
        +--> REST client with rate limits/retries/schema validation
        +--> HTTP streaming client
        +--> market-data cache
        +--> account/position/order cache
        +--> OCO/bracket/OSO group-order manager
        |
        v
Post-trade ledger / audit log / metrics / alerts
```

Separation of responsibility:

```text
Strategy:
  Emits desired trade intent only.

Risk engine:
  Decides if intent may become a TradeStation order or order group.

TradeStation OMS:
  Owns intent, durable state, order lifecycle, group lifecycle, idempotency, reconciliation, and unknown-state handling.

TradeStation adapter:
  Authenticates, rate-limits, calls TradeStation endpoints, parses streams, and emits normalized TradeStation events.

Reconciler:
  Compares local ledger, stream events, REST snapshots, positions, balances, and fills.
```

---

## 4. Recommended Python stack

Use:

```text
Python >= 3.12
httpx.AsyncClient
pydantic v2
SQLAlchemy 2.x or SQLModel
asyncio / anyio
PostgreSQL
Alembic
cryptography
structlog
OpenTelemetry
pytest
pytest-asyncio
respx or pytest-httpx
hypothesis
ruff
mypy or pyright
```

Use Redis only for ephemeral locks/cache if needed. PostgreSQL is the source of truth.

Do not depend on unofficial TradeStation wrappers as the production core. They can be inspected as references, but this implementation should own auth, rate limits, submit semantics, stream parsing, OMS, risk, and reconciliation.

---

## 5. Repository layout

```text
tradestation_trading/
  pyproject.toml
  README.md
  specs/
    tradestation/
      openapi.YYYY-MM-DD.json
      openapi.lock
  src/tradestation_trading/
    __init__.py
    config.py
    types.py

    auth/
      oauth.py
      pkce.py
      token_store.py
      token_models.py
      redaction.py

    tradestation/
      adapter.py
      rest.py
      rate_limit.py
      endpoints.py
      models.py
      order_mapper.py
      group_order_mapper.py
      stream_client.py
      stream_parser.py
      stream_models.py
      marketdata.py
      brokerage.py
      orders.py
      group_orders.py
      errors.py
      capabilities.py

    execution/
      oms.py
      state_machine.py
      order_builder.py
      group_order_manager.py
      protective_exit_manager.py
      idempotency.py
      submit_workflow.py
      cancel_replace.py
      reconciliation.py
      fill_processor.py

    risk/
      engine.py
      checks.py
      exposure.py
      stale_data.py
      duplicate_guard.py
      kill_switch.py

    marketdata/
      cache.py
      bars.py
      quotes.py
      symbol_validation.py

    ledger/
      db.py
      schema.py
      repositories.py
      migrations/

    observability/
      logging.py
      metrics.py
      alerts.py
      audit.py

    cli/
      main.py
      auth_login.py
      reconcile.py
      kill_switch.py
      live_readiness.py

  tests/
    unit/
    integration_sim/
    replay/
    chaos/
    fixtures/
```

---

## 6. Configuration model

Implement strict config loading. Do not allow implicit defaults for live trading.

Example config:

```yaml
environment: SIM  # SIM | LIVE
base_url: https://sim-api.tradestation.com/v3
client_id: ${TRADESTATION_CLIENT_ID}
client_secret: ${TRADESTATION_CLIENT_SECRET}
redirect_uri: http://localhost:31022/callback
requested_scopes:
  - openid
  - offline_access
  - MarketData
  - ReadAccount
  - Trade
account_allowlist:
  - "123456789"
live_trading_enabled: false
max_order_notional: "1000"
max_symbol_position_notional: "5000"
max_daily_loss: "500"
max_daily_order_count: 20
allow_market_orders: false
allow_options: false
allow_futures: false
allow_extended_hours: false
kill_switch_file: /var/run/tradestation_trading/kill_switch
```

Validation rules:

- If `environment=SIM`, base URL must equal `https://sim-api.tradestation.com/v3`.
- If `environment=LIVE`, base URL must equal `https://api.tradestation.com/v3`.
- If `environment=LIVE`, require `live_trading_enabled=true`, account allowlist, and explicit deploy-time acknowledgement.
- Never infer LIVE from URL alone.
- Never allow one process to switch environment after startup.
- Never allow order submission without account allowlist match.
- Never allow order submission while kill switch is active.

---

## 7. TradeStation internal models

Use internal models so strategy code never constructs raw TradeStation JSON.

### 7.1 Enums

```python
class Environment(str, Enum):
    SIM = "SIM"
    LIVE = "LIVE"

class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"
    BUY_TO_OPEN = "BUY_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    TRAILING_STOP_LIMIT = "TRAILING_STOP_LIMIT"

class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    GTD = "GTD"
    IOC = "IOC"
    FOK = "FOK"

class GroupType(str, Enum):
    SINGLE = "SINGLE"
    OCO = "OCO"
    BRACKET_OCO = "BRACKET_OCO"
    OSO = "OSO"
    ENTRY_WITH_BRACKET = "ENTRY_WITH_BRACKET"
    AGGREGATE_EXIT_OCO = "AGGREGATE_EXIT_OCO"

class PartialFillBehavior(str, Enum):
    CANCEL_SIBLINGS_ON_ANY_FILL = "CANCEL_SIBLINGS_ON_ANY_FILL"
    REDUCE_SIBLINGS_PROPORTIONALLY = "REDUCE_SIBLINGS_PROPORTIONALLY"
    TRADESTATION_NATIVE = "TRADESTATION_NATIVE"
```

### 7.2 Order intent

```python
class OrderIntent(BaseModel):
    intent_id: UUID
    strategy_id: str
    account_id: str
    symbol: str
    asset_class: AssetClass
    side: Side
    quantity: Decimal
    order_type: OrderType
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trail_amount: Decimal | None = None
    time_in_force: TimeInForce
    extended_hours: bool = False
    route: str | None = None
    reason: str
    created_at: datetime
    client_order_id: str
    tradestation_metadata: dict[str, Any] = Field(default_factory=dict)
```

### 7.3 Order group intent

```python
class OrderGroupIntent(BaseModel):
    group_intent_id: UUID
    strategy_id: str
    account_id: str
    group_type: GroupType
    legs: list[OrderIntent]
    parent_client_order_id: str | None = None
    oco_group_key: str | None = None
    partial_fill_behavior: PartialFillBehavior
    use_tradestation_native_group: bool = True
    created_at: datetime
    reason: str
    tradestation_metadata: dict[str, Any] = Field(default_factory=dict)
```

### 7.4 Protective exit plan

This is the key model for the OCO/bracket scenarios.

```python
class ProtectiveExitPlan(BaseModel):
    plan_id: UUID
    strategy_id: str
    account_id: str
    symbol: str
    asset_class: AssetClass
    position_side: Literal["LONG", "SHORT"]
    current_position_quantity: Decimal
    target_price: Decimal | None
    stop_price: Decimal | None
    stop_limit_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    mode: Literal[
        "CREATE_ENTRY_WITH_BRACKET",
        "CREATE_STANDALONE_BRACKET_EXIT",
        "MOVE_STOP_TO_BREAKEVEN",
        "RESIZE_AFTER_PARTIAL_EXIT",
        "ADD_TO_POSITION_REPLACE_WITH_AGGREGATE_EXIT",
    ]
    related_trade_id: UUID | None = None
    existing_group_id: UUID | None = None
```

### 7.5 TradeStation references

```python
class TradeStationOrderRef(BaseModel):
    environment: Environment
    account_id: str
    tradestation_order_id: str
    tradestation_group_id: str | None = None
    client_order_id: str
    local_order_id: UUID
    local_group_id: UUID | None = None
```

---

## 8. Database schema

Use PostgreSQL. Current-state tables are projections. Event tables are append-only.

Minimum tables:

```text
tradestation_accounts
  id uuid pk
  environment text
  account_id text
  status text
  created_at timestamptz
  updated_at timestamptz
  unique(environment, account_id)

tradestation_order_groups
  id uuid pk
  group_intent_id uuid unique
  strategy_id text
  account_id text
  environment text
  group_type text
  local_state text
  tradestation_group_id text null
  parent_order_id uuid null
  partial_fill_behavior text
  created_at timestamptz
  updated_at timestamptz

tradestation_orders
  id uuid pk
  intent_id uuid unique
  group_id uuid null references tradestation_order_groups(id)
  strategy_id text
  account_id text
  environment text
  symbol text
  asset_class text
  side text
  quantity numeric
  remaining_quantity numeric
  filled_quantity numeric
  order_type text
  limit_price numeric null
  stop_price numeric null
  time_in_force text
  client_order_id text unique
  tradestation_order_id text null
  parent_local_order_id uuid null
  payload_hash text
  local_state text
  tradestation_status text null
  created_at timestamptz
  updated_at timestamptz
  unique(environment, account_id, tradestation_order_id)

tradestation_order_events
  id uuid pk
  order_id uuid null references tradestation_orders(id)
  group_id uuid null references tradestation_order_groups(id)
  event_type text
  previous_state text null
  next_state text null
  source text -- strategy | risk | rest | stream | poll | reconciler | operator
  payload jsonb
  payload_hash text
  occurred_at timestamptz
  received_at timestamptz

tradestation_raw_events
  id uuid pk
  environment text
  account_id text null
  stream_name text null
  tradestation_order_id text null
  raw_payload jsonb
  raw_payload_hash text
  received_at timestamptz
  processed_at timestamptz null
  processing_status text

tradestation_fills
  id uuid pk
  order_id uuid references tradestation_orders(id)
  tradestation_fill_id text null
  account_id text
  symbol text
  side text
  quantity numeric
  price numeric
  commission numeric null
  occurred_at timestamptz
  received_at timestamptz
  unique(order_id, tradestation_fill_id)

tradestation_positions
  id uuid pk
  environment text
  account_id text
  symbol text
  asset_class text
  quantity numeric
  avg_price numeric null
  source text -- tradestation_snapshot | local_fill_projection
  as_of timestamptz
  unique(environment, account_id, symbol, source)

risk_events
  id uuid pk
  intent_id uuid null
  group_intent_id uuid null
  decision text -- APPROVED | REJECTED | WARNED
  reason text
  details jsonb
  created_at timestamptz

idempotency_records
  id uuid pk
  key text unique
  intent_id uuid null
  group_intent_id uuid null
  payload_hash text
  status text -- CREATED | SUBMITTING | SUBMITTED | UNKNOWN | RECONCILED | FAILED
  tradestation_order_id text null
  tradestation_group_id text null
  created_at timestamptz
  updated_at timestamptz

kill_switch_events
  id uuid pk
  state text -- ENABLED | DISABLED
  reason text
  actor text
  created_at timestamptz
```

Important constraints:

- `client_order_id` must be globally unique per deployment.
- `intent_id` and `group_intent_id` must be unique.
- `payload_hash` is required before submission.
- `tradestation_order_id` can be null until accepted, but if present must be unique per environment/account.
- Store raw TradeStation payloads for replay.
- Store normalized projections separately from raw events.

---

## 9. TradeStation adapter interface

This is not a broker-generic interface. It is a clean seam for testing and for separating OMS from API details.

```python
class TradeStationAdapter(Protocol):
    async def get_accounts(self) -> list[TradeStationAccount]: ...
    async def get_balances(self, account_ids: list[str]) -> list[TradeStationBalance]: ...
    async def get_positions(self, account_ids: list[str]) -> list[TradeStationPositionSnapshot]: ...
    async def get_open_orders(self, account_ids: list[str]) -> list[TradeStationOrderSnapshot]: ...
    async def get_recent_orders(self, account_ids: list[str], since: datetime) -> list[TradeStationOrderSnapshot]: ...

    async def confirm_order(self, order: OrderIntent) -> TradeStationOrderConfirmation: ...
    async def submit_order(self, order: OrderIntent) -> TradeStationOrderAck: ...

    async def confirm_order_group(self, group: OrderGroupIntent) -> TradeStationOrderConfirmation: ...
    async def submit_order_group(self, group: OrderGroupIntent) -> TradeStationGroupAck: ...

    async def replace_order(self, tradestation_order_id: str, replacement: OrderIntent) -> TradeStationOrderAck: ...
    async def cancel_order(self, tradestation_order_id: str) -> TradeStationCancelAck: ...

    async def stream_orders(self, account_ids: list[str]) -> AsyncIterator[TradeStationOrderEvent]: ...
    async def stream_positions(self, account_ids: list[str]) -> AsyncIterator[TradeStationPositionEvent]: ...
    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[TradeStationQuoteEvent]: ...
    async def stream_bars(self, symbol: str, spec: BarSpec) -> AsyncIterator[TradeStationBarEvent]: ...
```

---

## 10. OAuth/token implementation

Implement `TradeStationAuthManager`.

Responsibilities:

- Build authorization URL.
- Handle callback and code exchange.
- Store refresh token securely.
- Keep access token in memory where possible.
- Refresh proactively before expiry.
- Use a refresh lock to prevent refresh stampede.
- On 401, refresh once and retry once.
- Support standard Auth Code Flow.
- Support PKCE only if TradeStation key is configured for it.
- Redact all secrets in logs.

Refresh logic:

```text
if access_token missing:
    refresh or require interactive login
elif expires_at <= now + refresh_margin:
    refresh under lock
else:
    use current token
```

Recommended refresh margin: 120-180 seconds.

If rotating refresh tokens are enabled and a new refresh token is returned, persist it atomically in the same transaction as token metadata.

Token store interface:

```python
class TokenStore(Protocol):
    async def load(self, key: str) -> StoredTokenSet | None: ...
    async def save(self, key: str, token_set: StoredTokenSet) -> None: ...
    async def compare_and_swap_refresh_token(
        self,
        key: str,
        old_refresh_token_hash: str,
        new_token_set: StoredTokenSet,
    ) -> bool: ...
```

Use encryption-at-rest. Store token hashes for comparison; store encrypted values separately.

---

## 11. TradeStation REST client

Implement `TradeStationRestClient` around `httpx.AsyncClient`.

Features:

- Base URL selected at startup only.
- Bearer token per request.
- JSON request/response handling.
- Form-encoded OAuth calls where required.
- Timeouts.
- Connection pooling.
- Structured logs.
- Redaction.
- Typed errors.
- Pydantic validation with `extra="allow"` for new broker fields.
- Tolerance for missing/null fields.
- Per-resource-category rate limiting.

Retry policy:

```text
GET:
  retry temporary network errors, 408, 429, 500, 502, 503, 504 with jittered backoff

POST order confirm / group confirm:
  retry carefully, because confirmation does not place orders

POST order submit / group submit:
  no blind retry

PUT replace:
  no blind retry if result unknown; reconcile first

DELETE cancel:
  no blind retry if result unknown; reconcile first
```

Order submit timeout workflow:

```text
submit_order starts
  persist SUBMIT_ATTEMPT with payload_hash
  send POST
  if response ack includes tradestation_order_id:
      persist tradestation_order_id immediately
      transition to BROKER_ACCEPTED / WORKING as appropriate
  elif network timeout / connection reset / unknown result:
      mark SUBMITTED_UNKNOWN
      freeze this intent
      reconcile open/recent TradeStation orders by account/symbol/side/qty/type/price/time window
      if exactly one matching order is found:
          attach tradestation_order_id and continue
      else:
          leave RECONCILE_REQUIRED and alert
      never submit the same order again automatically
```

Group order submit timeout follows the same rule, but matching may require multiple child orders and/or a TradeStation group ID.

---

## 12. Rate limiter

Implement `TradeStationRateLimitGovernor`.

Inputs:

- Endpoint path.
- Method.
- Resource category.
- Response headers if available.
- Static configured quotas from docs/spec.

Behavior:

- Maintain buckets/windows per resource category.
- Parse rate-limit response headers when available.
- On 429, wait until reset if known; otherwise back off with jitter.
- Prioritize order state, positions, and reconciliation over market-data polling.
- Prefer streams to polling for quotes/orders/positions.
- Historical bars use separate pacing/credit logic.

Priority queue:

```text
P0: kill switch actions, cancel all, unknown-order reconciliation
P1: order/position/balance snapshots
P2: order confirmation and placement
P3: market data needed for risk checks
P4: historical bars/backfill
P5: diagnostics and low-priority polling
```

Do not let quote polling or historical bars starve order/position reconciliation.

---

## 13. HTTP streaming client

Implement `TradeStationHttpStreamClient` and `JsonObjectStreamParser`.

Parser must handle:

- one JSON object in one HTTP chunk
- multiple JSON objects in one HTTP chunk
- one JSON object split across chunks
- newline-delimited JSON
- whitespace between objects
- malformed JSON leading to stream restart
- missing/null fields

Do not treat an HTTP chunk as a JSON message.

Lifecycle:

```text
connect
receive initial snapshot messages
on EndSnapshot: mark stream ready
consume live events
on GoAway: reconnect after jitter, fetch REST snapshot, reconcile
on stream error: terminate/reconnect/reconcile according to policy
on stale stream/no messages: mark stale, reconnect, reconcile
on network error: reconnect, reconcile
```

After reconnect:

1. Fetch relevant REST snapshot.
2. Recreate stream.
3. Wait for `EndSnapshot` if applicable.
4. Reconcile snapshot with local state.
5. Resume normal processing.

Use bounded queues. A slow consumer must not exhaust memory.

---

## 14. Market data subsystem

Implement in this order:

1. Symbol validation/details.
2. Quote snapshot for startup and diagnostics.
3. Historical bars with local cache.
4. Streaming quote/bars for active symbols.
5. Entitlement error handling.

Historical-bar planner must enforce TradeStation limits from docs/spec, including:

- intraday request bar-count cap
- barsback minute cap
- minute date-range cap
- credit pacing for large requests

Risk engine must reject new orders when required market data is stale.

Example staleness policy:

```text
regular-hours equity marketable order:
  max_quote_age_ms = 3000

regular-hours passive limit order:
  max_quote_age_ms = 10000

after-hours:
  market orders disabled
  stricter bid/ask validation
```

---

## 15. Account/brokerage startup sequence

```text
1. Load config.
2. Initialize token manager.
3. Authenticate or refresh token.
4. Fetch TradeStation accounts.
5. Validate account allowlist.
6. Fetch balances.
7. Fetch positions.
8. Fetch open orders.
9. Load local non-terminal orders from DB.
10. Reconcile local vs TradeStation open/recent orders.
11. Start order stream.
12. Start position stream.
13. Wait for stream readiness / EndSnapshot.
14. Enable strategy only if reconciliation is clean.
```

If reconciliation fails, strategy must remain disabled for new opening orders.

---

## 16. Order state machine

Use local states independent of TradeStation status strings.

```text
NEW
RISK_REJECTED
RISK_APPROVED
CONFIRMING
CONFIRMED
SUBMITTING
SUBMITTED_UNKNOWN
BROKER_ACCEPTED
WORKING
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELED
REPLACE_REQUESTED
REPLACED
REJECTED
EXPIRED
ERROR
RECONCILE_REQUIRED
```

Rules:

- Every transition is persisted.
- Transitions are idempotent.
- Duplicate stream events must not duplicate fills.
- Terminal states are absorbing except for correction/reconciliation events.
- `RECONCILE_REQUIRED` freezes opening orders until resolved.

---

## 17. Group state machine

Order groups need their own state because a group can be partly healthy and partly broken.

```text
GROUP_NEW
GROUP_RISK_REJECTED
GROUP_RISK_APPROVED
GROUP_CONFIRMING
GROUP_CONFIRMED
GROUP_SUBMITTING
GROUP_SUBMITTED_UNKNOWN
GROUP_ACCEPTED
GROUP_WORKING
GROUP_PARENT_WORKING
GROUP_CHILDREN_PENDING_ACTIVATION
GROUP_CHILDREN_WORKING
GROUP_PARTIALLY_FILLED
GROUP_COMPLETED
GROUP_CANCEL_REQUESTED
GROUP_CANCELED
GROUP_BROKEN
GROUP_RECONCILE_REQUIRED
```

`GROUP_BROKEN` examples:

- Target child exists but stop child missing.
- Stop exists but target missing.
- Child quantities do not match managed position.
- Parent filled but children never activated.
- One child replacement succeeded and the other failed.
- TradeStation stream/poll disagree about group status.

Broken protective groups must freeze new opening orders and alert immediately.

---

## 18. Pre-trade risk engine

Run before confirmation and before submission. Run again before replacement.

Account-level checks:

- Account allowlist.
- Account trading enabled.
- Max daily loss.
- Max daily order count.
- Max open orders.
- Max gross notional.
- Max net exposure.
- Buying power/margin availability.
- Allowed asset classes.
- LIVE environment flag.

Symbol-level checks:

- Allowed symbols.
- Blocked symbols.
- Max position per symbol.
- Max order quantity.
- Max order notional.
- Price bands relative to last/bid/ask.
- Halt/no-quote/stale-quote guard.
- Options/futures contract validation.
- Short-sale restrictions/locate requirements if applicable.

Strategy-level checks:

- Strategy enabled.
- Max concurrent orders.
- Max position delta.
- Duplicate signal debounce.
- Cooldown after reject/cancel/error.
- Kill switch.

Order-type checks:

- Market orders disabled by default in LIVE.
- Options/futures disabled until explicitly enabled/tested.
- Limit prices required for options unless explicitly overridden.
- Stop/stop-limit side/price sanity.
- Route/TIF/session compatibility.
- Quantity and tick-size precision.

Group-order checks:

- Validate all legs together.
- Validate aggregate exposure impact.
- Validate OCO siblings do not create unintended exposure.
- Validate bracket exits match current or expected position size.
- Validate OSO child behavior.
- Validate only one active protective-exit plan per managed position unless explicitly allowed.

---

## 19. TradeStation order mapper

Strategy code must never build raw TradeStation payloads.

Implement:

```text
TradeStationOrderMapper
  internal OrderIntent -> TradeStation single-order payload

TradeStationGroupOrderMapper
  internal OrderGroupIntent -> TradeStation group-order payload

TradeStationOrderEventMapper
  TradeStation raw order/stream event -> normalized local order event
```

Use the pinned OpenAPI spec for exact field names and enums.

Expected endpoints to verify in OpenAPI:

```text
GET    /v3/brokerage/accounts
GET    /v3/brokerage/accounts/{accounts}/balances
GET    /v3/brokerage/accounts/{accounts}/positions
GET    /v3/brokerage/accounts/{accounts}/orders
GET    /v3/brokerage/accounts/{accounts}/historicalorders
GET    /v3/orderexecution/routes
GET    /v3/orderexecution/activationtriggers
POST   /v3/orderexecution/orderconfirm
POST   /v3/orderexecution/orders
POST   /v3/orderexecution/ordergroupconfirm
POST   /v3/orderexecution/ordergroups
PUT    /v3/orderexecution/orders/{orderID}
DELETE /v3/orderexecution/orders/{orderID}
GET    /v3/brokerage/stream/accounts/{accountIds}/orders
GET    /v3/brokerage/stream/accounts/{accountIds}/positions
```

Do not hard-code endpoint schemas from memory. Validate against OpenAPI.

---

## 20. TradeStation OCO / bracket / OSO requirements

Support these TradeStation order group behaviors.

### 20.1 `OCO`

A set of linked orders where execution of one order causes siblings to be canceled according to TradeStation native behavior.

Use for:

- alternative entries
- alternative exits
- simple one-cancels-other workflows

Requirements:

- Submit as TradeStation-native group order if supported.
- Track every child order ID.
- Reconcile sibling cancellation after fill.
- If a sibling is not canceled when expected, alert and optionally cancel it through safe policy.

### 20.2 `BRACKET_OCO`

A protective exit structure for an existing position:

```text
profit target limit order
protective stop or stop-limit order
same symbol
same exit side
same initial quantity
```

Requirements:

- Prefer TradeStation-native bracket/OCO group behavior.
- Track whether TradeStation decrements sibling quantities after partial fills.
- If TradeStation-native behavior differs by asset class/order type, encode capability flags and tests.
- Reconcile child quantities against actual position after every fill/position update.

### 20.3 `OSO`

Primary order sends secondary orders after fill.

Use for:

- entry order that activates protective bracket exits
- parent order with one or more secondary orders

Requirements:

- Confirm parent + children as a group before submission.
- Track parent state separately from children.
- Detect parent partial fill.
- Detect child activation failure.
- If parent fills but children are missing, freeze opening orders and alert immediately.

### 20.4 `ENTRY_WITH_BRACKET`

An OSO-style parent entry with target + stop exits.

Requirements:

- Parent entry order is confirmed/submitted with children where TradeStation supports it.
- Child exits must not be considered active until TradeStation says they are active/working.
- If parent partially fills, child quantities must match actual filled/position quantity.
- If parent is canceled after partial fill, protective exits must remain sized to filled quantity only.

### 20.5 `AGGREGATE_EXIT_OCO`

A new protective exit group for the current total position.

Use when:

- position size changed because of adding to a position
- existing exit orders are fragmented
- existing exits are unsafe to mutate
- TradeStation rejects a child replace that would preserve protection

Safety rule:

```text
submit and verify new aggregate protective exits first
then cancel old exits
never cancel old exits first unless the position is flat or another protective order is already live
```

---

## 21. Protective exit manager scenarios

Implement `TradeStationProtectiveExitManager`.

### Scenario A: Create entry with bracket

Input:

```text
Buy 100 XYZ at limit 10.00
Target sell 100 XYZ at 11.00
Stop sell 100 XYZ at 9.50
```

Expected workflow:

```text
1. Build ENTRY_WITH_BRACKET OrderGroupIntent.
2. Risk-check parent and children.
3. Confirm TradeStation group order.
4. Submit TradeStation group order.
5. Persist parent and child TradeStation order IDs when known.
6. Track parent fill.
7. Track child activation.
8. Reconcile child quantities with filled/position quantity.
```

Edge cases:

- Parent partially fills and children activate only for filled quantity.
- Parent partially fills then is canceled.
- Parent rejects; children must not exist.
- Parent fills but children fail to activate.
- TradeStation returns incomplete child IDs in submit response; reconcile by open orders.

### Scenario B: Create standalone bracket exit for existing position

Input:

```text
Currently long 100 XYZ
Target sell 100 XYZ at 11.00
Stop sell 100 XYZ at 9.50
```

Expected workflow:

```text
1. Fetch/reconcile current position.
2. Build BRACKET_OCO group for current position quantity.
3. Risk-check exits.
4. Confirm group.
5. Submit group.
6. Verify both target and stop children are live.
7. Mark position protected only after verification.
```

Edge cases:

- Current position is smaller/larger than expected.
- Existing protective exits already exist.
- Group submission creates one child but not the other.
- Stop price invalid due to current market price.
- Target/stop route or TIF unsupported.

### Scenario C: Move stop to breakeven, no profit taking

Input:

```text
Existing live bracket exits
Position remains original size
Request: move stop to breakeven
```

Preferred workflow:

```text
1. Reconcile current position and existing bracket group.
2. Locate existing stop child.
3. Build replace payload for the existing stop child only.
4. Preserve group linkage by replacing the existing TradeStation order where supported.
5. Risk-check replacement price.
6. Submit replace request.
7. Verify new stop price via stream/poll.
8. Do not cancel/recreate the whole bracket unless replacement is unsupported or unsafe.
```

Fallback workflow if TradeStation cannot safely replace the child in place:

```text
1. Build new AGGREGATE_EXIT_OCO for current full position using existing target and new breakeven stop.
2. Confirm new group.
3. Submit new group.
4. Verify both new children live.
5. Cancel old target/stop group.
6. Verify old children canceled.
```

Edge cases:

- Stop fills while replace is in flight.
- Target fills while replace is in flight.
- Replace rejected due to invalid stop price.
- Replace times out.
- TradeStation changes order ID after replace.
- Position is already flat.

### Scenario D: Partial profit then resize exits and move stop

Input:

```text
Original bracket exits for 100 shares
Separate partial-profit order sells 40 shares
Remaining position is 60 shares
Request: keep target, move stop to breakeven, resize exits to 60
```

Preferred workflow:

```text
1. Reconcile current position from TradeStation.
2. Reconcile current child target/stop quantities.
3. Determine required remaining exit quantity.
4. Replace existing target child quantity to 60 if needed.
5. Replace existing stop child quantity to 60 and stop price to breakeven.
6. Preserve existing group where TradeStation supports this safely.
7. Verify both children via stream/poll.
```

Fallback workflow if any child replacement is unsupported/unsafe:

```text
1. Build new AGGREGATE_EXIT_OCO for current remaining position quantity.
2. Confirm and submit the new group.
3. Verify both children are live.
4. Cancel old exit children/group.
5. Verify old exits canceled.
```

Edge cases:

- Partial-profit order and target child fill near the same time.
- TradeStation already decremented the sibling quantity.
- Child quantity replacement rejected because child partially filled.
- One child replace succeeds and the other fails.
- Position differs from expected because of manual action.
- Current position is flat; cancel all exits.

### Scenario E: Add to existing position

Input:

```text
Currently long 100 XYZ with old target/stop exits
Add 50 XYZ
Need exits for total 150 XYZ
```

Required workflow:

```text
1. Wait for add order to reach known final/partial state.
2. Reconcile actual current position quantity.
3. Build new AGGREGATE_EXIT_OCO for actual current position quantity.
4. Confirm new aggregate exits.
5. Submit new aggregate exits.
6. Verify both new exit children are live.
7. Cancel old exit orders/group.
8. Verify old exits canceled.
```

Do not mutate old exits into a new aggregate exit if doing so risks broken linkage or temporary loss of protection.

Edge cases:

- Add order partially fills.
- Add order fills but position stream is delayed.
- New aggregate group confirm fails.
- New aggregate group submit times out.
- New aggregate group creates only one child.
- Old target or stop fills before cancellation.
- Old exit cancel fails because already filled.
- Current position is different from intended quantity.
- Position flips or becomes flat during workflow.

### Scenario F: Manual TradeStation UI intervention

Input:

```text
User manually cancels/modifies an order in TradeStation UI.
```

Expected workflow:

```text
1. Detect via stream or polling reconciliation.
2. Update local state from TradeStation state.
3. If protective exits are missing/wrong-sized, freeze opening orders and alert.
4. Depending on policy, recreate protective exits or require operator action.
```

---

## 22. Confirmation and submission workflows

### 22.1 Single order

```text
1. Receive signal.
2. Normalize into OrderIntent.
3. Generate client_order_id.
4. Persist NEW.
5. Run risk.
6. Persist RISK_APPROVED or RISK_REJECTED.
7. Map to TradeStation payload.
8. Compute payload_hash.
9. Persist CONFIRMING.
10. Call TradeStation order confirmation endpoint.
11. Validate confirmation response: estimated cost, buying power, warnings.
12. Persist CONFIRMED.
13. Persist SUBMITTING and idempotency record.
14. Call TradeStation order submission endpoint.
15. If ack includes order ID, persist it immediately.
16. Continue lifecycle through stream/poll events.
```

### 22.2 Group order

```text
1. Receive group/exit intent.
2. Normalize into OrderGroupIntent.
3. Validate all legs.
4. Risk-check as a group.
5. Persist group and child orders.
6. Map to TradeStation group payload.
7. Compute group payload hash.
8. Confirm group order.
9. Submit group order.
10. Persist TradeStation group/child IDs when available.
11. Reconcile if response is incomplete.
12. Track parent/child activation and sibling cancellation/reduction.
```

---

## 23. Cancel/replace workflow

### 23.1 Cancel

```text
1. Validate local state says order is cancelable.
2. Persist CANCEL_REQUESTED.
3. Call TradeStation cancel endpoint.
4. Do not assume canceled until stream/poll confirms.
5. If cancel fails because already filled/canceled, reconcile and transition accordingly.
```

### 23.2 Replace

```text
1. Load existing local order and TradeStation order ID.
2. Validate replacement is allowed.
3. Build replacement payload preserving fields not intended to change.
4. Risk-check replacement.
5. Persist REPLACE_REQUESTED with old/new payload hashes.
6. Call TradeStation replace endpoint.
7. Do not assume success until stream/poll confirms.
8. If timeout/unknown, reconcile before retrying.
```

Replacement edge cases:

- Order fills during replace.
- Order cancels during replace.
- Replace rejected due to invalid price/quantity.
- Partial fill reduces remaining quantity while replace is in flight.
- TradeStation returns new order ID vs same order ID.
- Replace cannot modify a child order in a group.
- Replace breaks group behavior; detect and fail closed.

---

## 24. Reconciliation engine

Run reconciliation:

- at startup
- periodically
- after every stream reconnect
- after every unknown submission
- after every cancel/replace timeout
- after kill-switch actions
- before enabling strategy after restart

Startup reconciliation:

```text
1. Load local non-terminal orders.
2. Fetch TradeStation open orders.
3. Fetch recent/historical TradeStation orders since last known time.
4. Match by known TradeStation order ID.
5. Match unknown submissions by account/symbol/side/quantity/order type/price/timestamp/payload hash when possible.
6. Update local states.
7. Compare TradeStation positions vs local fill-derived positions.
8. Validate protective exits for each managed position.
9. Alert/freeze on mismatches.
```

Continuous reconciliation:

```text
- Compare open orders.
- Compare positions.
- Compare balances/margin if needed.
- Compare fills/order quantities.
- Validate protective exits for each managed position.
```

Matching unknown submitted orders:

```text
same account
same symbol
same side/action
same order type
same quantity or plausible remaining quantity
same limit/stop prices
created/updated within submit time window
not already linked to another local order
```

If exactly one match: attach TradeStation order ID.  
If multiple matches: leave `RECONCILE_REQUIRED` and alert.  
If no match after bounded attempts: leave `RECONCILE_REQUIRED`; do not auto-resubmit.

---

## 25. Edge-case catalog

Codex should implement tests for all of these.

### 25.1 Authentication/security

- Access token expires during request.
- Refresh token expires or is revoked.
- Rotating refresh token returned; old token must be replaced atomically.
- Two coroutines refresh simultaneously.
- 401 after refresh; fail closed.
- Client secret/token accidentally appears in exception; redaction must remove it.

### 25.2 Rate limits

- 429 on low-priority quote snapshot should not block order reconciliation.
- 429 on positions should back off and keep streams alive.
- Historical bar credit exhaustion.
- Concurrent stream limit reached.
- Rate-limit headers missing.
- System clock skew makes reset timestamp wrong.

### 25.3 Streaming

- JSON object split across chunks.
- Multiple JSON objects in one chunk.
- `EndSnapshot` received.
- `GoAway` received.
- Stream error received.
- Stream silently stalls.
- Stream reconnect produces duplicate events.
- Stream event arrives before REST snapshot.
- Stream event missing optional fields.
- Stream sends null where model expects missing, or missing where model expects null.

### 25.4 Order submission

- Confirm succeeds, submit fails.
- Submit times out but TradeStation accepted order.
- Submit times out and TradeStation did not accept order.
- Submit returns malformed/incomplete ack.
- TradeStation returns order ID but stream never shows it.
- Duplicate signal arrives while first order is submitting.
- Same strategy emits conflicting buy/sell intents.
- Different strategies trade same symbol/account.

### 25.5 Fills and partial fills

- Partial fill then cancel.
- Partial fill then replace.
- Partial fill then OCO sibling cancel/reduce.
- Fill event duplicated.
- Fill event arrives before order accepted event.
- Commission arrives later than fill.
- Position update arrives before fill event.
- Fill price outside expected band.

### 25.6 OCO/bracket/OSO

- OCO sibling not canceled by TradeStation after fill.
- Bracket sibling not reduced after partial fill.
- Entry parent partially fills; child exits size mismatch.
- Entry parent fills; child exits never activate.
- Stop child modified to breakeven while target fills.
- Target child modified/resized while stop fills.
- Add-to-position workflow creates new exits, but old exits fill before cancellation.
- Old exits cancel fails because already filled.
- New aggregate exit has one child live and one missing.
- Manual cancellation of one protective child leaves position unprotected.
- TradeStation rejects group due to unsupported route/TIF/order type.
- TradeStation group ID not returned; must map children individually.

### 25.7 Cancel/replace

- Cancel request races with fill.
- Cancel request times out.
- Replace request races with fill.
- Replace request changes quantity while partial fill occurs.
- Replace rejected but local state incorrectly assumed changed.
- TradeStation returns same order ID vs new order ID.
- Replace cannot alter group child safely.

### 25.8 Market/session

- Market closed.
- Extended-hours flag mismatch.
- Halted symbol.
- No bid/ask.
- Stale quote.
- Wide spread.
- Corporate action/split changes quantity/price.
- Options expiration/assignment.
- Futures expiration/roll.
- Short-sale restriction/locate issue.
- Fractional shares unsupported or restricted.

### 25.9 Multi-account/concurrency

- Same symbol in multiple TradeStation accounts.
- Same strategy in multiple accounts.
- Duplicate account IDs in config.
- Account removed from API key.
- API key has more accounts than allowlist.
- Multi-process deployment requires distributed locks for token refresh and order submission.

### 25.10 Operations

- Kill switch appears while order submitting.
- Kill switch cancels all open orders; some cancels fail due to fills.
- Database unavailable.
- DB commit succeeds but HTTP request fails.
- HTTP request succeeds but DB commit fails.
- Process crashes after submit before ack persisted.
- Process restarts with non-terminal orders.
- OpenAPI spec changes.
- Deployment accidentally points LIVE credentials at SIM URL or vice versa.

---

## 26. Observability

Structured logs must include:

```text
correlation_id
strategy_id
account_id_hash or redacted account id
intent_id
group_intent_id
local_order_id
client_order_id
tradestation_order_id if available
environment
state
```

Metrics:

```text
tradestation_orders_submitted_total
tradestation_orders_rejected_total
tradestation_orders_filled_total
tradestation_orders_canceled_total
tradestation_orders_reconcile_required_total
tradestation_order_submit_unknown_total
tradestation_http_latency_ms
tradestation_http_429_total
tradestation_http_5xx_total
tradestation_token_refresh_total
tradestation_token_refresh_failure_total
tradestation_stream_connected
tradestation_stream_reconnect_total
tradestation_stream_stale_total
tradestation_stream_parse_error_total
tradestation_position_mismatch_total
tradestation_protective_exit_missing_total
tradestation_kill_switch_active
```

Immediate alerts:

- Unknown submitted order.
- Position mismatch.
- Missing protective exit for managed position.
- Broken group order.
- Stream stale or repeated reconnect.
- Token refresh failure.
- Sustained 429 on order/account resources.
- Kill switch activation.
- Any live order in `RECONCILE_REQUIRED`.

---

## 27. Test plan

### 27.1 Unit tests

Auth:

- Refresh before expiry.
- Refresh under lock.
- Rotating refresh-token compare-and-swap update.
- Redaction.

REST:

- GET retry behavior.
- POST submit no blind retry.
- 401 refresh-once.
- 429 backoff.
- Missing/null fields.

Streaming:

- Split JSON object.
- Multiple JSON objects per chunk.
- EndSnapshot.
- GoAway.
- Stream error.
- Stale stream.

Order mapper:

- Single equity limit.
- Single stop.
- Entry-with-bracket group.
- Bracket OCO exit group.
- Aggregate exit OCO.
- Invalid TIF/order type/route rejected.

Risk:

- Account allowlist.
- Max order notional.
- Max position size.
- Stale quote.
- Duplicate signal.
- Market orders disabled.
- Live trading disabled.

OMS:

- Normal submit/fill.
- Submit timeout accepted by TradeStation.
- Submit timeout not accepted.
- Partial fill.
- Cancel/fill race.
- Replace/fill race.
- Duplicate events.

Protective exits:

- Move stop to breakeven modifies existing child where supported.
- If child replace unsupported, new aggregate exit group is submitted before old group canceled.
- Partial-profit resizes target/stop children where supported.
- Add-to-position creates new aggregate exits then cancels old exits.
- One child replace succeeds and one fails.
- Parent partial fill activates child quantity correctly.

### 27.2 Integration tests in TradeStation SIM

Run only with SIM credentials and fake accounts.

- Auth flow.
- Account discovery.
- Balance fetch.
- Position fetch.
- Quote snapshot.
- Historical bars.
- Stream bars.
- Stream orders.
- Stream positions.
- Confirm order.
- Place tiny SIM limit order.
- Cancel SIM order.
- Replace SIM order.
- Confirm group order.
- Place OCO/bracket group order where supported.
- Reconcile after stream reconnect.

### 27.3 Replay tests

Store raw TradeStation events and replay them through parser/state machine.

Replay fixtures:

- Normal order lifecycle.
- Partial fills.
- Duplicate fills.
- Out-of-order fill/status.
- Stream reconnect.
- Bracket partial sibling reduction.
- Manual cancellation.

### 27.4 Chaos tests

Simulate:

- Network timeout after submit.
- 500 after submit.
- DB crash after submit.
- Stream drop while order fills.
- Stream stale.
- Rate limits during market-data burst.
- Token expiry during order submission.
- Two processes submitting same intent.

---

## 28. Implementation milestones

### Milestone 1: Foundation

Deliver:

- Repo skeleton.
- Config validation.
- Postgres schema/migrations.
- Structured logging/redaction.
- OpenAPI spec pinned.
- TradeStation adapter interface.

Acceptance:

- Unit tests pass.
- SIM/LIVE config validation rejects unsafe configs.

### Milestone 2: Auth + REST core

Deliver:

- OAuth login/callback utility.
- Secure token store.
- Token refresh.
- REST client.
- Rate limiter.

Acceptance:

- Can fetch accounts in SIM.
- 401/429 tests pass.
- No secrets in logs.

### Milestone 3: Read-only brokerage/market data

Deliver:

- Accounts, balances, positions, open orders.
- Quote snapshots.
- Symbol details.
- Historical bars with limit planner.

Acceptance:

- Startup snapshot can populate account/position/order cache.
- Historical bar planner enforces limits.

### Milestone 4: Streaming

Deliver:

- HTTP stream parser.
- Order stream.
- Position stream.
- Quote/bar streams.
- Reconnect/reconcile hooks.

Acceptance:

- Parser test suite passes all chunking cases.
- Streams recover from GoAway/stale errors.

### Milestone 5: OMS + risk

Deliver:

- Order state machine.
- Group state machine.
- Durable event journal.
- Pre-trade risk engine.
- Idempotency records.
- Reconciliation engine.

Acceptance:

- Simulated order lifecycle tests pass.
- Unknown submit workflow never blind retries.

### Milestone 6: Single-order trading in SIM

Deliver:

- Confirm order.
- Submit order.
- Cancel.
- Replace.
- Fill processing.

Acceptance:

- Tiny SIM orders can be placed/canceled/replaced.
- Stream and REST reconciliation agree.

### Milestone 7: TradeStation group orders

Deliver:

- TradeStation group-order model.
- TradeStation group mapper.
- Confirm group order.
- Submit group order.
- Group state tracking.
- OCO/bracket/OSO lifecycle handling.

Acceptance:

- SIM group-order tests pass where TradeStation supports them.
- Unsupported group behavior returns explicit capability error, not silent fallback.

### Milestone 8: Protective exit manager

Deliver:

- Create entry with bracket.
- Create standalone bracket exit.
- Move stop to breakeven.
- Resize exits after partial profit.
- Add-to-position new aggregate exits then cancel old exits.

Acceptance:

- Tests prove the system does not leave a position unprotected.
- Tests prove add-to-position creates new aggregate exits before old exits are canceled.

### Milestone 9: Production hardening

Deliver:

- Metrics.
- Alerts.
- Kill switch.
- Runbooks.
- End-of-day reconciliation report.
- Live read-only dry-run mode.

Acceptance:

- Live deployment can authenticate/read-only with order submission disabled.
- Kill switch test passes.
- Unknown-state alerting works.

---

## 29. TradeStation capability model

Implement capabilities so unsupported TradeStation behavior fails explicitly.

```python
class TradeStationCapabilities(BaseModel):
    supports_single_orders: bool
    supports_order_confirm: bool
    supports_group_orders: bool
    supports_group_confirm: bool
    supports_oco: bool
    supports_bracket_oco: bool
    supports_oso: bool
    supports_replace: bool
    replace_preserves_order_id: bool | None
    supports_stream_orders: bool
    supports_stream_positions: bool
    supports_quote_stream: bool
    supports_bar_stream: bool
    supports_native_partial_fill_sibling_reduction: bool | None
```

Populate from docs/spec and validate through SIM tests.

If TradeStation cannot safely support a requested behavior natively, the system must either:

1. use the protected aggregate-exit fallback workflow, or
2. return a clear capability error and refuse to trade.

Do not silently approximate protective-exit behavior.

---

## 30. Specific Codex tasks

Codex should implement in this order:

1. Create repo skeleton and `pyproject.toml`.
2. Add config models and unsafe-config validation.
3. Add Pydantic models for TradeStation orders, groups, stream events, risk decisions.
4. Add SQLAlchemy schema and Alembic migration.
5. Implement redaction utilities.
6. Implement encrypted token store interface.
7. Implement OAuth manager.
8. Implement REST client with typed errors.
9. Implement rate-limit governor.
10. Implement stream parser and tests before stream network code.
11. Implement TradeStation adapter read-only endpoints.
12. Implement startup reconciliation.
13. Implement risk engine.
14. Implement OMS single-order flow.
15. Implement TradeStation confirm/submit/cancel/replace.
16. Implement TradeStation group-order model and mappers.
17. Implement OCO/bracket/OSO group manager.
18. Implement protective exit manager scenarios A-F.
19. Implement observability and kill switch.
20. Implement SIM integration tests.
21. Implement replay/chaos tests.

For every implementation step, add tests in the same commit.

---

## 31. Live readiness checklist

Do not enable live trading until every item is true.

Auth/security:

- [ ] Refresh tokens encrypted.
- [ ] Access tokens not persisted unnecessarily.
- [ ] Token refresh lock tested.
- [ ] Redaction tested.
- [ ] Secrets absent from logs/traces.

Environment:

- [ ] Separate SIM/LIVE deployments.
- [ ] LIVE requires explicit config acknowledgement.
- [ ] Account allowlist enforced.
- [ ] No runtime environment switching.

Orders:

- [ ] Durable journal before submit.
- [ ] Confirm-before-submit enabled.
- [ ] No blind retry for order submit or group submit.
- [ ] Unknown-submit reconciliation implemented.
- [ ] Cancel/replace verified by stream/poll.
- [ ] Raw events stored.

Risk:

- [ ] Market orders disabled by default.
- [ ] Options/futures disabled until explicitly enabled/tested.
- [ ] Per-account limits.
- [ ] Per-symbol limits.
- [ ] Daily loss/order-count limits.
- [ ] Duplicate guard.
- [ ] Stale market-data guard.
- [ ] Kill switch tested.

OCO/bracket/OSO:

- [ ] Entry-with-bracket tested in SIM or marked unsupported.
- [ ] Standalone bracket exit tested in SIM or marked unsupported.
- [ ] Move-stop-to-breakeven workflow tested.
- [ ] Partial-profit resize workflow tested.
- [ ] Add-to-position aggregate-exit workflow tested.
- [ ] Missing protective-exit alert tested.

Data/reconciliation:

- [ ] Startup reconciliation.
- [ ] Continuous reconciliation.
- [ ] Stream reconnect reconciliation.
- [ ] End-of-day reconciliation.
- [ ] Position mismatch alert.

Observability:

- [ ] Dashboard for order lifecycle.
- [ ] Dashboard for group/protective-exit health.
- [ ] Dashboard for stream health.
- [ ] Dashboard for rate limits.
- [ ] Alerts wired.
- [ ] Runbooks written.

---

## 32. Runbooks to create

Create markdown runbooks under `docs/runbooks/`:

```text
unknown_submitted_order.md
position_mismatch.md
missing_protective_exit.md
broken_order_group.md
stream_outage.md
token_refresh_failure.md
rate_limit_exhaustion.md
kill_switch_activation.md
manual_tradestation_intervention.md
live_disable_procedure.md
```

Each runbook should include:

- Symptoms.
- Immediate safety action.
- What data to inspect.
- How to reconcile.
- Whether opening orders remain disabled.
- How to resume trading.

---

## 33. Final design stance

This is TradeStation-only.

The implementation should be boring, explicit, and conservative.

The TradeStation adapter should authenticate, rate-limit, call endpoints, parse streams, and emit normalized TradeStation events.

The TradeStation OMS should own intent, state, group lifecycle, and reconciliation.

The risk engine should prevent bad orders before they reach TradeStation.

The protective exit manager should explicitly model:

- entry with bracket
- standalone bracket exits
- breakeven stop updates
- partial-profit exit resizing
- add-to-position aggregate protective exits

If TradeStation cannot safely support a behavior natively, the system must either use a tested protected fallback workflow or refuse the operation. Never silently emulate protective trading behavior in a way that can leave a live position unprotected.

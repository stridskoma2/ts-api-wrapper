# ts-api-wrapper

TradeStation-only Python REST wrapper for API v3.

The wrapper is correctness-first: it validates SIM/LIVE configuration, maps typed
requests to the official TradeStation v3 payloads, retries safe reads with bounded
backoff, never blindly retries order writes after ambiguous failures, parses HTTP
streaming chunks correctly, and exposes reconciliation helpers for unknown order
state.

Core wrapper surface:

- Typed account, balance, position, order, quote, symbol, and bar snapshots.
- Detailed account, balance, and beginning-of-day balance snapshots.
- Normalized order status helpers such as `is_active`, `is_done`, `can_cancel`,
  and `can_replace`.
- Order builders for market, limit, stop-market, stop-limit, OCO, and bracket
  order payloads.
- `what_if_order()` / `what_if_order_group()` aliases for TradeStation order
  confirmation.
- Option expiration, spread type, strike, and risk/reward helpers.
- Crypto symbol-name lookup from the TradeStation v3 market-data API.
- Order-by-ID and historical-order-by-ID helpers.
- Order writes return `TradeStationTrade`, preserving the raw payload hash, ack,
  latest snapshot, stream events, and explicit ambiguous-state signal.
- Order replace and cancel calls require the caller's account ID. Replace calls
  preflight the order through the account-scoped order endpoint before sending
  the write request.
- Snapshot helpers for accounts, balances, positions, and orders.
- Streaming primitives for order, position, quote, bar, market-depth, and option
  streams with bounded reconnect handling.
- The wrapper uses only `/v3` endpoint paths. TradeStation labels v3
  market-data stream responses with the legacy
  `application/vnd.tradestation.streams.v2+json` media type, so those stream
  requests use that `Accept` header while brokerage order/position streams use
  `application/vnd.tradestation.streams.v3+json`.
- Optional `HttpxAsyncTransport` for users who install the `httpx` extra.
- OAuth authorization-code exchange and loopback login helpers built on the same
  token-store contract as refresh-token auth.

Safety boundaries:

- `max_order_notional`, market-order enablement, option/future enablement,
  extended-hours enablement, account allowlisting, kill switch checks, explicit
  asset class, and GTD expiration rules are enforced before writes.
- `max_symbol_position_notional`, `max_daily_loss`, and
  `max_daily_order_count` are documented stateful integration guardrails. This
  wrapper does not enforce them without caller-provided portfolio/session state.
- API methods validate the OAuth scopes they require before sending requests.
  Market-depth streams require `Matrix`; option risk/reward requires
  `OptionSpreads`.

Pinned official spec:

- `specs/tradestation/openapi.2026-05-09.json`
- `specs/tradestation/openapi.lock`

Run the local verification suite:

```powershell
python -m unittest discover -s tests
```

Install the local quality tools from the repo extras, then run the configured
checks:

```powershell
python -m pip install -e .[test]
python -m ruff check .
python -m mypy src tests
```

SIM integration tests are skipped unless TradeStation SIM environment variables are
present. Any SIM order-placement test also requires `TRADESTATION_SIM_TRADE_TESTS=1`.

Minimal async usage:

```python
from tradestation_api_wrapper import Environment, TradeStationClient, TradeStationConfig
from tradestation_api_wrapper.rest import StaticTokenProvider

config = TradeStationConfig(
    environment=Environment.SIM,
    base_url="https://sim-api.tradestation.com/v3",
    client_id="...",
    requested_scopes=("openid", "offline_access", "MarketData", "ReadAccount", "Trade"),
    account_allowlist=("123456789",),
)

async with TradeStationClient(config, StaticTokenProvider("ACCESS_TOKEN")) as client:
    accounts = await client.get_accounts()
```

Order write usage:

```python
from decimal import Decimal

from tradestation_api_wrapper import AssetClass, TradeAction, limit_order

order = limit_order(
    account_id="123456789",
    symbol="MSFT",
    quantity=Decimal("1"),
    action=TradeAction.BUY,
    limit_price=Decimal("100"),
    asset_class=AssetClass.EQUITY,
)

confirmation = await client.what_if_order(order)
if not confirmation.errors:
    trade = await client.place_order(order)
    if trade.reconcile_required:
        # Do not assume the order failed. Reconcile via account/order state.
        ...
```

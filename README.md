# ts-api-wrapper

TradeStation-only Python REST wrapper for API v3.

The wrapper is correctness-first: it validates SIM/LIVE configuration, maps typed
requests to the official TradeStation v3 payloads, retries safe reads with bounded
backoff, never blindly retries order writes after ambiguous failures, parses HTTP
streaming chunks correctly, and exposes reconciliation helpers for unknown order
state.

Core wrapper surface:

- Typed account, balance, position, order, quote, symbol, and bar snapshots.
- Normalized order status helpers such as `is_active`, `is_done`, `can_cancel`,
  and `can_replace`.
- Order builders for market, limit, stop-market, stop-limit, OCO, and bracket
  order payloads.
- `what_if_order()` / `what_if_order_group()` aliases for TradeStation order
  confirmation.
- Order writes return `TradeStationTrade`, preserving the raw payload hash, ack,
  latest snapshot, stream events, and explicit ambiguous-state signal.
- Snapshot helpers for accounts, balances, positions, and orders.
- Streaming primitives for order, position, quote, and bar streams with bounded
  reconnect handling.

Pinned official spec:

- `specs/tradestation/openapi.2026-05-09.json`
- `specs/tradestation/openapi.lock`

Run the local verification suite:

```powershell
C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests
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

client = TradeStationClient(config, StaticTokenProvider("ACCESS_TOKEN"))
accounts = await client.get_accounts()
```

Order write usage:

```python
from decimal import Decimal

from tradestation_api_wrapper import TradeAction, limit_order

order = limit_order(
    account_id="123456789",
    symbol="MSFT",
    quantity=Decimal("1"),
    action=TradeAction.BUY,
    limit_price=Decimal("100"),
)

confirmation = await client.what_if_order(order)
if not confirmation.errors:
    trade = await client.place_order(order)
    if trade.reconcile_required:
        # Do not assume the order failed. Reconcile via account/order state.
        ...
```

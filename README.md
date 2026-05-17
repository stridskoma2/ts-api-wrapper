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
  unknown asset-class rejection, and GTD expiration rules are enforced before
  writes.
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

Migration notes for `0.2.0`:

- `replace_order(account_id, order_id, replacement)` and
  `cancel_order(account_id, order_id)` now require the account ID. Both methods
  require `ReadAccount` scope and preflight the order through the account-scoped
  order endpoint before sending the write request.
- `get_bars()` takes `BarChartParams` and `stream_bars()` takes
  `StreamBarChartParams` instead of raw query dictionaries. The streaming
  parameter model intentionally omits historical date fields because
  TradeStation stream bars ignore them.
- Stream helpers accept `raise_on_error=False` when callers need to keep a
  multi-symbol stream alive after per-symbol error events.
- Direct `OrderRequest(...)` construction defaults to `AssetClass.EQUITY`.
  Futures, options, and other non-equity callers must set `asset_class`
  explicitly; write validation rejects `AssetClass.UNKNOWN`.
- Order builders also default to equities; pass `asset_class` explicitly for
  futures, options, ETFs, and index-linked requests.
- `OrderReplaceRequest.AdvancedOptions` now expects `AdvancedOptionsReplace`,
  not `AdvancedOptions`. Legacy `OrderRequest` replacement coercion maps
  compatible advanced-option fields automatically, but direct replace requests
  must use the replace-specific model.
- For heavy streaming workloads, prefer `HttpxAsyncTransport` by installing
  `tradestation-api-wrapper[httpx]`. The urllib fallback is dependency-free and
  bounded, but still uses a background reader thread for streaming.

Async vs sync:

- The public network API is async-only. `TradeStationClient` methods are
  `async` methods, and stream helpers return async iterators.
- The package does not currently provide a separate synchronous client or
  synchronous convenience methods.
- From a normal script or scheduled job, put the wrapper calls in an async
  function and call it once with `asyncio.run(...)`.
- Inside an already-async app, such as FastAPI, a worker, or a notebook with an
  active event loop, `await` the client methods directly instead of calling
  `asyncio.run(...)`.

Build a SIM client:

```python
from tradestation_api_wrapper import (
    Environment,
    TradeStationClient,
    TradeStationConfig,
)
from tradestation_api_wrapper.rest import StaticTokenProvider

ACCOUNT_ID = "123456789"

config = TradeStationConfig(
    environment=Environment.SIM,
    base_url="https://sim-api.tradestation.com/v3",
    client_id="...",
    requested_scopes=("openid", "offline_access", "MarketData", "ReadAccount", "Trade"),
    account_allowlist=(ACCOUNT_ID,),
)

token_provider = StaticTokenProvider("ACCESS_TOKEN")
```

Run async calls from a synchronous script:

```python
import asyncio


async def main() -> None:
    async with TradeStationClient(config, token_provider) as client:
        accounts = await client.get_accounts()
        for account in accounts:
            print(account.account_id, account.status)


if __name__ == "__main__":
    asyncio.run(main())
```

Read account state:

```python
async with TradeStationClient(config, token_provider) as client:
    balances = await client.get_balances((ACCOUNT_ID,))
    positions = await client.get_positions((ACCOUNT_ID,))
    orders = await client.get_orders((ACCOUNT_ID,))
    snapshot = await client.fetch_state_snapshot((ACCOUNT_ID,))

    open_orders = snapshot.open_orders
    nonzero_positions = snapshot.nonzero_positions
```

Read quotes, symbol details, and bars:

```python
from tradestation_api_wrapper import BarChartParams, BarSessionTemplate, BarUnit

async with TradeStationClient(config, token_provider) as client:
    quotes = await client.get_quotes(("MSFT", "AAPL"))
    symbols = await client.get_symbols(("MSFT",))
    bars = await client.get_bars(
        "MSFT",
        params=BarChartParams(
            unit=BarUnit.MINUTE,
            interval=5,
            bars_back=20,
            session_template=BarSessionTemplate.USEQ_PRE_AND_POST,
        ),
    )
```

Use the optional `httpx` transport:

```python
from tradestation_api_wrapper import HttpxAsyncTransport

transport = HttpxAsyncTransport()

async with TradeStationClient(config, token_provider, transport=transport) as client:
    accounts = await client.get_accounts()
```

Stream quotes and keep per-symbol stream errors as events:

```python
from tradestation_api_wrapper import StreamEventKind

async with TradeStationClient(config, token_provider) as client:
    async for event in client.stream_quotes(("MSFT", "AAPL"), raise_on_error=False):
        if event.kind is StreamEventKind.DATA:
            print(event.payload)
        elif event.kind is StreamEventKind.ERROR:
            print("stream error event", event.payload)
```

Stream bars and option chains:

```python
from tradestation_api_wrapper import (
    BarUnit,
    OptionChainStreamParams,
    OptionSpreadTypeName,
    OptionType,
    StreamBarChartParams,
    StrikeRange,
)

async with TradeStationClient(config, token_provider) as client:
    async for event in client.stream_bars(
        "MSFT",
        params=StreamBarChartParams(
            unit=BarUnit.MINUTE,
            interval=1,
            bars_back=10,
        ),
    ):
        print(event.kind, event.payload)
        break

    async for event in client.stream_option_chain(
        "MSFT",
        params=OptionChainStreamParams(
            spread_type=OptionSpreadTypeName.SINGLE,
            strike_proximity=5,
            strike_range=StrikeRange.ALL,
            option_type=OptionType.CALL,
            enable_greeks=True,
        ),
        raise_on_error=False,
    ):
        print(event.kind, event.payload)
        break
```

Confirm, place, replace, and cancel a SIM order:

```python
from decimal import Decimal

from tradestation_api_wrapper import (
    AssetClass,
    OrderReplaceRequest,
    TradeAction,
    limit_order,
)

order = limit_order(
    account_id=ACCOUNT_ID,
    symbol="MSFT",
    quantity=Decimal("1"),
    action=TradeAction.BUY,
    limit_price=Decimal("100"),
    asset_class=AssetClass.EQUITY,
)

async with TradeStationClient(config, token_provider) as client:
    confirmation = await client.what_if_order(order)
    if not confirmation.errors:
        trade = await client.place_order(order)
        if trade.reconcile_required:
            # Do not assume the order failed. Reconcile via account/order state.
            ...
        elif trade.order_id is not None:
            replacement = OrderReplaceRequest(
                Quantity=Decimal("1"),
                LimitPrice=Decimal("99.50"),
            )
            updated_trade = await client.replace_order(
                ACCOUNT_ID,
                trade.order_id,
                replacement,
            )
            await client.cancel_order(ACCOUNT_ID, updated_trade.order_id or trade.order_id)
```

Use OAuth loopback login for an interactive session:

```python
from tradestation_api_wrapper import (
    MemoryTokenStore,
    OAuthManager,
    UrllibAsyncTransport,
    authorize_with_loopback,
)

transport = UrllibAsyncTransport()
token_store = MemoryTokenStore()
oauth = OAuthManager(
    client_id=config.client_id,
    client_secret=None,
    redirect_uri="http://127.0.0.1:31022/callback",
    scopes=config.requested_scopes,
    token_store=token_store,
    transport=transport,
)

await authorize_with_loopback(oauth, port=31022)

async with TradeStationClient(config, oauth, transport=transport) as client:
    accounts = await client.get_accounts()
```

For persisted tokens, provide a production `TokenCodec` to `FileTokenStore`.
`PlainTextTokenCodec` is test-only and intentionally refuses production use.

# ts-api-wrapper

TradeStation-only Python REST wrapper for API v3.

The wrapper is correctness-first: it validates SIM/LIVE configuration, maps typed
requests to the official TradeStation v3 payloads, retries safe reads with bounded
backoff, never blindly retries order writes after ambiguous failures, parses HTTP
streaming chunks correctly, and exposes reconciliation helpers for unknown order
state.

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

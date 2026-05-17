# Safety Invariants

These invariants protect callers from silent live-trading risk. Do not weaken
them to simplify implementation or tests.

## Order Writes Stay Ambiguity-Aware

Non-idempotent order writes must not be blindly retried when the result is
unknown. Treat these as ambiguous unless a later broker/account lookup proves a
final state:

- request timeout after the write may have reached TradeStation
- transport drop after submit
- 5xx response
- 408 response
- invalid JSON or non-object success body
- successful acknowledgement without a usable broker order ID

The caller-visible signal is `TradeStationTrade.reconcile_required`. Preserve it.
Do not replace it with a boolean that says the write failed.

## Replace And Cancel Stay Account-Scoped

`replace_order(account_id, order_id, replacement)` and
`cancel_order(account_id, order_id)` require the caller's account ID. Replacement
must preflight through the account-scoped order endpoint before sending the write
request. Do not reintroduce order-ID-only replacement or cancellation helpers.

## Scope Checks Happen Before Network Calls

API methods must validate required OAuth scopes before sending requests. Missing
scope errors should fail locally and clearly.

Examples:

- account/order reads require `ReadAccount`
- order writes require `Trade`
- market data requires `MarketData`
- market-depth streams require `Matrix`
- option risk/reward requires `OptionSpreads`

## Validation Stays At The Known Boundary

Validate rules that are knowable from `TradeStationConfig` and the request:

- environment and base URL consistency
- account allowlist
- kill switch
- market-order enablement
- options/futures enablement
- extended-hours enablement
- explicit unknown asset class
- invalid GTD expiration
- invalid OSO/OCO child shape
- max order notional when the request contains enough price data

Do not claim to enforce stateful portfolio/session rules without caller-provided
state, such as daily loss, daily order count, or current symbol exposure.

## Pagination Is Bounded

Paginated reads must make progress and must stop at a fixed upper bound. If the
API repeats tokens, omits progress, or exceeds the bound, raise
`PaginationError` rather than looping indefinitely.

## Streams Preserve Error Events When Requested

`raise_on_error=False` lets callers keep multi-symbol streams alive and receive
per-symbol errors as stream events. Preserve that behavior for quote, bar,
market-depth, option, order, and position streams where applicable.

## Token Persistence Is Codec-Driven And Lock-Safe

Persisted token storage must use the token-store contract. `PlainTextTokenCodec`
is test-only and must refuse production use. `FileTokenStore` writes and
compare-and-swap operations must remain serialized by a lock file and bounded
timeout.

## Optional Dependencies Stay Optional

`HttpxAsyncTransport` is available through the `httpx` extra. The default urllib
transport must remain usable without installing optional dependencies.

## Protocol Details Stay Spec-Aligned

Use only `/v3/...` endpoint paths. Keep the current stream media-type behavior:

- market-data streams use
  `application/vnd.tradestation.streams.v2+json`
- brokerage streams use
  `application/vnd.tradestation.streams.v3+json`

Do not infer protocol changes from package naming, external wrappers, or a
desire for naming symmetry. Confirm against the pinned spec first.


# Agent Instructions

## Mandatory Clean Code Skill

For any coding task in this workspace, always invoke and follow
`$uncle-bob-clean-code`.

Enforce all clean-code rule categories at all times: `C/E/F/G/N/P/T`.
Do not introduce technical debt. Preserve behavior unless a behavior change is
explicitly requested. If scope conflicts with quality, request a smaller scope
or more time instead of silently waiving rules.

Required reporting for code changes:

- Include relevant clean-code rule IDs.
- Include before and after rule snapshots.
- Confirm verification steps and outcomes.

## Repository Boundary

This repository is a TradeStation-only Python REST and stream wrapper for API v3.
Keep it a wrapper. Do not turn it into a strategy engine, order-management
system, portfolio policy layer, or DayTrading workflow coordinator.

Wrapper-owned responsibilities:

- Protocol-correct TradeStation v3 HTTP and streaming calls.
- Typed request and response models.
- Auth, scope preflight, token storage contracts, and transport lifecycle.
- Bounded retries for safe reads.
- Explicit ambiguous-state handling for order writes.
- Validation that can be done from the request/config boundary.
- Reconciliation primitives that callers can use after uncertain order state.

Caller-owned responsibilities:

- Strategy decisions.
- Portfolio/session state such as max daily loss, symbol exposure, and daily
  order counts.
- Broker failover policy.
- Live trading orchestration.
- DayTrading-specific position sizing, stop management, and trade lifecycle
  decisions.

## Source Of Truth

Use the pinned official spec before adding or changing API surface:

- `specs/tradestation/openapi.2026-05-09.json`
- `specs/tradestation/openapi.lock`

Keep endpoint paths on `/v3/...`. Do not replace the market-data streaming
`Accept` header with a guessed v3 media type; TradeStation's v3 market-data
streams currently use the legacy-labeled
`application/vnd.tradestation.streams.v2+json` media type. Brokerage
order/position streams use `application/vnd.tradestation.streams.v3+json`.

## Safety Invariants

Read `docs/safety-invariants.md` before changing order writes, replacement,
cancellation, validation, retries, pagination, streams, token persistence, or
scope handling.

Non-negotiable examples:

- Do not blindly retry non-idempotent order writes after ambiguous failures.
- Preserve `TradeStationTrade.reconcile_required` as the caller-visible signal
  for uncertain write state.
- Keep `replace_order()` and `cancel_order()` account-scoped.
- Preflight OAuth scopes before sending requests.
- Keep pagination bounded.
- Keep token file writes lock-safe.

## Change Recipes

Use `docs/agent-recipes.md` for cross-cutting changes. It lists the files and
checks normally involved in endpoint, stream, order-write, validation, auth, and
transport changes.

## Validation

Use the smallest verification command that covers the changed surface. See
`docs/validation.md` for the local validation matrix.

Common commands:

```powershell
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

On this Windows machine, prefer the bundled Codex Python runtime when the bare
`python` launcher resolves to the Windows Store shim.


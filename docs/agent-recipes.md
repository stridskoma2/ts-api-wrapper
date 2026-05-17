# Agent Change Recipes

Use these recipes to keep wrapper changes complete, small, and reviewable.
Preserve behavior unless the task explicitly asks for a behavior change.

## Add Or Change A REST Endpoint

1. Confirm the endpoint, method, path, request schema, response schema, scopes,
   and media types in `specs/tradestation/openapi.2026-05-09.json`.
2. Add or update typed models in `src/tradestation_api_wrapper/models.py` only
   when an existing model cannot accurately represent the API shape.
3. Add the low-level request in `src/tradestation_api_wrapper/rest.py`.
4. Add or update the high-level client method in
   `src/tradestation_api_wrapper/client.py`.
5. Add scope constants or checks in `src/tradestation_api_wrapper/capabilities.py`
   when the spec requires a scope.
6. Add focused tests in the matching `tests/unit/test_*` file.
7. Update `README.md` when the public wrapper surface changes.

Keep request/response detail visible to callers. Avoid hiding protocol data
behind workflow abstractions.

## Add Or Change A Stream

1. Confirm stream path, required scopes, query parameters, and `Accept` header in
   the pinned spec.
2. Preserve the current media-type split:
   - market-data streams:
     `application/vnd.tradestation.streams.v2+json`
   - brokerage order/position streams:
     `application/vnd.tradestation.streams.v3+json`
3. Add typed stream parameter models in `models.py` when the parameters are
   stable API surface.
4. Route parsing through `src/tradestation_api_wrapper/stream.py`.
5. Preserve `raise_on_error=False` behavior for callers that need per-symbol
   stream errors as events.
6. Test chunk parsing, error events, reconnect boundaries, and parameter
   serialization.

## Change Order Writes

1. Start from `docs/safety-invariants.md`.
2. Preserve `TradeStationTrade` as the write-result handle.
3. Preserve `TradeStationTrade.reconcile_required` for unknown or ambiguous
   order state.
4. Do not blindly retry non-idempotent writes after timeouts, 5xx responses,
   408 responses, transport drops, invalid JSON success bodies, or missing
   broker IDs.
5. Keep account-scoped preflight behavior for replace and cancel.
6. Add regression tests for both success and ambiguous/error branches.
7. Update migration notes when public method signatures change.

## Change Validation

1. Keep validation at the wrapper boundary when the data needed is present in
   request/config.
2. Leave stateful portfolio/session controls to callers unless the caller
   explicitly supplies the needed state.
3. Prefer enums and typed models over stringly-typed flags.
4. Reject explicit `AssetClass.UNKNOWN` for writes.
5. Preserve direct-caller compatibility where it does not weaken safety.
6. Add table-driven tests for accepted and rejected inputs.

## Change Auth Or Scopes

1. Update scope definitions in `capabilities.py`.
2. Add or update preflight checks before network calls.
3. Keep auth/token behavior transport-agnostic.
4. Keep persisted token writes lock-safe and codec-driven.
5. Add tests for missing scope, valid scope, and production token-store safety.

## Change Retry, Pagination, Or Transport Behavior

1. Keep safe reads retryable with bounded backoff.
2. Keep write ambiguity explicit instead of retrying through uncertainty.
3. Preserve `Retry-After` handling where available.
4. Keep pagination bounded and raise `PaginationError` when the API cannot make
   progress.
5. Keep optional transports optional; `httpx` support must not become required
   for dependency-free users.
6. Test boundary cases with fake transports rather than live network calls.

## Public Surface Checklist

Before finishing a public API change, confirm:

- The pinned spec supports the new or changed surface.
- The wrapper boundary is still REST/stream focused.
- README examples and migration notes match the signature.
- Tests cover the new behavior and the failure branch most likely to hurt live
  callers.
- Verification commands are reported with outcomes.


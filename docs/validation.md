# Validation Strategy

Use the lightest command that covers the changed surface. Escalate when the
change crosses module boundaries, touches live-trading safety, or changes public
API.

## Local Commands

```powershell
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

Install test tools when needed:

```powershell
python -m pip install -e .[test]
```

On this Windows machine, prefer the bundled Codex Python runtime if bare
`python` resolves to the Windows Store shim.

## Decision Matrix

| Change type | Minimum validation | Escalate when |
| --- | --- | --- |
| Docs only | Read the changed docs for broken links and stale commands | Public API examples changed |
| README example only | `python -m unittest discover -s tests` when examples touch public signatures | Example changes order writes, auth, or streams |
| One pure model/helper | Matching `tests/unit/test_*` module | Public model export changes |
| Validation behavior | `tests/unit/test_models_and_validation.py` | Order writes or config behavior also changed |
| Order write behavior | `tests/unit/test_trade.py` and `tests/unit/test_rest_retries.py` | Public client signatures changed |
| REST client endpoint | Matching client/rest tests plus `tests/unit/test_spec_coverage.py` | New endpoint family or scope behavior |
| Stream parser/session | `tests/unit/test_stream_parser.py` and `tests/unit/test_stream_session.py` | Client stream helper or media type changed |
| Auth/token storage | `tests/unit/test_auth.py` and `tests/unit/test_config.py` | Token persistence or OAuth flow changed |
| Transport/retry/rate limit | `tests/unit/test_httpx_transport.py`, `tests/unit/test_rest_retries.py`, or `tests/unit/test_rate_limit.py` as applicable | Write ambiguity or retry semantics changed |
| Public API or cross-module change | `python -m unittest discover -s tests` | Release candidate or migration note change |
| Type or lint-sensitive change | `python -m ruff check .` and `python -m mypy src tests` | Any public model/client signature changed |

## Full Verification

Run full local verification before claiming broad safety:

```powershell
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

SIM integration tests are environment-gated. Do not claim live/SIM broker
coverage unless the required TradeStation SIM environment variables were present
and the relevant integration tests actually ran. SIM order-placement tests also
require `TRADESTATION_SIM_TRADE_TESTS=1`.

## Reporting

Every implementation closeout should include:

- the exact commands run
- pass/fail/skip outcome
- any environment issue separated from product failure
- the clean-code rule IDs enforced
- a before and after rule snapshot


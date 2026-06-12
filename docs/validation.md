# Validation Strategy

Run the full test suite after every code change before claiming the work is done.
This is the reliable signal; do not infer safety from diff size. Even a one-line
edit to configuration, a constant, or a single helper can have non-local blast
radius, so a scoped subset can pass while the change is actually broken.

Pure documentation or comment edits that change no code or behavior are the only
exception.

## Local Commands

One command runs the entire gate (tests, lint, strict type check):

```powershell
./check.ps1
```

The individual steps, when you need just one:

```powershell
python -m unittest discover -s tests
python -m ruff check .
python -m mypy src tests
```

Optional coverage report (the `coverage` tool ships with the `test` extra):

```powershell
python -m coverage run -m unittest discover -s tests
python -m coverage report
```

Install test tools when needed:

```powershell
python -m pip install -e .[test]
```

On this Windows machine, prefer the bundled Codex Python runtime if bare
`python` resolves to the Windows Store shim.

## Intermediate Feedback (Optional, Not A Substitute)

While iterating you may run a narrower command for faster feedback between edits.
This is a development convenience only; **always finish with the full suite before
claiming the change is done.**

| Change type | Fast intermediate check |
| --- | --- |
| One pure model/helper | Matching `tests/unit/test_*` module |
| Validation behavior | `tests/unit/test_models_and_validation.py` |
| Order write behavior | `tests/unit/test_trade.py` and `tests/unit/test_rest_retries.py` |
| REST client endpoint | Matching client/rest tests plus `tests/unit/test_spec_coverage.py` |
| Stream parser/session | `tests/unit/test_stream_parser.py` and `tests/unit/test_stream_session.py` |
| Auth/token storage | `tests/unit/test_auth.py` and `tests/unit/test_config.py` |
| Transport/retry/rate limit | `tests/unit/test_httpx_transport.py`, `tests/unit/test_rest_retries.py`, or `tests/unit/test_rate_limit.py` |
| Type or lint-sensitive change | `python -m ruff check .` and `python -m mypy src tests` |

## Full Verification

Run full local verification before claiming any change is complete:

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

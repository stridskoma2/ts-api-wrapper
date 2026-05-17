from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

SPEC_LOCK_PATH = Path("specs/tradestation/openapi.lock")


def pinned_spec_path(lock_path: Path = SPEC_LOCK_PATH) -> Path:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    pinned_file = lock.get("pinned_file")
    if not isinstance(pinned_file, str):
        raise AssertionError("OpenAPI lock is missing pinned_file")
    spec_path = lock_path.parent / pinned_file
    if not spec_path.exists():
        raise AssertionError(f"pinned OpenAPI file does not exist: {spec_path}")
    return spec_path


SPEC_PATH = pinned_spec_path()

WRAPPED_ENDPOINTS = {
    "/v3/brokerage/accounts",
    "/v3/brokerage/accounts/{accounts}/balances",
    "/v3/brokerage/accounts/{accounts}/bodbalances",
    "/v3/brokerage/accounts/{accounts}/historicalorders",
    "/v3/brokerage/accounts/{accounts}/historicalorders/{orderIds}",
    "/v3/brokerage/accounts/{accounts}/orders",
    "/v3/brokerage/accounts/{accounts}/orders/{orderIds}",
    "/v3/brokerage/accounts/{accounts}/positions",
    "/v3/brokerage/stream/accounts/{accounts}/orders",
    "/v3/brokerage/stream/accounts/{accounts}/orders/{ordersIds}",
    "/v3/brokerage/stream/accounts/{accounts}/positions",
    "/v3/marketdata/barcharts/{symbol}",
    "/v3/marketdata/options/expirations/{underlying}",
    "/v3/marketdata/options/riskreward",
    "/v3/marketdata/options/spreadtypes",
    "/v3/marketdata/options/strikes/{underlying}",
    "/v3/marketdata/quotes/{symbols}",
    "/v3/marketdata/stream/barcharts/{symbol}",
    "/v3/marketdata/stream/marketdepth/aggregates/{symbol}",
    "/v3/marketdata/stream/marketdepth/quotes/{symbol}",
    "/v3/marketdata/stream/options/chains/{underlying}",
    "/v3/marketdata/stream/options/quotes",
    "/v3/marketdata/stream/quotes/{symbols}",
    "/v3/marketdata/symbollists/cryptopairs/symbolnames",
    "/v3/marketdata/symbols/{symbols}",
    "/v3/orderexecution/activationtriggers",
    "/v3/orderexecution/orderconfirm",
    "/v3/orderexecution/ordergroupconfirm",
    "/v3/orderexecution/ordergroups",
    "/v3/orderexecution/orders",
    "/v3/orderexecution/orders/{orderID}",
    "/v3/orderexecution/routes",
}

EXPLICITLY_SKIPPED_ENDPOINTS: dict[str, str] = {}


class SpecCoverageTests(unittest.TestCase):
    def test_pinned_spec_path_reads_openapi_lock(self) -> None:
        lock = json.loads(SPEC_LOCK_PATH.read_text(encoding="utf-8"))

        self.assertEqual(SPEC_PATH.name, lock["pinned_file"])

    def test_pinned_spec_path_reports_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "openapi.lock"
            lock_path.write_text('{"pinned_file": "missing.json"}', encoding="utf-8")

            with self.assertRaisesRegex(AssertionError, "pinned OpenAPI file does not exist"):
                pinned_spec_path(lock_path)

    def test_openapi_paths_are_wrapped_or_explicitly_skipped(self) -> None:
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        spec_paths = set(spec["paths"])

        missing = spec_paths - WRAPPED_ENDPOINTS - set(EXPLICITLY_SKIPPED_ENDPOINTS)

        self.assertFalse(missing, sorted(missing))


if __name__ == "__main__":
    unittest.main()

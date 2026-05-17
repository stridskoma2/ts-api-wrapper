from __future__ import annotations

import json
import unittest
from pathlib import Path

SPEC_PATH = Path("specs/tradestation/openapi.2026-05-09.json")

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
    def test_openapi_paths_are_wrapped_or_explicitly_skipped(self) -> None:
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        spec_paths = set(spec["paths"])

        missing = spec_paths - WRAPPED_ENDPOINTS - set(EXPLICITLY_SKIPPED_ENDPOINTS)

        self.assertFalse(missing, sorted(missing))


if __name__ == "__main__":
    unittest.main()

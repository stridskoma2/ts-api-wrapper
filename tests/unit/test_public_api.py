from __future__ import annotations

import unittest

import tradestation_api_wrapper as package

# Callers must be able to catch wrapper errors and run reconciliation without
# importing private-looking submodules.
REQUIRED_ROOT_EXPORTS = (
    "AccessTokenProvider",
    "AmbiguousOrderState",
    "AuthenticationError",
    "CapabilityError",
    "ConfigurationError",
    "NetworkTimeout",
    "PaginationError",
    "RateLimitError",
    "ReconciliationOutcome",
    "ReconciliationResult",
    "RequestValidationError",
    "RetryExhausted",
    "RetryPolicy",
    "StaticTokenProvider",
    "StreamError",
    "StreamParseError",
    "StreamReconnectPolicy",
    "TokenCodec",
    "TokenStore",
    "TradeStationAPIError",
    "TradeStationWrapperError",
    "TransportError",
    "UnknownOrderFingerprint",
    "match_unknown_order",
)


class PublicApiTests(unittest.TestCase):
    def test_safety_relevant_names_are_exported_at_package_root(self) -> None:
        for name in REQUIRED_ROOT_EXPORTS:
            with self.subTest(export=name):
                self.assertTrue(hasattr(package, name))
                self.assertIn(name, package.__all__)

    def test_all_entries_resolve_to_attributes(self) -> None:
        for name in package.__all__:
            with self.subTest(export=name):
                self.assertTrue(hasattr(package, name))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from tradestation_api_wrapper.models import (
    OrderLegSnapshot,
    OrderSnapshot,
    OrderType,
    TradeAction,
    UnknownOrderFingerprint,
)
from tradestation_api_wrapper.reconciliation import ReconciliationOutcome, match_unknown_order


def fingerprint() -> UnknownOrderFingerprint:
    return UnknownOrderFingerprint(
        account_id="123456789",
        symbol="MSFT",
        trade_action=TradeAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        limit_price=Decimal("10.25"),
        submitted_at=datetime(2026, 5, 9, 1, 0, tzinfo=UTC),
        payload_hash="hash",
    )


def snapshot(order_id: str) -> OrderSnapshot:
    return OrderSnapshot.model_validate(
        {
            "AccountID": "123456789",
            "OrderID": order_id,
            "OrderType": "Limit",
            "LimitPrice": "10.25",
            "OpenedDateTime": "2026-05-09T01:01:00Z",
            "Legs": [
                {
                    "Symbol": "MSFT",
                    "BuyOrSell": "Buy",
                    "QuantityOrdered": "2",
                }
            ],
        }
    )


class ReconciliationTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        result = match_unknown_order(fingerprint(), (snapshot("1"),))

        self.assertEqual(result.outcome, ReconciliationOutcome.EXACT_MATCH)
        self.assertEqual(result.order.order_id, "1")  # type: ignore[union-attr]

    def test_multiple_matches_stay_ambiguous(self) -> None:
        result = match_unknown_order(fingerprint(), (snapshot("1"), snapshot("2")))

        self.assertEqual(result.outcome, ReconciliationOutcome.MULTIPLE_MATCHES)

    def test_no_match(self) -> None:
        wrong = snapshot("1").model_copy(update={"limit_price": Decimal("11")})

        result = match_unknown_order(fingerprint(), (wrong,))

        self.assertEqual(result.outcome, ReconciliationOutcome.NO_MATCH)


if __name__ == "__main__":
    unittest.main()


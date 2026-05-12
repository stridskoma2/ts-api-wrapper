from __future__ import annotations

import unittest
from decimal import Decimal

from tests.unit.test_models_and_validation import limit_order
from tradestation_api_wrapper.errors import AmbiguousOrderState, NetworkTimeout
from tradestation_api_wrapper.models import OrderAck, OrderSnapshot
from tradestation_api_wrapper.trade import TradeStationTrade
from tradestation_api_wrapper.validation import canonical_payload_hash, order_payload


class TradeTests(unittest.TestCase):
    def test_trade_exposes_ack_order_id_and_payload_hash(self) -> None:
        request = limit_order()
        payload = order_payload(request)
        trade = TradeStationTrade(
            request=request,
            payload=payload,
            payload_hash=canonical_payload_hash(payload),
            ack=OrderAck.model_validate({"Orders": [{"OrderID": "abc"}]}),
        )

        self.assertEqual(trade.order_id, "abc")
        self.assertFalse(trade.reconcile_required)

    def test_trade_requires_reconcile_when_ack_has_no_order_id(self) -> None:
        request = limit_order()
        payload = order_payload(request)
        trade = TradeStationTrade(
            request=request,
            payload=payload,
            payload_hash=canonical_payload_hash(payload),
            ack=OrderAck.model_validate({"Message": "accepted"}),
        )

        self.assertTrue(trade.reconcile_required)

    def test_trade_can_be_updated_with_snapshot_and_events(self) -> None:
        request = limit_order()
        payload = order_payload(request)
        trade = TradeStationTrade(
            request=request,
            payload=payload,
            payload_hash=canonical_payload_hash(payload),
        ).with_order_snapshot(OrderSnapshot.model_validate({"OrderID": "1", "Status": "FLL"}))

        updated = trade.with_event({"OrderID": "1", "Status": "FLL"})

        self.assertTrue(updated.is_done)
        self.assertEqual(updated.order_id, "1")
        self.assertEqual(len(updated.events), 1)

    def test_ambiguous_trade_is_explicit(self) -> None:
        request = limit_order()
        payload = order_payload(request)
        trade = TradeStationTrade(
            request=request,
            payload=payload,
            payload_hash=canonical_payload_hash(payload),
            ambiguous_error=AmbiguousOrderState("submit order", "request", NetworkTimeout("timeout")),
        )

        self.assertTrue(trade.is_ambiguous)
        self.assertTrue(trade.reconcile_required)


if __name__ == "__main__":
    unittest.main()


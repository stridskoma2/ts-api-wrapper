from __future__ import annotations

import unittest

from tradestation_api_wrapper.models import OrderSnapshot
from tradestation_api_wrapper.order_status import TradeStationOrderStatus, normalize_order_status


class OrderStatusTests(unittest.TestCase):
    def test_normalizes_known_and_unknown_status(self) -> None:
        self.assertEqual(normalize_order_status("opn"), TradeStationOrderStatus.SENT)
        self.assertEqual(normalize_order_status("new-code"), TradeStationOrderStatus.UNKNOWN)
        self.assertIsNone(normalize_order_status(""))

    def test_order_snapshot_exposes_status_helpers(self) -> None:
        working = OrderSnapshot.model_validate({"OrderID": "1", "Status": "OPN"})
        done = OrderSnapshot.model_validate({"OrderID": "2", "Status": "FLL"})
        replaced = OrderSnapshot.model_validate({"OrderID": "3", "Status": "UCH"})
        too_late_to_cancel = OrderSnapshot.model_validate({"OrderID": "4", "Status": "LAT"})

        self.assertTrue(working.is_active)
        self.assertTrue(working.is_working)
        self.assertTrue(working.can_cancel)
        self.assertTrue(working.can_replace)
        self.assertTrue(done.is_done)
        self.assertFalse(done.can_cancel)
        self.assertTrue(replaced.is_done)
        self.assertFalse(replaced.is_active)
        self.assertTrue(too_late_to_cancel.is_active)
        self.assertFalse(too_late_to_cancel.can_cancel)
        self.assertFalse(too_late_to_cancel.can_replace)


if __name__ == "__main__":
    unittest.main()

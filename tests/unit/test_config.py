from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from tests.helpers import sim_config
from tradestation_api_wrapper.config import (
    LIVE_ACKNOWLEDGEMENT,
    LIVE_BASE_URL,
    SIM_BASE_URL,
    Environment,
    TradeStationConfig,
)
from tradestation_api_wrapper.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_sim_requires_sim_base_url(self) -> None:
        with self.assertRaises(ValidationError):
            sim_config(base_url=LIVE_BASE_URL)

    def test_live_requires_live_url_enabled_flag_and_acknowledgement(self) -> None:
        with self.assertRaises(ValidationError):
            TradeStationConfig(
                environment=Environment.LIVE,
                base_url=LIVE_BASE_URL,
                client_id="client",
                requested_scopes=("openid", "offline_access"),
                account_allowlist=("live-account",),
            )

        config = TradeStationConfig(
            environment=Environment.LIVE,
            base_url=LIVE_BASE_URL,
            client_id="client",
            requested_scopes=("openid", "offline_access", "Trade"),
            account_allowlist=("live-account",),
            live_trading_enabled=True,
            live_acknowledgement=LIVE_ACKNOWLEDGEMENT,
        )
        self.assertEqual(config.base_url, LIVE_BASE_URL)

    def test_rejects_duplicate_accounts_and_unresolved_placeholders(self) -> None:
        with self.assertRaises(ValidationError):
            sim_config(account_allowlist=("123", "123"))
        with self.assertRaises(ValidationError):
            sim_config(client_id="${TRADESTATION_CLIENT_ID}")
        with self.assertRaises(ValidationError):
            sim_config(client_secret="${TRADESTATION_CLIENT_SECRET}")

    def test_kill_switch_blocks_order_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kill_switch = Path(temp_dir) / "kill"
            kill_switch.write_text("stop", encoding="utf-8")
            config = sim_config(kill_switch_file=kill_switch)
            with self.assertRaises(ConfigurationError):
                config.assert_can_submit_orders("123456789")

    def test_account_allowlist_is_enforced(self) -> None:
        config = sim_config()
        with self.assertRaises(ConfigurationError):
            config.assert_account_allowed("not-allowed")

    def test_trading_flags_require_trade_scope(self) -> None:
        with self.assertRaises(ValidationError):
            sim_config(
                requested_scopes=("openid", "offline_access", "MarketData", "ReadAccount"),
                allow_market_orders=True,
            )


if __name__ == "__main__":
    unittest.main()

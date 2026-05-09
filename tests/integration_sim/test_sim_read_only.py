from __future__ import annotations

import os
import unittest

from tests.helpers import sim_config
from tradestation_api_wrapper.client import TradeStationClient
from tradestation_api_wrapper.rest import StaticTokenProvider


def has_sim_access_token() -> bool:
    return bool(os.getenv("TRADESTATION_SIM_ACCESS_TOKEN") and os.getenv("TRADESTATION_SIM_ACCOUNT_ID"))


@unittest.skipUnless(has_sim_access_token(), "TradeStation SIM access token/account env vars absent")
class SimReadOnlyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_accounts_and_positions(self) -> None:
        account_id = os.environ["TRADESTATION_SIM_ACCOUNT_ID"]
        client = TradeStationClient(
            sim_config(account_allowlist=(account_id,)),
            StaticTokenProvider(os.environ["TRADESTATION_SIM_ACCESS_TOKEN"]),
        )

        accounts = await client.get_accounts()
        positions = await client.get_positions((account_id,))

        self.assertTrue(any(account.account_id == account_id for account in accounts))
        self.assertIsInstance(positions, tuple)


if __name__ == "__main__":
    unittest.main()

